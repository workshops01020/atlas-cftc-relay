# Free Atlas CFTC relay

This folder is a template for a separate public, data-only GitHub repository.
It contains no Atlas Trader source code and no FRED key.

## One-time setup

1. Create a new **public** GitHub repository, for example `atlas-cftc-relay`.
2. Copy `publish_cftc_feed.py` and `.github/workflows/update-feed.yml` into it.
3. Enable Actions and run **Update Atlas CFTC feed** once manually.
4. Optional: create an Actions secret named `CFTC_RELAY_HMAC_KEY`. A long random
   value adds a shared-secret check; leave it empty if you only want HTTPS,
   schema, expiry and payload-hash verification.
5. The feed URL is:

   `https://raw.githubusercontent.com/YOUR_ACCOUNT/atlas-cftc-relay/main/latest.json`

GitHub Actions is free for public repositories. Its schedule may run a few
minutes late; this is acceptable because CFTC reports are weekly and the feed
expires safely if it becomes too old.

## Atlas setup

On the VPS, set the relay URL once when Atlas asks for it, or set:

```text
ATLAS_CFTC_RELAY_URL=https://raw.githubusercontent.com/YOUR_ACCOUNT/atlas-cftc-relay/main/latest.json
```

If you used the optional HMAC secret, also set
`ATLAS_CFTC_RELAY_HMAC_KEY` to the same value on the VPS. The FRED key remains
separate and is never written to this repository.

The relay is only a transport cache. Atlas still validates every CFTC record,
release date, overlap anchor, freshness limit and account safety condition before
the bridge can trade.
