# CoastGuard observations with MyShipTracking

This is a private, six-hour sampling pipeline for an explicitly reviewed China
Coast Guard watchlist. It does not replace the existing AIS feed, track Taiwan
CGA, identify every CCG vessel, or reconstruct a continuous route from sparse
samples. No new commercial positions are committed or deployed to Pages.

## Before enabling

1. Store the API key as repository **Secret** `MyshipTracking`, not a Variable.
   The position API only needs the key; the provider's additional Secret is not
   needed or injected. Rotate credentials previously shared in chat.
2. Apply `sql/coastguard_private.sql` in Supabase's SQL Editor as an administrator.
   It creates four separate RLS-protected `mst_ccg_*` tables and service-role-only
   RPCs. Never grant anon/authenticated access or reuse public `vessel_routes`.
   Existing `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` Secrets are used by the job.
3. Verify non-service roles cannot select these tables or execute the RPCs.
   The SQL integration CI tests this against a disposable database, not your
   production Supabase. No production migration is automatically applied.
4. Set repository Variable `MST_STORAGE_READY=true` only after that verification.
   Set `MST_TRACKING_ENABLED=true` to enable heartbeats and live-mode queries.
   Both are deliberately off when unset. Setting only the API Secret does not
   start billing.

The current [provider terms](https://api.myshiptracking.com/docs/terms), section 4,
restrict data and derivatives to internal use and prohibit public redistribution.
Obtain separate permission before adding this feed to a public dashboard. A
Supabase service key is not permission to publish data.

## Schedule and manual checks

The existing `pipeline-heartbeat.yml` checks whether
`track-coastguard.yml` has run in the last 360 minutes and dispatches it with
`dry_run=false` when enabled. Both use the existing data-pipeline queue rules;
there is no new Pages deploy job or competing cron. GitHub delays and other queued
jobs mean this is approximately every six hours, not an exact-time SLA.

The tracking workflow's **Run workflow** input defaults to `dry_run=true`.
Dry runs make no network calls and need no credentials. For a real run, uncheck
that input after completing the prerequisites above. A dry run also appears in
the heartbeat's run history and can postpone its next dispatch; the private
database remains the authoritative guard against paid calls less than six hours
apart. Do not use repeated manual runs to bypass the cadence.

Local offline check:

```sh
python src/fetch_coastguard_myshiptracking.py --dry-run
python -m pytest tests/test_coastguard_myshiptracking.py tests/test_pipeline_heartbeat.py -q
```

Live mode is explicit (`--live`); the program also requires the two enablement
Variables, valid credentials, and a successful atomic private-storage claim.
`--limit 1` is available for a controlled one-vessel smoke check; it still consumes
the normal six-hour slot. No live request is part of automated tests.

## Watchlist and credits

`config/coastguard_watchlist.json` contains 14 seed MMSIs derived from existing
`KNOWN_GOV_MMSI` and the 2026-11-27 historical `gov_vessels_recent` roster. They are
AIS identity evidence, not verified official ownership. Extend the list only after
reviewing historical names/tracks and evidence; never classify an entire MMSI
prefix as CCG. Numeric aliases retain known identities; Taiwan `HAI JING` (海景)
must not be automatically included. Other government categories stay separate.

The client calls `/api/v2/vessel/bulk` with `response=simple&src=ter`. Its maximum is
100 identifiers per batch and the cost is **per returned vessel**, not per HTTP
call. The implementation intentionally requires the entire active list to fit one
budgeted batch; it fails closed rather than silently rotating targets or truncating
the list. Four complete 14-vessel samples cost up to 56 credits/day, about 1,680
credits/30 days, excluding retries, other users of the account, or future modules.

Optional repository Variables (defaults):

| Variable | Default |
|---|---:|
| `MST_MAX_CREDITS_PER_RUN` | 25 |
| `MST_DAILY_CREDIT_LIMIT` | 100 |
| `MST_MIN_BALANCE_RESERVE` | 100 |

Free account-info requests check expiry and the remaining balance. The database
reserves the entire potential batch cost before any paid request, using database
time, a singleton row lock, and a unique GitHub run ID. Reruns cannot reset that
reservation. Unfinished requests and missing billing headers retain the full
reservation. HTTP 401/402/403/5xx and network errors are not replayed; only 429
with no positive charge gets up to two bounded Retry-After retries. Account and
transport errors do not print provider messages, credentials, or positions.

## Internal results and quality

- `mst_ccg_runs.result` is the private per-run JSON: normalized observations,
  individual query states, raw response and known/unknown charges.
- `mst_ccg_positions.point` holds the private per-position JSON. The key is
  `(source, mmsi, received)`; later timestamps at the same coordinates remain valid
  stationary observations. Same-time conflicts remain inspectable in raw runs.
- `mst_ccg_latest.point` retains the newest valid observation, never overwritten
  by older or missing replies. No missing vessel is deleted from the watchlist.
- `mst_ccg_state` holds attempt, successful-response and fresh-observation times.
  An eight-hour freshness lapse produces a generic workflow warning on the next
  claimed run; complete GitHub scheduling outages still require external uptime
  monitoring for prompt notification.

`received` is the provider's position timestamp, not the fetch time. Each run
records `fetched_at` separately. States distinguish returned/invalid/not-returned
from fresh (<=1h), delayed (1-6h) and stale (>6h). Invalid/masked coordinates and
timestamps more than five minutes in the future are quarantined. AIS type groups
are preserved as `provider_vtype`, not mistaken for standard AIS ship-type codes.
Coverage is terrestrial: a missing observation is **not** evidence of switched-off
AIS, vessel absence or illegal activity. Historical `/vessel/track` purchases and
automatic paid name searches are not enabled by this implementation.

Successful finish calls prune observations and run records older than 90 days,
while retaining the latest position and identity list. Review retention against
your contract. Private JSON can be exported for internal analysis from these
tables; do not use public workflow artifacts, Pages, public route tables or the
public `ais-archive` branch as storage.

Disable `MST_TRACKING_ENABLED` to stop both dispatch and paid calls without
affecting other pipelines. GitHub integration cannot read Secret values; actual
Secret names, production schema installation and CCG coverage require operator
verification before activation.

API references: [bulk positions](https://api.myshiptracking.com/docs/vessels-current-position-api),
[account info](https://api.myshiptracking.com/docs/account-info).
