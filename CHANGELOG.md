# 1.4.0 — Linux reliability, store compatibility, and notifications

Store parsing is selectively ported from jamesccupps/UnifiStockWatcher commit
`3515b84d1d96b31382efbc8019d37af4c1b55397` (store implementation v3.2.7), MIT.
No upstream Windows GUI, PowerShell, installer, updater, or signing code is bundled.

## Fixes and features

- Current camera/physical-security category, dynamic category discovery per region,
  Next.js redirects/build rotation, renamed products and alternate product shapes.
- Defensive variants, titles and prices; no fabricated out-of-stock result for a
  missing product. Unpriced products no longer appear free.
- Persistent ETag cache with a forced full refresh after ten minutes. Daily category
  discovery and bounded parallel category reads. Cache files contain public store
  data only and are created on the server, never shipped in the source ZIP.
- Persistent store backoff up to thirty minutes; no product fallback storm when
  categories report rate limiting. Missing/delisted products are periodically retried.
- Shared Linux file lock for GUI/service state transactions and a region revision
  marker; checks from a previous region cannot repopulate the new baseline.
- Restock estimates, time sold out, coming-soon labels, and price-change history.
- CSV formula escaping, UTF-8 BOM input handling, stricter slug checks.
- Optional ntfy Bearer token, protected-topic guide, explicit token removal,
  redacted errors, HTTP status checking, and bounded retries for network/429/5xx
  failures (Retry-After capped at 15 seconds). Tokens are never sent back in API
  settings responses. The no-login Tailscale/localhost policy is unchanged.
- Unchanged checks update compact state/counters instead of rewriting large history;
  change events are written immediately. History search is debounced.

## Install / upgrade

Run `sudo bash install.sh` from this source tree. Existing settings and watch data
are backed up and preserved. No new ntfy token is required for public topics.
Diagnostics reports 1.4.0. Do not upload your live server configuration or backups.

## Validation

Offline backend regression tests and frontend integration tests use mocked network
responses; they do not prove the live store contract or real phone delivery.
The installer still targets Ubuntu/Debian with systemd. Test on your deployment
before replacing a working installation; a fresh privileged VM provisioning test
is not included in the automated test suite.
