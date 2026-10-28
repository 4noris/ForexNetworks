# Live Dealing & Risk and the historical archive

The broker's configuration entry point is `/risk-center`. It combines source
health, outstanding high-risk event and approval counts, monitoring limits, and
links to live dealing, Operations, rule automation and Analytics. Limit drafts
remain mounted during background updates. Creating/updating a monitoring limit
does not trade, change an MT5 group, or activate an automation rule.

The center supports account/group/symbol limits for gross lots and verified
gross notional; account/group limits also support floating loss, minimum margin
level and lot concentration. Updates use the existing administrator authorization
and audit log. Monetary limits require one verified account currency and complete
source valuation. Stale observations cannot generate fresh limit breaches.
Zero/negative equity is evaluated against used margin rather than being excluded.
Limit evaluation uses all matching rows before display truncation. Empty or
invalid explicit group scope never silently broadens to all groups.

`GET /api/dealing-risk/health` provides administrator-only source freshness,
saved group mismatches, unresolved currency metadata, pending approvals and
historical snapshot status. It reads the published Manager/cache and local
database; it does not initiate an MT5 download or mutate broker configuration.
`PUT /api/operations/limits/{id}` validates, persists and audits limit changes.
The center surfaces mismatched historical configuration for operator review;
it never guesses which book should own a renamed group.

The existing React/Vite UI, FastAPI application, SQLite `users.db`, authentication,
MT5 wrapper, risk detectors, and visual components are retained. There is one
configured Manager. Analytics is `/dealing-risk/analytics`, with navigation from
the sidebar and live toolbar. Administrator access is required for archive APIs,
exports, risk evidence and synchronization controls, matching Operations access.
This application has roles but no per-user group entitlement model. Non-admins
are denied archive access rather than being granted an invented group scope.

## Run and configure

Run `start_production.cmd` after building `frontend` with `npm.cmd run build`.
For development use `start_backend.cmd` and `npm.cmd run dev` in `frontend`.
The API starts the historical worker automatically; no browser starts a sync.
The existing persistent Manager connection is shared, with its SDK lock released
between individual history calls. No extra Manager login/session is introduced.

`services/history_worker.py` owns three independent background loops: historical
sync/reconciliation, observed snapshots, and queued exports. Long initial
backfills do not pause snapshots or export processing. The worker is embedded
in the API service to reuse its sole Manager session. Jobs, checkpoints and
leases are durable in SQLite, so restarting the API resumes incomplete work.

Environment defaults:

| Variable | Default | Purpose |
| --- | --- | --- |
| `HISTORY_WORKER_ENABLED` | follows `MT5_MANAGER_AUTOCONNECT` | Start the archive supervisor; set false to disable all its processing |
| `HISTORY_SYNC_INTERVAL_MINUTES` | 60 | Initial persisted sync interval |
| `HISTORY_SNAPSHOT_SECONDS` | 300 | Initial observed-snapshot interval |
| `HISTORY_RECONCILIATION_HOUR_UTC` | 2 | Initial daily reconciliation hour in UTC |
| `HISTORY_BACKFILL_FROM` | 30 days before initialization | Initial UTC coverage start, `YYYY-MM-DD` |
| `HISTORY_GROUPS` | configured book groups | Semicolon-separated exact MT5 groups |
| `LIVE_RISK_REFRESH_SECONDS` | 5 | Minimum live HTTP/event refresh interval |
| `OPERATIONAL_REFRESH_SECONDS` | 30 | Initial group-catalog browser refresh interval |

Archive settings are seeded only once. Change the interval (30 or 60 minutes),
snapshot cadence, reconciliation hour and backfill start in **Data Synchronization
Status**. The worker reads the saved settings automatically. This does not change
frontend refresh state. Environment changes require restarting the API; live
configuration is polled separately. Keep `HISTORY_GROUPS` explicitly bounded for
large installations. Empty scope never means download every available group.

The initial backfill runs in bounded one-day batches. Each successful batch
commits records, refreshed daily aggregates and its checkpoint in one transaction.
Normal jobs overlap two days. Daily reconciliation overlaps 30 days, finds
late/missing source records, applies corrections and records changed hashes in
`history_discrepancies`. Use an earlier backfill start to reconcile older data.
Three attempts with bounded backoff are permitted per failed batch. A failed
batch never advances its checkpoint; prior data stays queryable.

## Storage and consistency

Migration 1 runs idempotently on API import and is recorded in
`history_migrations`. New `history_*` tables store account metadata, source
deals/orders, precomputed daily account/group/currency/symbol aggregates,
account snapshots, open-position snapshots, symbol specifications, checkpoints,
leases, jobs, export jobs and source corrections. Existing persisted risk events
and resolution workflows are reused. Indexes cover dates, scope and source IDs.
The existing factory reset recreates the archive settings after clearing data.

