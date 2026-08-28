#!/usr/bin/env python3
"""Build the small public CFTC feed consumed by Atlas Trader.

This file is intended for a separate *data-only* public repository. It never
contains Atlas source code or a FRED key. GitHub Actions can run it for free in
that repository and commit the resulting ``latest.json`` file.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request
import zipfile


UTC = timezone.utc
CFTC_ENDPOINT = "https://publicreporting.cftc.gov/resource/jun7-fc8e.json"
CFTC_ARCHIVE_TEMPLATE = "https://www.cftc.gov/files/dea/history/deahistfo{year}.zip"
MARKET_CODE = "088691"
MARKET_NAMES = {"GOLD - COMMODITY EXCHANGE INC.", "GOLD - CEI GOLD"}
PURPOSE = "ATLAS_CFTC_OFFICIAL_RELAY_V1"
MAX_BYTES = 32 * 1024 * 1024
REQUIRED_ARCHIVE_FIELDS = {
    "Market and Exchange Names",
    "As of Date in Form YYYY-MM-DD",
    "CFTC Contract Market Code (Quotes)",
    "Open Interest (All)",
    "Noncommercial Positions-Long (All)",
    "Noncommercial Positions-Short (All)",
    "Commercial Positions-Long (All)",
    "Commercial Positions-Short (All)",
}


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def fetch(url: str, *, accept: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": accept, "User-Agent": "ATLAS-CFTC-Relay/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise RuntimeError("official_response_too_large")
    return payload


def api_rows(year: int) -> list[dict[str, str]]:
    where = (
        "cftc_contract_market_code='088691' AND "
        "futonly_or_combined='Combined' AND "
        f"report_date_as_yyyy_mm_dd>='{year}-01-01T00:00:00'"
    )
    params = {
        "$select": (
            "id,market_and_exchange_names,contract_market_name,"
            "cftc_contract_market_code,report_date_as_yyyy_mm_dd,"
            "noncomm_positions_long_all,noncomm_positions_short_all,"
            "comm_positions_long_all,comm_positions_short_all,"
            "open_interest_all,futonly_or_combined"
        ),
        "$where": where,
        "$order": "report_date_as_yyyy_mm_dd ASC",
        "$limit": "50000",
    }
    raw = fetch(CFTC_ENDPOINT + "?" + urllib.parse.urlencode(params), accept="application/json")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("cftc_api_payload_invalid")
    return [dict(row) for row in payload if isinstance(row, dict)]


def archive_rows(year: int) -> list[dict[str, str]]:
    raw = fetch(CFTC_ARCHIVE_TEMPLATE.format(year=year), accept="application/zip")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = [item for item in archive.infolist() if not item.is_dir()]
        if len(entries) != 1:
            raise RuntimeError("cftc_archive_entry_set_invalid")
        with archive.open(entries[0], "r") as stream:
            text = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")
            reader = csv.DictReader(text)
            if reader.fieldnames is None or not REQUIRED_ARCHIVE_FIELDS <= set(reader.fieldnames):
                raise RuntimeError("cftc_archive_schema_invalid")
            rows: list[dict[str, str]] = []
            for row in reader:
                if (
                    str(row.get("CFTC Contract Market Code (Quotes)", "")).strip() != MARKET_CODE
                    or str(row.get("Market and Exchange Names", "")).strip() not in MARKET_NAMES
                ):
                    continue
                report = str(row["As of Date in Form YYYY-MM-DD"]).strip()
                rows.append(
                    {
                        "id": report[2:4] + report[5:7] + report[8:10] + MARKET_CODE + "C",
                        "market_and_exchange_names": str(row["Market and Exchange Names"]).strip(),
                        "contract_market_name": str(row["Market and Exchange Names"]).strip(),
                        "cftc_contract_market_code": MARKET_CODE,
                        "report_date_as_yyyy_mm_dd": report + "T00:00:00",
                        "noncomm_positions_long_all": str(row["Noncommercial Positions-Long (All)"]).strip(),
                        "noncomm_positions_short_all": str(row["Noncommercial Positions-Short (All)"]).strip(),
                        "comm_positions_long_all": str(row["Commercial Positions-Long (All)"]).strip(),
                        "comm_positions_short_all": str(row["Commercial Positions-Short (All)"]).strip(),
                        "open_interest_all": str(row["Open Interest (All)"]).strip(),
                        "futonly_or_combined": "Combined",
                    }
                )
    return rows


def load_previous(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return int(value.get("sequence", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def main() -> int:
    output = Path(os.environ.get("CFTC_RELAY_OUTPUT", "latest.json")).resolve()
    now = datetime.now(UTC).replace(microsecond=0)
    year = now.year
    try:
        rows = api_rows(year)
        source = "PUBLIC_REPORTING_API"
    except Exception:
        rows = archive_rows(year)
        source = "OFFICIAL_HISTORICAL_COMPRESSED"
    if not rows:
        raise RuntimeError("cftc_gold_rows_missing")
    rows.sort(key=lambda row: str(row.get("report_date_as_yyyy_mm_dd", "")))
    deduped = {str(row["report_date_as_yyyy_mm_dd"]): row for row in rows}
    rows = [deduped[key] for key in sorted(deduped)]
    feed = {
        "schema_version": 1,
        "purpose": PURPOSE,
        "sequence": load_previous(output) + 1,
        "published_at_utc": now.isoformat().replace("+00:00", "Z"),
        "expires_at_utc": (now + timedelta(hours=72)).isoformat().replace("+00:00", "Z"),
        "source_endpoint": CFTC_ENDPOINT,
        "source_mode": source,
        "cftc": rows,
    }
    feed["payload_sha256"] = hashlib.sha256(canonical(rows)).hexdigest()
    relay_key = os.environ.get("CFTC_RELAY_HMAC_KEY", "").strip()
    if relay_key:
        feed["hmac_sha256"] = hmac.new(
            relay_key.encode("utf-8"), canonical(feed), hashlib.sha256
        ).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(feed, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"status": "PASS", "sequence": feed["sequence"], "source_mode": source, "rows": len(rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