Monetary values are decimal text. SQLite aggregates use Python `Decimal`,
including averages and numeric ordering, instead of binary `REAL` sums. Source
record hashes and the original normalized SDK fields remain available for audit.
Unknown currencies are separated per account, never silently treated as USD.
The archive binds to a hash of its Manager source. Changing Manager credentials
to another server/login will stop ingestion into that archive to prevent mixing
unrelated source IDs. It does not expose credentials in analytics responses.

Database leases serialize each kind of job across API processes sharing this
local database. Sync leases renew during SDK calls; every batch verifies job
ownership inside its transaction. A dead worker's job can be reclaimed after
180 seconds. Old workers cannot commit batches or finish a new owner's job.
Export and snapshot leases are independent of sync leases.

SQLite WAL is appropriate for this existing single-host deployment. These leases
do not coordinate hosts with separate database copies. A distributed deployment
requires a shared transactional database and worker deployment design; do not
place SQLite on a network filesystem or assume these leases span different files.

## Financial definitions and available history

* Client realized **net trading P&L** sums `Profit + Commission + Storage + Fee`
  from MT5 BUY/SELL deals. Entry-side costs are included exactly once. Balance,
  credit, deposit and withdrawal operations remain in raw activity but are excluded
  from trading P&L. All four components must be available for net to be available.
* Execution volume counts each deal, including entries and exits. It is not
  unique round-trip volume. `VolumeExt` uses 100,000,000 units per lot and the
  legacy `Volume` uses 10,000. Closing executions include OUT, INOUT and OUT_BY;
  they are not synthetic reconstructed position histories.
* Currencies are separate in totals, charts and exports. No FX rate is invented.
  The live page suppresses combined monetary cards for mixed currencies and
  suppresses authoritative cards when account metrics are estimated.
* Daily/weekly/monthly grouping uses UTC. Date ranges include the whole final
  UTC calendar day. Weekly grouping is SQLite UTC calendar year/week (`%Y-%W`),
  with Monday as the first day; it is explicitly not labeled ISO week-year.
* Reported realized drawdown is the largest decrease from a prior peak of
  cumulative daily client net trading P&L, starting at zero. It is not account
  equity drawdown or a cash-flow-adjusted return. The overview compares against
  the immediately preceding equally long UTC period.
* Equity, balance, margin, floating P&L, positions and exposure are actual
  observed snapshots. The source generation timestamp is stored. Stale or
  incomplete Manager generations are not newly captured. Closing deals cannot
  reconstruct these values before archive activation. There is no backfilled
  floating P&L, equity or exposure, and missing samples are not filled in.
* Exposure is per instrument: gross lots = buy + sell; net lots = buy - sell.
  Account-currency notional requires contract size, price, conversion rate and
  an explicitly supported linear calculation mode (forex, CFD, leveraged CFD,
  stocks). Futures, index-specific valuation, options, bonds and collateral
  modes stay unavailable here rather than receiving a guessed valuation.
  If any position is unvalued, the combined notional is unavailable.
* Balance/equity/margin snapshots are account-wide. They cannot be allocated
  to a selected symbol; that limitation is displayed beside the snapshot table.
* Group/currency membership is the metadata available at collection time.
  The installed SDK does not supply a complete historical group-transfer or
  account-currency change ledger. Those histories cannot be reconstructed.
* Source deletions are not inferred from an empty SDK response. Exact deletion
  reconciliation requires source tombstones or a verified complete deletion feed.
  Corrections and late-arriving records inside the overlap window are supported.
* Risk reports read existing persisted detection signals, evidence, severity,
  thresholds and resolution status. Existing Risk Automation and Operations
  remain the controls for detection and resolution. A signal is not proof of fraud.
* The installed SDK methods were inspected directly. Deals use
  `DealRequestByGroup`; orders use `HistoryRequestByGroup` (`HistoryRequest`
  takes a login and must not be passed a group). No requested price, slippage,
  tick evidence or execution-quality measurement is invented. Available actual
  fields are visible in execution evidence.

Coverage and last-success timestamps are displayed separately from report
generation time. A fast database response is not evidence of fresh MT5 data.
The overview limits its chart series to 10,000 currency/day points and disables
drawdown if that series is truncated. Narrow the range/currency when indicated.
Snapshot tables are paginated/capped; trend charts identify that they show the
latest available samples, not every observation in a large date range.

## API and exports

All routes are under `/api/dealing-risk`, using the existing cookie/Bearer auth:

* `/config`: independent browser refresh configuration.
* `/realized`: archived realized totals for explicitly selected live groups.
* `/analytics/catalog`, `/analytics/overview`, `/analytics/pnl`,
  `/analytics/accounts`, `/analytics/exposure`, `/analytics/trading-activity`,
  `/analytics/risk-events`: database-only reports; no connection to MT5 is needed.
* `/sync/status`, `POST /sync/run`, `PUT /sync/settings`: status and admin controls.
* `POST /analytics/exports`, `/analytics/exports/{id}`,
  `/analytics/exports/{id}/download`: queued exports owned by their requesting admin.

Reports validate ranges and filters and cap page size at 1,000. Financial reports
support account/group/symbol/currency filters and daily, weekly, monthly, account,
group or symbol grouping. Numeric sorting is available for net P&L and lots.
Activity includes historical orders, deals and closing executions. Min/max/average
account net P&L and winning/losing account counts use the complete filtered scope.

CSV and Excel exports cover the selected complete dataset, not just the current
page. They include filters, UTC generation time and currency totals where meaningful.
Rows are read in batches from one consistent SQLite read snapshot while ingestion
can continue. Source text is protected against spreadsheet formula execution.
Requests are rate-limited and ownership-checked; completed content is retained
for 24 hours. Export status is polled independently. PDF is not included because
this application does not have a PDF reporting dependency or existing PDF pipeline.

## Refresh and validation

Live updates retain the existing mounted dashboard, menus, dialogs, filters and
scroll. There is one in-flight live request; changing group scope aborts and
ignores outdated responses. Warming responses do not erase a previously published
generation. Existing WebSocket events are throttled to the configurable live
interval and timed polling remains available. Unknown monetary values are not
rendered as zero. The sidebar refresh action invalidates queries and emits a
refresh event instead of reloading the browser.

Analytics uses the existing TanStack Query client, a complete scope-based query
key, abort signals and previous-data retention with an explicit prior-scope label.
Refreshes every 30 seconds replace data in place. Paging, filters, expanded evidence
and navigation are component state. The route joins the existing persistent outlet
so navigating back retains its state. Synchronization never triggers a browser
history download.

Run validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
Set-Location frontend
.\node_modules\.bin\eslint.cmd src/pages/RiskAnalytics.jsx src/pages/DealingRisk.jsx src/layout/MainLayout.jsx src/components/Sidebar.jsx src/App.jsx e2e/risk-analytics.spec.js
npm.cmd run build
$env:E2E_BASE_URL='http://127.0.0.1:8000'
.\node_modules\.bin\playwright.cmd test e2e/risk-analytics.spec.js e2e/tab-retention.spec.js --workers=1
```

The browser tests mock API responses and verify in-place live KPI updates,
mounted-node retention, filters, pagination, open evidence, schedule changes and
the absence of document navigation during refresh. They do not prove the native
SDK's behavior. Some vendor SDK calls may hold Python's GIL; the current wrapper
already notes this limitation. Threaded scheduling removes history calls from
HTTP handlers but cannot guarantee native-library latency isolation. True process
isolation requires a licensed/session-compatible Manager connection service.

## Completion updates

Reliability checks now use a complete, recent Manager generation for empty as
well as populated groups, avoiding redundant native scans. Live refresh retains
the last successful values during an interruption and labels them reconnecting;
an offline API response contains nulls and no fabricated publication timestamp.
Account Analytics defaults to the latest observed generation for its account
snapshot table; history remains available through the overview and date filters.
SDK IPs serialized as numeric strings are normalized before evidence enrichment.
Unresolved historical currencies are repaired only from a verified group currency,
with re-normalized source hashes and rebuilt aggregates. Missing metadata remains
explicit. A disconnected client during Windows transport cleanup is handled
separately from application failures, which still reach the normal error handler.

Live account, exact symbol and risk-category filters are applied before table
row limits. Symbol-filtered floating P&L covers matching positions; account
balance, equity and margin retain their account-wide meaning. Realized history
uses the selected account and symbol; live risk categories cannot be applied
retrospectively and disable that historical card rather than returning a different scope.

Exposure history can be grouped by symbol, account/symbol or group/symbol.
Concentration uses verified notional within each observed generation and currency.
Account snapshots include margin utilization (margin/equity) and margin level
(equity/margin), plus account identifiers. Zero denominators remain unavailable.

Snapshot signals detect configured margin utilization, floating-P&L changes,
and same-instrument increases in gross lots. Settings live in the sync controls;
observations, prior timestamps, actual values and thresholds are persisted in the
existing risk-event tables. These are review signals, not fraud findings.

Each synchronized batch stores source/stored deal counts and decimal net totals
by currency in `history_batch_audits`. Corrections are applied; unmatched source
comparisons remain visible for investigation. `history_coverage` preserves actual
contiguous ranges, preventing a partially interrupted earlier backfill from
claiming coverage across an unfetched gap. Migration 2 seeds known prior ranges.

Exports now carry durable owner/lease fields and cannot publish after another
worker takes ownership. Expiry is enforced at download as well as cleanup.
The existing Exposure view no longer substitutes lots times price for unknown
notional or distributes portfolio changes across symbols: symbol deltas compare
actual observations, and unsupported valuations remain unavailable.

No screenshot accompanied the request. The page reuses existing theme tokens,
cards, tables, chart containers and navigation instead of introducing a new style.
