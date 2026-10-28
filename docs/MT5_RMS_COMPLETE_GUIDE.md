# MT5 RMS ? Complete Tool Manual

Updated: 1 October 2026. This manual consolidates the former documentation set and adds a reference extracted from the current repository. It covers operators, administrators, developers, and deployment engineers. Actual broker availability and permissions must be checked at runtime; source inspection is not live acceptance testing.

## Contents

- [Current product and architecture](#current-product-and-architecture)
- [Command Center and persistent risk events](#command-center-and-persistent-risk-events)
- [User and configuration guide](#user-and-configuration-guide)
- [Broker operations](#broker-operations)
- [Anti-Fraud methodology](#anti-fraud-methodology)
- [Windows deployment](#windows-deployment)
- [Local startup](#local-startup)
- [Google Sheets integration](#google-sheets-integration)
- [Project structure](#project-structure)
- [SDK accuracy and limits](#sdk-accuracy-and-limits)
- [Real-time architecture](#real-time-architecture)
- [API endpoint reference](#api-endpoint-reference)
- [Request model reference](#request-model-reference)
- [Environment reference](#environment-reference)
- [Database schema reference](#database-schema-reference)
- [Verification and maintenance](#verification-and-maintenance)
- [Historical consolidation proposal](#historical-consolidation-proposal)

## Current product and architecture

FastAPI (`api.py`) serves the API and built React/Vite frontend. `mt5_connection.py` owns the single configured persistent MT5 Manager connection and publishes verified in-memory generations. `risk_automation.py` evaluates saved automation rules and executes supported actions. SQLite stores application state; PostgreSQL dependencies do not mean PostgreSQL is the deployed database.

Run one backend process for this architecture. Historical scans, journals, live snapshots, and background jobs share the configured Manager connection. Browser compatibility credential fields do not select another MT5 identity. Never publish `.env`, database files, server journals, or service-account keys as frontend assets.

Most screens read MT5 and write local workflow records. **Risk Automation can modify MT5** through group moves, account/trading disablement, read-only settings, and leverage changes. Simulation does not apply these changes. Internal labels, monitoring, review, symbol restrictions, and lot restrictions are local controls; do not assume local controls enforce trade rejection inside MT5.

Current permission checks distinguish authenticated users and administrators through `is_admin`. The proposed ADMIN/DEALER/RISK_MANAGER/COMPLIANCE/VIEWER role matrix is not implemented as a full role system. Administrative routes enforce permission on the backend.

| Screen / route | Purpose and source |
|---|---|
| Command Center `/command-center` and `/` | Scoped verified snapshot KPIs, exposure, risk accounts, stored risk events, audit changes |
| Dealing and Risk `/dealing-risk` | Live Monitor, P&L Leaders, Analyser, Exposure, Book Suggestions, Anti-Fraud |
| Manager `/manager` | Connection status, account aggregates, summary and exposure |
| Market Watch `/market-watch` | Real symbols, quotes, spreads, metadata, account risk |
| Operations `/operations` | Alerts, cases, related evidence, limits, channels, audit, reconciliation, intelligence and CID |
| Book Shifting `/book-shifting` | Stored group and book movement with source-generation equity |
| Risk Automation `/risk-automation` | Rules, simulation, approvals, live execution, controls and history |
| Book P&L `/bbook-pnl` | Configured book views, historical equity/P&L snapshots and backfill |
| PnL `/deal-report` | Scoped historical closed-deal reporting and export |
| Brokerage `/real-brokerage` | Signed-equity reconciliation, daily snapshots, cash flows and Excel export |
| Controls `/settings` | Manager diagnostics, book presets, Telegram, integration and administrative settings |
| Login `/login`; password `/change-password` | App authentication and required password changes |

Legacy aliases `/brokerage`, `/diff`, `/dashboard`, `/analytics`, and `/equity-report` redirect to current report routes. Sidebar navigation supports theme switching and responsive navigation. Pages load lazily; browser query caches and identical-GET deduplication reduce repeat requests. WebSocket notifications announce source changes; HTTP responses remain the authoritative payload.

P&L Leaders currently requests a **31-day** history window. Daily, Weekly, Monthly, and Overall reuse that result; Overall is not an unlimited lifetime report. After a ready response it refreshes after 60 seconds; warming responses retry after 2 seconds. Refresh intervals differ by screen and can differ from source-generation cadence.

Exposure aggregates live account and position data and combines it with cached realized performance. Book Suggestions uses deterministic account performance, concentration, volume and risk indicators to suggest A-Book, B-Book or Hybrid review. Missing history means incomplete evidence. Suggestions do not move accounts; actual movement belongs to explicitly configured automation or broker operations.

## Command Center and persistent risk events

Select live groups and use Refresh. The header reports status, source timestamp, generation, selected groups, exclusions and age. Automatic refresh runs every 30 seconds while visible and also responds to changed Manager WebSocket markers. In-flight refreshes are cancelled when scope or page changes, and obsolete responses cannot overwrite the new request.

KPIs are total accounts, active accounts (accounts with positions), equity, balance, floating P&L, used/free margin, gross lots, open positions, critical/high-risk accounts and open cases. Open cases are an application-wide count, while financial/account metrics follow selected MT5 groups and exclusions. Unknown or mixed deposit currencies suppress monetary totals rather than adding incompatible currencies.

The symbol table includes BUY/SELL/net lots, open positions, floating P&L, distinct accounts, gross notional, concentration and data status. Notional requires positive MT5 contract size, current price and profit conversion rate. Missing inputs show unavailable values. Concentration requires a complete total notional. Up to 200 symbols and 30 top risk accounts/events are returned.

Top risk accounts link to the Analyser investigation route. Their evidence label reflects stored event quality. What Changed combines recent application audit actions with scoped persistent risk detections, capped at 20 entries; it is not a complete MT5 transaction stream. Global audit actions can appear alongside scoped risk events.

Persistent Anti-Fraud detections use `risk_events`; repeated event IDs update last-seen evidence without discarding workflow status. `risk_event_actions` stores operator status actions. Supported states are NEW, ACKNOWLEDGED, UNDER_REVIEW, ESCALATED, RESOLVED and CLOSED. Event detail exposes evidence and action history; administrators can change status, assignment, factual note and case association. A linked case must belong to the same client. Status changes also write to the audit log. Disappearance from a scan does not automatically close an event.

`antifraud_monitor_scopes` stores requested scan scopes/settings. Once a scope is registered by an Anti-Fraud request, the backend can continue checking it without an open browser tab. `ANTIFRAUD_BACKGROUND_SCAN_ENABLED` controls this worker; its loop checks every 30 seconds and its configured per-scope scan interval has a 60-second minimum. This does not automatically discover every brokerage group. Source scan locks and warming/stale responses still apply.

Durable event storage and Telegram deduplication are distinct: event persistence survives restart; the Telegram digest cache described below remains process-local. Do not assume durable events guarantee exactly-once notification delivery.


## User and configuration guide

Version: 2.0  
Audience: brokerage owners, administrators, dealers, risk managers, compliance analysts, and deployment engineers.

This guide explains every visible section of the product, where its data comes from, how each calculation or detector works, how to configure it, and how to interpret warnings. Reporting screens read MT5 and create local reports and workflow records. Risk Automation can modify MT5 account settings and groups; see the current architecture section before enabling actions.

### Contents

1. [Product map](#1-product-map)
2. [Data model and evidence rules](#2-data-model-and-evidence-rules)
3. [Installation and first-time setup](#3-installation-and-first-time-setup)
4. [Login, profiles, groups, and filters](#4-login-profiles-groups-and-filters)
5. [PnL section](#5-pnl-section)
6. [Brokerage section](#6-brokerage-section)
7. [Dealing and Risk section](#7-dealing-and-risk-section)
8. [Manager section](#8-manager-section)
9. [Market Watch section](#9-market-watch-section)
10. [Operations section](#10-operations-section)
11. [Book Shifting section](#11-book-shifting-section)
12. [Controls section](#12-controls-section)
13. [Trade-IP and VPS setup](#13-trade-ip-and-vps-setup)
14. [Daily operating procedures](#14-daily-operating-procedures)
15. [Scaling and reliability](#15-scaling-and-reliability)
16. [Troubleshooting](#16-troubleshooting)
17. [Security and compliance checklist](#17-security-and-compliance-checklist)
18. [Release acceptance](#18-release-acceptance)

### 1. Product map

| Navigation item | Main purpose | Primary data |
|---|---|---|
| PnL | Closed-deal performance dashboard | MT5 groups, users, deals |
| Brokerage | Equity reconciliation and brokerage P&L | MT5 daily rows, deals, accounts |
| Dealing and Risk | Live exposure, client risk, investigation, anti-fraud, stress testing | MT5 users, accounts, positions, deals, ticks, journals |
| Manager | Live manager book and asset exposure | Persistent Manager API cache |
| Market Watch | Quotes, spreads, contract details, high-margin accounts | MT5 symbols, ticks, accounts |
| Operations | Alerts, cases, related accounts, limits, intelligence, reconciliation, channels, audit | Live cache, MT5 history, local SQLite database |
| Book Shifting | Real-time group/book movement evidence and equity at movement | Consecutive atomic MT5 account snapshots, configured book mappings, local SQLite history |
| Controls | Connection status and Telegram alert thresholds | Runtime configuration and `.env` |

The sidebar connection indicator refers to the persistent system Manager connection. A green indicator means the process has an active Manager API handle. Screens that show a stale warning should not be treated as current even if older values remain visible.

### 2. Data model and evidence rules

#### 2.1 Systems of record

- MT5 is the source of truth for users, groups, balances, equity, margin, positions, orders, deals, daily snapshots, symbols, ticks, and group policies.
- MT5 server journals are the source of truth for ticket-linked client IP evidence where the journal contains both a trade identifier and a public IP.
- `users.db` stores application users, saved profiles, alert acknowledgements, exposure limits, investigation cases, case history, notification channels, reconciliation snapshots, and the dealer audit log.
- Public network intelligence supplies approximate country, city, provider, ASN, hosting, and proxy indicators for public IPs. It is enrichment, not MT5 financial data.

#### 2.2 Evidence labels

| Label | Meaning |
|---|---|
| Verified/live MT5 | Directly read from the current Manager cache or Manager API response |
| Journal proven | A journal record links a public IP to the relevant deal, order, ticket, or position identifier |
| Inferred login IP | MT5 `LastIP` is shown because ticket-linked journal evidence is absent; it is not execution proof |
| Candidate/similarity | A rule found correlated behavior that needs human review |
| Data required | The detector cannot run because the required MT5 field, ledger, or journal is unavailable |
| Stale | The last Manager cache update exceeds `LIVE_CACHE_STALE_SECONDS` or the cache reports an error |

Never use a heuristic signal by itself to suspend a client or make a compliance conclusion. Confirm it against KYC, CRM/device data, payment records, server journals, and dealer review.

### 3. Installation and first-time setup

#### 3.1 Prerequisites

- Windows host capable of running the installed `MT5Manager` Python wrapper.
- A dedicated MT5 Manager credential with read access to required groups, users, accounts, positions, deals, daily rows, symbols, ticks, and group configuration.
- Python dependencies from `requirements.txt`.
- Node.js and npm for frontend development; production uses the built `frontend/dist` files.
- Optional Telegram bot, SMTP server, webhook endpoint, and MT5 server journal access.

#### 3.2 Configure `.env`

Copy `.env.example` to `.env`, then replace placeholders. Do not commit or share `.env`.

```dotenv
MT5_MANAGER_SERVER=broker-server.example.com:443
MT5_MANAGER_LOGIN=1000
MT5_MANAGER_PASSWORD=replace_with_manager_password
MT5_MANAGER_AUTOCONNECT=true

PORT=8000
ALLOWED_ORIGINS=https://console.example.com
LIVE_CACHE_STALE_SECONDS=10

TELEGRAM_BOT_TOKEN=replace_with_bot_token
TELEGRAM_CHAT_ID=replace_with_chat_id
TELEGRAM_ALERTS_ENABLED=true
TELEGRAM_RISK_ALERTS_ENABLED=true
TELEGRAM_ANTIFRAUD_ALERTS_ENABLED=true
ANTIFRAUD_MAX_TICK_CHECKS=3

PROFIT_ALERT_PCT_THRESHOLD=30
DOUBLE_PROFIT_PCT_THRESHOLD=100
HIGH_LOT_ALERT_THRESHOLD=1.0
ACCOUNT_LOT_ALERT_THRESHOLD=5.0
ALERT_DEDUPE_RETENTION_SECONDS=86400

LOW_DEPOSIT_ALERT_THRESHOLD=500
LOW_DEPOSIT_HIGH_PROFIT_MIN=200
LOW_DEPOSIT_PROFIT_MULTIPLE=1.5

MT5_SERVER_LOG_DIRS=C:\\MT5Server\\Logs;D:\\MT5Archive\\Logs
IP_GEOLOCATION_ENABLED=true
```

#### 3.3 Configuration reference

| Variable | Purpose | Default/notes |
|---|---|---|
| `MT5_MANAGER_SERVER` | MT5 server host and port | Required |
| `MT5_MANAGER_LOGIN` | Numeric Manager login | Required |
| `MT5_MANAGER_PASSWORD` | Manager password | Required |
| `MT5_MANAGER_AUTOCONNECT` | Starts persistent connection supervisor | `true` |
| `PORT` | Backend HTTP port | `8000` |
| `ALLOWED_ORIGINS` | Comma-separated browser origins | Use explicit HTTPS origins in production |
| `LIVE_CACHE_STALE_SECONDS` | Age after which live cache is marked stale | `10`, minimum effective value 2 seconds |
| `DB_PATH` | SQLite database path | Project `users.db` |
| `APP_SECRET_SALT` | Local encryption derivation salt | Set a private production value |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token | Required for Telegram |
| `TELEGRAM_CHAT_ID` | Operations chat/channel ID | Required for Telegram |
| `TELEGRAM_ALERTS_ENABLED` | Master Telegram switch | `true` |
| `TELEGRAM_RISK_ALERTS_ENABLED` | Live-risk Telegram switch | `true` |
| `TELEGRAM_ANTIFRAUD_ALERTS_ENABLED` | Anti-fraud Telegram switch | `true` |
| `PROFIT_ALERT_PCT_THRESHOLD` | Unusual profit as percentage of balance | `30` |
| `DOUBLE_PROFIT_PCT_THRESHOLD` | Critical profit percentage | `100` |
| `HIGH_LOT_ALERT_THRESHOLD` | Individual-position lot trigger | `1.0` |
| `ACCOUNT_LOT_ALERT_THRESHOLD` | Account gross-lot trigger | `5.0` |
| `ALERT_DEDUPE_RETENTION_SECONDS` | Telegram duplicate suppression | `86400`, minimum 900 |
| `LOW_DEPOSIT_ALERT_THRESHOLD` | Maximum deposit classified as small | `500` |
| `LOW_DEPOSIT_HIGH_PROFIT_MIN` | Minimum profit for low-deposit anomaly | `200` |
| `LOW_DEPOSIT_PROFIT_MULTIPLE` | Profit/deposit anomaly multiple | `1.5` |
| `MT5_SERVER_LOG_DIRS` | Server journal roots | Semicolon-separated on Windows |
| `IP_GEOLOCATION_ENABLED` | Enables public-IP enrichment | `true` |
| `SMTP_HOST` | SMTP hostname for email channel | No email is sent without it |
| `SMTP_PORT` | SMTP port | `587` |
| `SMTP_TLS` | StartTLS switch | `true` |
| `SMTP_FROM` | Sender address | `mt5-alerts@localhost` |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | SMTP authentication | Optional |
| `ALLOW_INSECURE_WEBHOOKS` | Allows HTTP webhook URLs | `false`; keep false in production |

#### 3.4 Start and verify

Development:

```powershell
.\start_backend.cmd
Set-Location frontend
npm.cmd install
npm.cmd run dev
```

Production build verification:

```powershell
python -m pytest -q
Set-Location frontend
npm.cmd run lint
npm.cmd run build
```

Use `start_production.cmd` after the virtual environment and frontend build exist. Confirm the backend health endpoint, browser login, Manager connection, live groups, Telegram test, and report exports.

### 4. Login, profiles, groups, and filters

#### 4.1 Login

The first database initialization creates the local administrator account documented by the deployment process. Change any initial password before production use. Logout clears the browser session.

#### 4.2 System Manager profile

The application primarily uses the single Manager identity configured in `.env`. Compatibility profile fields sent by older frontend code are ignored by the backend for Manager identity. This prevents browsers from choosing arbitrary MT5 credentials for protected reporting operations.

#### 4.3 Group selector

Group selectors are populated from groups visible to the configured Manager account. Select only the groups intended for the report or investigation.

Group filter rules support one pattern per line:

- `Broker\*` includes matching paths.
- `!Demo*` excludes matching paths.
- Explicitly selected groups and filter rules are resolved against groups actually returned by MT5.

Excluded account fields accept MT5 logins separated by commas or whitespace. Always record applied group and exclusion scope with exported reports.

### 5. PnL section

The PnL screen builds a closed-deal performance dashboard for a selected date range and group scope.

#### 5.1 Controls

1. Select start and end dates.
2. Select one or more MT5 groups.
3. Apply exclusions where required.
4. Run or refresh the report.
5. Use export when the returned dataset is complete and warnings are understood.

#### 5.2 KPI definitions

| KPI | Meaning |
|---|---|
| Total Clients | Distinct scoped client accounts represented in the report |
| Net Client P&L | Aggregated client closed-deal P&L for the selected period |
| Closed Lots | Sum of normalized closed trading volume |
| Total Trades | Count of reconstructed closed positions/trades according to the report lifecycle logic |

Top Accounts and Losers Accounts rank clients from real report rows. Empty lists mean no qualifying data was returned under the current scope; they are not populated with samples.

### 6. Brokerage section

The Brokerage screen reconciles opening equity, closing equity, and period cash flows to calculate brokerage P&L.

#### 6.1 Required dates

- Opening Equity Date: exact MT5 daily snapshot used as the starting equity.
- Closing Equity Date: exact daily snapshot used as ending equity.
- Summary From/To: deal and cash-flow period used by the trade summary.

#### 6.2 Core formula

```text
Net Brokerage = Closing Equity - Opening Equity
                - Deposits - Net Credit - Additional
                + Withdrawals
```

Negative equity is preserved in Real Brokerage. Missing daily rows generate warnings and coverage counts. Do not publish totals until opening/closing snapshot gaps have been reconciled.

#### 6.3 Visible sections

- Opening Equity, Closing Equity, and Net Brokerage cards.
- Top Winners and Top Losers.
- Summary Equity / Daily Equity Summary.
- Daily Snapshot table.
- MT5 Server Reports → Trades → Summary mapping.
- Trades Summary and LP Summary.
- Excel export, which preserves detailed rows for account-level reconciliation.

#### 6.4 Recommended reconciliation workflow

1. Match group selection to the MT5 source report.
2. Match exclusions.
3. Confirm MT5 server timezone and exact EOD dates.
4. Compare scoped, opening-snapshot, and closing-snapshot account counts.
5. Resolve warnings.
6. Export and compare the first differing login rather than only grand totals.

### 7. Dealing and Risk section

This area has six tabs sharing the selected live-group scope: Live Monitor, P&L Leaders, Analyser, Exposure, Book Suggestions, and Anti-Fraud. Risk Controls also exists as an implementation component; consult the route and component reference rather than assuming it is a current top-level tab.

#### 7.1 Live Monitor

The monitor reads the persistent MT5 cache and updates continuously.

KPI meanings:

- Floating P&L: sum of current open-position/account floating result.
- Total Equity: scoped account equity, including accounts without positions.
- Margin Used and Free Margin: current account aggregates.
- Open Positions and Gross Lots: active exposure counts and normalized lots.
- Flagged and Critical: accounts whose risk rules exceed configured internal levels.

Charts and tables:

- Live P&L Movement shows recent snapshots, not reconstructed historical ticks.
- Symbol Exposure aggregates buy lots, sell lots, net lots, positions, and floating P&L.
- Client Network Map plots approximate IP geolocation. It is not exact physical location.
- Top Providers counts enriched public network providers.
- Countries & Shared IPs groups accounts by LastIP enrichment.
- Risk & Abuse Alerts shows risk score, margin, exposure concentration, network evidence, and rule signals.
- Worst Open Positions sorts current losses.
- Live Symbol Book summarizes the current book by symbol.

Live risk rules include stop-out proximity, floating loss relative to balance, concentrated exposure, grid-like positions, increasing-size/martingale-like exposure, burst execution, simultaneous opposing sides, volume escalation, shared login IP, and hosting/VPS evidence. These are operational signals, not a finding of wrongdoing.

#### 7.2 P&L Leaders

- Live Floating Winners/Losers use current account floating P&L.
- Daily, Weekly, Monthly, and Overall tabs use closed deals.
- Overall currently uses the 31-day history requested by the frontend.
- Data-quality badges show returned deals, trade deals, closing deals, timezone, and missing timestamps.
- The current P&L Leaders screen refreshes 60 seconds after a ready response and retries after 2 seconds while warming.

#### 7.3 Client Analyser

Search a client login and select a historical date range. The analyser pairs opening and closing MT5 deals into completed position lifecycles.

##### Pattern rules

| Pattern | Implemented rule |
|---|---|
| Scalp | Holding time is less than or equal to the selected scalping threshold; default 180 seconds |
| Reversal | Same-symbol side changes and the next trade opens 0–20 seconds after the prior close |
| Burst | Trade opens within 2 seconds of the immediately previous opening |
| Grid | Same-symbol trades open within 300 seconds, price difference is at most 0.2%, and volume difference at most 0.01 lots |
| Martingale | Previous same-symbol trade lost and the next position volume is larger |

Toxicity score is capped at 100 and equals qualifying scalp percentage plus reversal percentage plus burst percentage. Scalp percentage contributes only when at least 30% of analysed trades are scalps. It is an observed behavior score, not a prediction or compliance verdict.

Other outputs include gross/net P&L, win rate, profit factor, maximum drawdown, worst loss streak, symbol concentration, hourly activity, daily activity, symbol breakdown, and the full lifecycle grid.

##### Execution network evidence

- Journal proven means the trade ID and public IP were found together in an accepted server journal line.
- Login IP fallback means current/historical `LastIP` is displayed only as an inference.
- All proven IPs lists every mapped public IP where more than one exists.
- IP changed compares covered trades chronologically.
- VPS evidence uses provider, ASN, hosting, and proxy attributes.

The health panel reports proven coverage, total attribution, remote and local journal records, files scanned, and remediation actions. Export CSV preserves the investigated lifecycle/evidence rows.

##### Dealer Review

Choose Watch, Clear, Investigate, A-Book candidate, or B-Book candidate, add factual notes, and save. The review creates or updates a local investigation case, adds a case-history event, and writes the dealer audit log.

#### 7.4 Anti-Fraud

Anti-Fraud scans the configured lookback and selected groups. Settings are stored in the browser per profile.

##### Settings

| Setting | Default | Effect |
|---|---:|---|
| Lookback | 24 hours | Deal/history scan window |
| Tick gap | 3 seconds | Minimum real quote gap for latency confirmation |
| Candidate profit | 10 | Minimum P&L for latency candidate |
| Require total realized P&L | Off | Optional account-level gating |
| Minimum total realized P&L | 0 | Gating threshold when enabled |
| Maximum duration | 5 minutes | Maximum duration for sharp/latency candidates |
| Scalp duration | 180 seconds | Short-lifetime threshold |
| Scalp sequence minimum | 3 | Minimum profitable short trades for HFT/scalper signal |
| Sharp-profit threshold | 100 | Minimum fast-trade profit |
| Large order | 1 lot | Individual open-position threshold |
| Large account | 5 lots | Gross open lots per account |
| Mini equity | 100 | Active account low-equity threshold |
| Stop-out margin level | 100% | Critical open-position threshold |

##### Active detectors

| Detector | How it works | Source |
|---|---|---|
| Latency Arbitrage | A profitable short-duration candidate opens within ±2 seconds of the first tick after a real tick-history gap | Deals + `TickHistoryRequest` |
| Scalpers HFT | Minimum sequence of profitable trades held within scalp duration | Deals |
| Sharp Deals | Trade profit exceeds threshold and closes inside maximum duration | Deals |
| Achieved Profit | Account realized P&L exceeds configured threshold in lookback | Deals |
| Lookback Deposit / High Profit | Profit exceeds the minimum and configured multiple of deposits inside the selected lookback | Lookback deals + balance postings |
| Lifetime Deposits / High Profit | Profit exceeds the minimum and configured multiple of lifetime cumulative deposits | Lifetime deals + balance postings |
| Starting Capital / High Profit | Profit exceeds the minimum and configured multiple of starting balance plus lookback deposits | Lifetime deals + balance postings |
| Repeated Funding | Three or more separate deposits occurred inside the selected lookback | Balance postings |
| Profit Withdrawal Pressure | Lookback withdrawals are at least the configured low-deposit threshold and 80% of recent realized profit | Deals + balance postings |
| Single-Trade Profit Concentration | One closed deal generated at least 80% of gross winning profit | Closed deals |
| Deposit / Withdrawal Churn | Deposits and withdrawals are each at least the funding threshold and at least 80% matched | Balance postings |
| Symbol Profit Concentration | One symbol generated at least 80% of gross winning profit across multiple trades | Closed deals |
| Hedge Accounts | Account has simultaneous BUY and SELL positions on one symbol | Positions |
| Failed Stop Outs | Positions remain while equity ≤ 0 or positive margin level is at/below configured threshold | Accounts + positions |
| Mini Accounts | Active equity is at/below configured threshold | Accounts |
| Large Volume by Account | Gross open lots exceed account threshold | Positions |
| Large Volume by Order | One position exceeds order threshold | Positions |
| Agent Commissions | MT5 agent daily/monthly commission postings exist | Deals |

Latency candidates are checked only for the highest-profit candidates, up to the implementation cap of 40 per scan. A rapid profitable trade without tick-gap evidence remains a candidate and is not labelled confirmed latency arbitrage.

##### Data-required detectors

Rejected Orders, Trade Request Flood, IP Control, CID Control, Invalid Balances, Overdue Credits, Stealing Quotes, and Affiliate Cheating remain marked Data Required until their named journal/ledger source is available. The product does not fabricate results for them.

##### Toxic client review

The watchlist groups verified rule events by client and computes an investigation score. Open client details to inspect each event’s detector, time, symbol, value, position, and evidence. Copy Report produces a compact review payload; it does not prove misconduct.

#### 7.5 Risk Controls

This is a read-only stress calculator. It refreshes approximately every second while active.

| Control | Meaning |
|---|---|
| Directional market shock | Percentage move applied directionally to current exposure; negative hurts BUY and benefits SELL |
| Margin level floor | Account watchlist threshold |
| Concentration limit | Maximum one-symbol share of account exposure |
| Symbol gross limit | Maximum gross symbol notional in USD |

Approximate notional uses real lots × contract size × current price. Scenario P&L applies the selected directional shock to BUY/SELL exposure. Projected equity adds scenario P&L to current equity. Projected margin level compares projected equity to current margin where margin is positive.

The page shows portfolio totals, symbol breaches, account watchlist reasons, and MT5 group policies returned by `GroupGet`. A missing group-policy field is shown as unavailable, not guessed.

### 8. Manager section

Manager Overview uses the persistent in-memory cache for fast reads.

- Active Accounts shows visible versus enabled accounts.
- Open Positions, Total Balance, Total Equity, Margin, and Free Margin are cache aggregates.
- Margin Call Alerts are derived from account margin conditions.
- Summary Book displays client/coverage positions, buy/sell volume and price, net volume, client/coverage profit, and uncovered result.
- Asset Exposure displays normalized MT5 exposure records.
- Top Net Long and Top Net Short charts rank absolute USD net exposure.
- Force Refresh refetches the backend cache view; it cannot force an unreachable MT5 server to respond.

Always check Last Update and status. `Connection Error` plus old values means use the values only as the last known snapshot.

### 9. Market Watch section

Market Watch reads symbol specifications and the latest cached ticks.

- Total Symbols: visible symbol specifications in scope.
- Filtered Symbols: rows matching search and display limit.
- Active Ticks: symbols with usable latest ticks.
- Average/Maximum Spread: `(Ask - Bid) / point`, in symbol points.
- Table columns: symbol/path, spread, Bid, Ask, latest tick time, contract size, tick value, and initial margin.
- Display Limit controls browser/table volume from 50 to 500 rows.
- Peak Margin Exposure ranks accounts with meaningful margin usage and shows margin level and floating P&L.

A blank or old tick may indicate a closed market, disabled symbol, unavailable tick capability, or stale Manager connection.

### 10. Operations section

Operations is the persistent workflow layer. Its local records remain available across page reloads.

#### 10.1 Alert Center

The feed combines:

- Account live-risk severity and evidence.
- VPS/hosting-network evidence.
- Large positions at/above the configured lot threshold.
- Unusual floating profit at/above the configured balance percentage.
- Configured exposure-limit warning/breach states.

Each alert has a stable hash key based on its type and relevant values. Acknowledge stores actor and time. Create Case copies a client-linked alert into an investigation case. Platform alerts without a client login cannot be converted directly to a client case.

#### 10.2 Investigation Cases

Case fields:

- Client login: required positive MT5 login.
- Title: 2–200 characters.
- Assigned dealer: up to 120 characters.
- Status: open, investigating, monitoring, resolved, or closed.
- Priority: low, medium, high, or critical.
- Summary/notes and structured evidence.

Every create/update/review adds case history and an audit event. Closing a case does not delete it.

#### 10.3 Related Accounts

Verified Shared Attributes detects:

- Accounts sharing the same MT5 `LastIP`.
- Accounts sharing the same non-zero MT5 introducing-agent account.

Coordinated Position Timing compares different accounts’ current positions. Same symbol, same side, and opening times within five seconds creates a timing-similarity candidate. The implementation caps displayed live correlations to protect performance.

Device/CID relationships use two authoritative MT5 sources in Operations Center → Related Accounts:

- Live sessions come from `OnlineGetArray / IMTOnline.ComputerID` and are retained as observations.
- **CID login journal audit** reads `LoggerServerRequest` client-login entries using preset lookbacks, custom From/To dates, or All History. It records device type, client build, CID, access server, ping, IP, and available geo fields, then flags shared CIDs, IPs used by three or more CIDs, accounts using multiple CIDs, and CIDs seen from multiple countries. Blocking SDK calls run in isolated, time-limited workers so an unsupported journal mode cannot freeze the RMS.

Journal results depend on the Manager account's server-log permission and the server's configured retention. The RMS reports missing evidence as unavailable; it does not infer a CID from unrelated MT5 fields. Payment-detail relationships still require external CRM or payment data.

#### 10.4 Exposure Limits

Scopes:

- Account: numeric login.
- Group: exact live group.
- Symbol: exact symbol; supported metrics are gross lots and gross exposure.

Metrics:

- Gross lots: sum of absolute position lots.
- Floating loss: sum of negative floating P&L expressed as positive loss.
- Gross exposure: sum of absolute notional exposure.
- Margin level below: inverse limit using the lowest positive margin level.
- Concentration percentage: highest account symbol concentration in scope.

Warning % creates a pre-breach line. For ordinary maximum limits, warning = threshold × warning percentage. For `margin_level_below`, the inverse warning line is threshold / warning percentage. Breaches create alerts and dispatch to enabled channels meeting minimum severity.

#### 10.5 Intelligence

The default scan uses 90 days and reads real MT5 deal history.

- Profitability: reconstructed closed-position realized P&L.
- Observed Value Score: cohort-relative score = 35% normalized trade count + 35% normalized volume + 30% normalized absolute realized P&L. It is not predicted future LTV.
- Historical comparison: first half versus second half of the selected period; `P&L Change = current half - previous half`.
- Symbol concentration: largest symbol volume divided by total volume.
- Copy-trading candidate: different accounts, same symbol and side, opening inside the same three-second bucket.
- Latency candidate: profit ≥ 10 and holding time ≤ 300 seconds. Tick-gap confirmation is still required before calling it latency arbitrage.
- Deposit/profit anomaly: realized P&L is at least the greater of 500 or 2× deposits, with extra handling for accounts with no observed deposit in the lookback.

#### 10.6 Reconciliation

The automatic daily snapshot stores UTC report date, account count, total balance, equity, margin, open positions, Manager-cache timestamp, status, and warnings. The supervisor retries every five minutes until a complete snapshot is stored for the date. Run Now performs an immediate snapshot and records an audit event.

This operations snapshot is a control total, not a replacement for the detailed Brokerage report.

#### 10.7 Notification Channels

| Channel | Configuration |
|---|---|
| Telegram | Uses configured bot token/chat; destination may remain `configured-chat` |
| Email | Valid recipient plus SMTP environment variables |
| Webhook | HTTPS URL by default; receives JSON containing the alert |

Minimum severity filters delivery. Channel failures are logged and do not stop evaluation of other channels. Alert deduplication prevents repeated delivery of the same exposure alert during the suppression window.

#### 10.8 Audit Log

The audit log records case operations, dealer reviews, exposure-limit changes, alert acknowledgements, channel changes, intelligence scans, reconciliation runs, selected report exports, Telegram settings changes, and Telegram tests. Actor comes from the current local username or request actor header.

Treat the local audit log as an application audit trail. For regulated deployments, also retain reverse-proxy authentication logs, Windows service logs, file-access logs, and immutable centralized audit storage.

#### 10.9 WebSocket updates

The Operations WebSocket sends Manager connection/cache heartbeat messages approximately every two seconds. The browser uses them to refresh the live operations payload, avoids overlapping requests, pauses refresh while the tab is hidden, and reconnects after disconnects. HTTP requests have timeouts so a slow upstream call does not leave the interface waiting indefinitely.

### 11. Book Shifting section

Book Shifting is a separate operational monitor in the main sidebar. It records a movement when the same MT5 client login appears in a different group in a newer complete account-cache generation.

#### 11.1 What is captured

Each immutable event contains:

- detection time in UTC;
- configured system Manager login;
- client login and name;
- previous and destination MT5 group;
- previous and destination configured book (`A Book`, `B Book`, `Cent`, `Hybrid`, or `Unassigned`);
- authoritative MT5 account Equity in the first complete snapshot showing the destination group; and
- the source Manager generation timestamp.

The first observation of an account establishes its baseline and never creates a false shift. Every later group change is retained, including changes within the same book and movements involving an unassigned group. A Book to B Book and B Book to A Book counters are displayed separately.

#### 11.2 Accuracy and timing

The observer examines each newly published atomic user generation. By default account data refreshes every five seconds and the observer checks every second, so expected detection latency is approximately zero to five seconds plus Manager response time. It does not interpolate or reconstruct equity. The captured value is exactly the account Equity returned in that destination generation.

This is polling evidence, not an MT5 database transaction callback. If a client moves to another group and back entirely between two completed user snapshots, or while this backend is stopped, the transition cannot be observed. Keep the backend continuously running and clocks synchronized. A stale/disconnected badge means new changes are not currently reliable.

#### 11.3 Using the monitor

- KPI cards show total retained movements, A-to-B count, B-to-A count and latest detection time.
- Search accepts Manager login, client login, client name, source group or destination group.
- From Book and To Book filters isolate a direction such as A Book to B Book.
- Refresh performs an immediate read; otherwise the page reads durable movement history every second and displays its last successful synchronization time.
- Export CSV downloads the currently filtered rows, including the source generation epoch for audit reconciliation.
- The history table is newest first and remains available after application restart because it is stored in `users.db`.

Configure exact group-to-book mappings under Controls before relying on book classifications. A group not mapped to a configured book is deliberately shown as `Unassigned`; it is never guessed from the group name.

### 12. Controls section

#### 12.1 Connection

Displays the configured single Manager identity and connected/disconnected status. Configuration changes to MT5 host/login/password are made in `.env` and require service restart.

#### 12.2 Book mappings

Assign exact MT5 groups to A Book, B Book, Cent, or Hybrid. A group can exist in only one preset; assigning it to one removes it from the others when saved. These mappings classify Book Shifting events but never change the client group in MT5.

#### 12.3 Telegram switches

- Telegram Alerts: master automatic-delivery switch.
- Risk Alerts: live accounts, exposure, VPS, and large-position messages.
- Anti-Fraud Alerts: high-severity trading/network signals.

Bot Token and Chat ID badges indicate presence, not guaranteed reachability. Always use Test after deployment or token/chat changes.

#### 12.4 Trigger thresholds

- Profit Alert: floating profit as a percentage of absolute balance.
- Critical Profit: higher profit percentage tier.
- Position Size: individual position lots.
- Account Exposure: gross lots across the account.

Saving updates `.env`-backed alert controls and writes an audit event. Choose thresholds from actual book distributions and review false-positive volume weekly.

### 13. Trade-IP and VPS setup

Standard MT5 deal records generally do not include the client execution IP. For ticket-level proof, provide one of:

1. Manager/Admin permission for supported server journal requests.
2. Read-only replicated MT5 server connection/request journals under `MT5_SERVER_LOG_DIRS`.

The local scanner:

- accepts UTF-8 and UTF-16 logs;
- ignores files above 100 MB;
- scans at most the newest 200 matching files per root;
- looks for requested accounts/trade identifiers and public IPs;
- excludes private/reserved/non-client infrastructure addresses where identifiable.

VPS classification combines explicit hosting/proxy flags and provider/ASN evidence. Residential and mobile networks can change IPs legitimately. Hosting evidence increases risk confidence but is not proof that a prohibited strategy was used.

### 14. Daily operating procedures

#### Dealer start-of-day

1. Confirm Manager shows connected and current Last Update.
2. Confirm Operations does not show stale.
3. Review critical alerts and unresolved cases.
4. Check margin-call/stop-out accounts and worst open positions.
5. Review symbol concentration and exposure-limit warnings.
6. Confirm daily reconciliation completed.

#### Alert investigation

1. Read source, timestamp, scope, and exact threshold.
2. Open Client Analyser for historical and live evidence.
3. Check journal-proven IP coverage before using network claims.
4. Compare related-account and coordinated-timing candidates.
5. Create/assign a case and add factual notes.
6. Preserve CSV/report evidence.
7. Acknowledge only after ownership is clear.
8. Resolve/close with a documented decision.

#### End-of-day

1. Run or verify daily operations reconciliation.
2. Run Brokerage report on the intended snapshot dates.
3. Resolve missing-snapshot warnings.
4. Export and retain required reports.
5. Review notification delivery failures and backend logs.

### 15. Scaling and reliability

- The persistent Manager connection refreshes users, positions, ticks, and derived metrics at different intervals to reduce reconnect cost.
- Core Manager groups, clients, positions, panels, and metrics are staged as one generation; SDK failures keep the last complete generation instead of publishing a partial import.
- Browser live requests prevent overlap and have explicit timeouts.
- SQLite uses WAL, busy timeout, foreign keys, and operational indexes for concurrent local writes.
- Large history scans are bounded by request validation and detector caps.
- Frontend tables use display limits and scroll regions.
- Telegram delivery is serialized to respect burst limits.

For multi-instance deployment, move local state/deduplication to a shared production database and queue. A single `users.db` and in-memory alert dedupe are intended for one active backend instance.

### 16. Troubleshooting

#### Manager unreachable

- Test DNS/IP and TCP reachability to the configured host/port from the service account.
- Confirm firewall, broker allowlist, VPN, and RDP-host outbound access.
- Confirm Manager credentials and rights.
- Check `api.log` for supervisor retry messages.
- Do not interpret cached values as current when the stale warning is present.

#### No groups or accounts

- Confirm the Manager login can see the groups.
- Remove overly restrictive include/exclude patterns.
- Verify the cache has completed its first user refresh.

#### Report mismatch

- Match dates, timezone, group scope, and exclusions.
- Check daily snapshot coverage warnings.
- Preserve signed negative equity in Real Brokerage.
- Compare account rows to find the first divergence.

#### No trade-IP coverage

- Confirm server journals, not Manager terminal connectivity logs.
- Confirm date retention and service-account read permissions.
- Search a known journal for a public IP and ticket/login on the same record.
- Review remote/local record and file-scan counts in Client Analyser.

#### Telegram/email/webhook failure

- Telegram: verify token, chat ID, bot membership, outbound HTTPS, and Test.
- Email: verify SMTP host/port/TLS/authentication and sender policy.
- Webhook: use HTTPS, verify certificate/DNS, and inspect receiver logs.
- Check `api.log`; one channel failure does not imply all channels failed.

#### Database locked or slow

- Ensure only supported application processes access `users.db`.
- Keep the database on a reliable local disk where possible.
- Exclude the live database from tools that hold long file locks.
- For several backend instances, migrate to a shared server database.

### 17. Security and compliance checklist

- Use a least-privilege, read-only MT5 reporting credential.
- Place the application behind TLS and authenticated network access.
- Set explicit `ALLOWED_ORIGINS`; never use wildcard CORS in production.
- Rotate MT5, Telegram, SMTP, and application secrets.
- Protect `.env`, `users.db`, journals, and exports.
- Back up `users.db` and test restoration.
- Define retention for IPs, journals, reports, cases, and audit data.
- Restrict exports and record their access.
- Change initial credentials.
- Use HTTPS webhooks; leave insecure webhooks disabled.
- Centralize immutable logs for regulated environments.

### 18. Release acceptance

- [ ] `python -m pytest -q` passes.
- [ ] `npm.cmd run lint` and `npm.cmd run build` pass in `frontend`.
- [ ] Manager is connected and cache timestamps advance.
- [ ] Live screens correctly change to stale/offline when MT5 is unavailable.
- [ ] Known Brokerage report reconciles account by account.
- [ ] Known ticket maps only to its journal-proven IP.
- [ ] Anti-fraud unavailable detectors remain Data Required.
- [ ] Exposure warning and breach thresholds behave as configured.
- [ ] Telegram, email, and webhook channels are tested where enabled.
- [ ] Case history and audit events are written.
- [ ] Daily reconciliation stores a complete current snapshot.
- [ ] Secrets, TLS, CORS, backups, and retention have been reviewed.

For deployment-specific Windows/RDP instructions, see `DEPLOY_WINDOWS_RDP.md`. For deeper reconciliation and trade-IP operational notes, see `BROKER_OPERATIONS_GUIDE.md`.

## Broker operations

This guide is for brokerage administrators, dealers, risk teams, and deployment engineers. It describes what the product reads from MT5, how numbers are reconciled, how trade-wise IP evidence works, and how to operate the service without presenting inferred or incomplete data as fact.

### 1. Data integrity principles

- **Real Brokerage preserves signed MT5 equity.** Negative account equity is included in opening and closing totals.
- **Diff mode is the only floored view.** It may clamp negative equity to zero for a specific comparison workflow and must not be described as raw MT5 equity.
- **Trade IP provenance is explicit.** Ticket-linked journal IPs are marked as proven. When absent, the MT5 account LastIP may be shown only as an inferred fallback and is never described as execution evidence.
- **Missing data remains missing.** Warnings and coverage counts identify incomplete daily snapshots or absent journals.
- **MT5 remains the system of record.** The dashboard calculates and presents MT5 records; it does not create balances, trades, IPs, or fills.

### 2. Runtime architecture

| Component | Default address | Purpose |
|---|---|---|
| React/Vite frontend | `http://localhost:5173` | Dealer and reporting interface |
| FastAPI backend | `http://localhost:8000` | MT5 access, calculations, exports, alerts |
| MT5 Manager SDK | Broker server | Users, groups, accounts, positions, deals, daily rows |
| MT5 Admin/server journals | Broker server or copied log directory | Ticket-linked execution IP evidence |
| Telegram Bot API | External | High-lot, VPS, and risk notifications |

Start the backend with `start_backend.cmd`. Start the frontend from `frontend` with `npm.cmd run dev`. In production, place both services behind TLS, restrict CORS, and run them under a service supervisor.

### 3. Required configuration

Copy `.env.example` settings into `.env` and replace placeholders. Never commit `.env`.

```dotenv
PORT=8000
ALLOWED_ORIGINS=https://broker-console.example.com

TELEGRAM_BOT_TOKEN=replace_with_botfather_token
TELEGRAM_CHAT_ID=replace_with_private_operations_chat
HIGH_LOT_ALERT_THRESHOLD=1.0
ACCOUNT_LOT_ALERT_THRESHOLD=5.0
ALERT_DEDUPE_RETENTION_SECONDS=86400

MT5_SERVER_LOG_DIRS=C:\MT5Server\Logs;D:\MT5Archive\Logs
IP_GEOLOCATION_ENABLED=true
```

On Windows, separate multiple `MT5_SERVER_LOG_DIRS` paths with semicolons. The backend process identity needs read access to those directories. Use read-only shares where possible.

### 4. Brokerage report reconciliation

1. Select the exact same groups and excluded accounts in MT5 and the product.
2. Set Opening Equity to the MT5 daily snapshot date, normally the prior EOD.
3. Set Closing Equity to the required EOD snapshot date.
4. Set the summary range for cash flows and closed trading activity.
5. Run Real Brokerage and inspect warnings before using totals.
6. Compare `scoped_accounts`, `opening_snapshot_accounts`, and `closing_snapshot_accounts` in the API response or warning panel.
7. Export the workbook and reconcile account rows, not only the grand total.

The core bridge is:

```text
Net Brokerage = Closing Equity - Opening Equity
                - Deposits - Net Credit - Additional
                + Withdrawals
```

If a snapshot is incomplete, the backend reports how many scoped accounts lack a daily row for that exact date. Do not publish the total until the missing rows are explained.

### 5. Trade-wise IP evidence

#### Why trade IP may be unavailable

Standard MT5 deal records normally contain deal/order/position identifiers but not the client source IP. A defensible trade IP requires a server journal line containing both:

- A public client IP address.
- A matching deal, order, ticket, or position identifier.

Manager terminal logs commonly show access-server addresses. Those are broker infrastructure IPs and must not be attributed to clients.

#### Preferred source: Admin/server journal access

Grant the reporting credential permission to read server logs through `LoggerServerRequest`. On many MT5 installations this requires Admin API rights rather than ordinary Manager API rights.

Use a dedicated, least-privilege reporting account. It needs read permissions for:

- Users and accounts.
- Groups.
- Deals and positions.
- Daily reports.
- Server connection/request journals.

It must not receive trade execution, balance adjustment, user modification, or configuration write permissions.

#### Alternative source: copied server logs

1. Export or replicate MT5 server connection/request journals to a protected directory.
2. Retain filenames with dates in `YYYYMMDD` form where possible.
3. Configure the parent directories in `MT5_SERVER_LOG_DIRS`.
4. Give the backend service read-only filesystem permission.
5. Analyse a client and date range containing known trades.
6. Confirm the Trade IP evidence panel reports local records and mapped positions.

The scanner accepts UTF-8 and UTF-16 logs, ignores files over 100 MB, searches at most the newest 200 matching files per root, and only retains lines containing requested account/ticket identifiers plus public IPs.

#### Reading the diagnostics

| Status | Meaning | Action |
|---|---|---|
| Healthy | At least 80% of closed trades have ticket-linked IP evidence | Review uncovered trades and retention gaps |
| Partial | Some trades map, but coverage is below 80% | Check journal retention, server nodes, and date range |
| Permission/source missing | No mapping and journal access returned an error | Grant journal read access or configure server logs |
| No ticket evidence | Journals were read but lines did not link IPs to trade IDs | Confirm the correct journal type and message format |

Each trade displays its primary proven IP, all proven IPs, evidence source, provider/ASN, VPS classification, and whether the IP changed from the previous covered trade.

### 6. Broker IP investigation workflow

1. Open Client Analyser and choose the client and date range.
2. Check Trade IP coverage before interpreting network results.
3. Review `IP changed` flags and multi-IP trades chronologically.
4. Compare provider, ASN, country, and VPS evidence.
5. Export CSV to preserve the evidence set used for review.
6. Record the dealer decision and supporting evidence.
7. Confirm suspicious behavior with KYC, device/CID records, payment relationships, and server journals before taking action.

IP changes alone are not proof of abuse. Mobile networks, CGNAT, travel, VPNs, access servers, and IPv4 reassignment can all produce legitimate changes.

### 7. Real-time notifications

Live risk is normally refreshed every one to two seconds while its operational views are active. A new high-lot ticket alerts immediately on the next successful snapshot. An existing ticket alerts again only after crossing another configured lot threshold step. Floating P&L changes do not retrigger the same ticket.

VPS and account-state alerts use stable deduplication keys. `ALERT_DEDUPE_RETENTION_SECONDS` controls suppression; the default is 24 hours. Test Telegram from Settings after every deployment.

Suggested starting thresholds:

| Brokerage profile | High lot | Account gross lots |
|---|---:|---:|
| Small retail book | 0.50 | 2.00 |
| Standard retail book | 1.00 | 5.00 |
| Professional book | 5.00 | 20.00 |

Tune against the actual book and review alert volume weekly.

### 8. Security and privacy

- Use TLS for browser, API, and reverse-proxy traffic.
- Set `ALLOWED_ORIGINS` to explicit production origins; do not leave `*` in production.
- Store MT5 and Telegram secrets in a secret manager or protected service environment.
- Restrict journal directories because they may contain client IPs and account identifiers.
- Define IP-data retention and access rules consistent with applicable privacy law and contracts.
- Log administrative access and export activity at the deployment layer.
- Back up `users.db` and test restoration.
- Rotate credentials and Telegram tokens after staff or vendor access changes.

### 9. Troubleshooting

#### Correct trades, zero trade-IP coverage

- Verify the evidence panel's remote/local record counts.
- Confirm the credential can call `LoggerServerRequest`.
- Confirm configured logs are server journals, not terminal/Manager connectivity logs.
- Search a known server journal for the client login, deal ID, and a public IP on the same line.
- Verify the requested date range matches retained logs.

#### IP location is unavailable

- Confirm `IP_GEOLOCATION_ENABLED=true`.
- Private/reserved IPs are intentionally not sent for geolocation.
- Provider lookup failures are cached briefly and retried later.
- Network geolocation is approximate and must not be treated as exact physical location.

#### Report total differs from MT5

- Confirm group patterns and exclusions.
- Compare account counts and missing-snapshot warnings.
- Confirm the exact EOD date and timezone.
- Ensure Real Brokerage is used when signed equity is required.
- Export account rows and identify the first differing login.

#### Telegram is noisy

- Increase lot thresholds.
- Increase `ALERT_DEDUPE_RETENTION_SECONDS`.
- Confirm only one backend instance is sending to the same chat, or use a shared dedupe store for multi-instance deployment.

### 10. Release acceptance checklist

- [ ] Backend tests pass with `python -m pytest -q`.
- [ ] Frontend production build passes with `npm.cmd run build` in `frontend`.
- [ ] Real Brokerage reconciles a known MT5 daily export account by account.
- [ ] Incomplete snapshots produce visible warnings.
- [ ] Trade IP diagnostic status matches journal permissions and configured sources.
- [ ] A known ticket maps to the expected public IP without substituting account LastIP.
- [ ] High-lot test generates one Telegram message and no P&L tick spam.
- [ ] CORS, TLS, secrets, backups, retention, and least-privilege access are configured.
- [ ] Operations staff can explain the difference between verified, inferred, and unavailable data.

## Anti-Fraud methodology

- This document describes the rules currently implemented in `api.py` and `frontend/src/pages/AntiFraud.jsx`.
- A detector event is a **review signal**, not a finding of fraud. MT5 data gaps can leave a detector with no data.

### Scope and source data

- The scan uses the single configured MT5 Manager connection and the groups selected in the UI. Excluded logins and symbol patterns are removed from the relevant scan data.
- The default deal lookback is the last **24 hours**, ending at scan time in UTC; the request accepts **1–168 hours**. Open positions and account values are read as current MT5 data.
- MT5 deals are deduplicated by deal ticket. Open positions are deduplicated by position ticket. Only logins in the resolved selected groups are included.
- A completed trade is assembled from MT5 buy/sell deals with the same position ID. It requires at least one entry deal and one exit deal. Opening time is the first entry; closing time is the last exit; holding time is their difference.
- **Realized trade P&L** = sum of `Profit + Commission + Fee + Storage/Swap` across the lifecycle's deals, rounded to two decimals. **Account realized P&L** = sum of these completed trade P&Ls in the lookback. Deposit and withdrawal postings are not included in this trade P&L.
- **Open gross lots** = sum of open position lots; **floating P&L** = sum of open position `Profit` fields in the client summary. The summary uses the position profit field and does not add separate open-position swap or commission fields.
- **Lookback deposits** = sum of positive `DEAL_BALANCE` profit postings in the lookback. **Lookback withdrawals** = absolute sum of negative balance postings. **Lifetime deposits** = positive balance postings in all available account history, fetched only for candidate accounts with at least 200 realized profit by default.
- **Starting balance** = the sum of `Profit + Commission + Fee + Storage/Swap` on all retrieved deals before the lookback, floored at zero. **Starting capital** = starting balance + lookback deposits. Missing or incomplete lifetime history can affect these values; inspect scan warnings.

### Detector rules

- **Latency Timing Anomaly — warning, fraud indicator:** A profitable completed trade with P&L at least **10**, hold time at most **5 minutes**, and, when enabled, account realized P&L at least the configured minimum becomes a candidate. The scan checks up to **3** highest-profit candidates by default (`ANTIFRAUD_MAX_TICK_CHECKS`, capped at 40). A valid historical bid/ask tick gap of at least **3 seconds** must end at the exact MT5 `TimeMsc` trade opening time. Missing precise time or complete valid tick coverage produces an `UNCHECKED` candidate, never a positive event. A matching gap is a timing indicator for dealer review, not proof of fraud.
- **Short-Hold Scalping — warning, behavioral indicator:** At least **3** profitable completed trades for one account, each held at most **180 seconds**, without the additional high-frequency evidence below. Evidence includes trade count, maximum holding time, and inter-trade opening intervals.
- **High-Frequency Scalping — high, behavioral indicator:** At least **10** qualifying short-hold trades (or a higher configured scalp sequence minimum) and at least **5** consecutive opening intervals of at most **2 seconds**.
- **Sharp Deals — high:** A completed trade has realized P&L at least **100** and holding time at most **5 minutes**. One event is created per qualifying trade.
- **News Trading — high or warning:** A trade opens within **5 minutes** before or after the closest MT5 server news item. It is **high** if that news item's flags include bit `1`, otherwise **warning**. Evidence records the time offset, news item, holding time, and trade P&L.
- **Achieved Profit — warning:** Account realized P&L is at least `max(minimum_total_realized_pnl, sharp_profit_threshold)`; defaults make this **100**. The `check_total_realized_pnl` switch only gates Latency Arbitrage candidates; this rule always applies its own threshold.
- **Lookback Deposit / High Profit — warning:** `0 < lookback deposits <= 500` and account realized P&L is at least `max(200, lookback deposits × 1.5)`.
- **Lifetime Deposits / High Profit — high:** `0 < lifetime deposits <= 500` and account realized P&L is at least `max(200, lifetime deposits × 1.5)`.
- **Starting Capital / High Profit — high:** `0 < starting capital <= 500` and account realized P&L is at least `max(200, starting capital × 1.5)`.
- For the same account and funding/profit activity, only the strongest of these three funding/profit events is emitted: starting capital, then lifetime deposits, then lookback deposits. The emitted event lists the correlated suppressed rules. These are financial-risk indicators with `PARTIAL` funding quality because an MT5 balance posting alone does not prove external payment origin. Credit, correction, bonus, and balance postings with transfer/reversal/bonus/correction comments are excluded from deposit/withdrawal totals; ambiguous funding suppresses these detectors.
- **Hedge Accounts — warning:** At least one open BUY and one open SELL position exist on the same symbol for an account. One event is created per symbol.
- **Failed Stop Outs — critical:** An account still has open positions and either equity is at most **0**, or a positive margin level is at most **100%**. A zero/missing margin level alone does not satisfy the margin rule.
- **Mini Accounts — warning:** An account with open positions has available equity at most **100**.
- **Large Volumes by Account — warning:** Total gross open lots are at least **5** **and** gross exposure / equity (or balance when positive equity is unavailable) is at least **5×**. Exposure is the sum of `lots × MT5 contract size × current price × profit-currency conversion` for every position. Conversion uses the position's MT5 `RateProfit`, or 1 when the symbol profit currency and account currency match. Missing contract size, current price, conversion, or positive capital prevents the alert.
- **Large Volumes by Order — warning:** An individual open position has at least **1** lot **and** its converted exposure / equity (or balance when positive equity is unavailable) is at least **1×**. Lot size is supporting evidence, not the sole trigger. Both ratios are configurable.
- **Agent Commissions — info:** Sum the `Profit` field, or `Commission` when profit is absent, on daily/monthly agent commission deal actions. A nonzero total creates one system-level event with login `0`.
- **Repeated Funding — warning:** At least **3** separate positive balance postings occur in the lookback. Event value is the deposit count.
- **Profit Withdrawal Pressure — high:** Account realized P&L is positive and lookback withdrawals are at least `max(500, realized P&L × 0.8)`.
- **Deposit / Withdrawal Churn — warning:** Both lookback deposits and withdrawals are at least **500**, and the smaller total is at least **80%** of the larger. Event value is `smaller / larger × 100`.
- **Single-Trade Profit Concentration — warning:** At least **3** completed trades by default, gross winning profit (sum of positive normalized completed trade P&L) at least **200**, and one winning trade supplies at least **80%** of it.
- **Symbol Profit Concentration — warning:** At least **3** completed trades and **2** winning trades, gross winning profit at least **200**, and one symbol supplies at least **80%** of gross winning profit. The minimum completed trade count is configurable.
- The default `500`, `200`, and `1.5` funding thresholds come from `LOW_DEPOSIT_ALERT_THRESHOLD`, `LOW_DEPOSIT_HIGH_PROFIT_MIN`, and `LOW_DEPOSIT_PROFIT_MULTIPLE`. Trade, tick, volume, equity, margin, and news thresholds can be changed in the Anti-Fraud settings panel; those settings are stored in that browser.

### Other displayed calculations

- **Client toxicity percentage** = `min(100, qualified scalp percentage + reversal percentage + burst percentage)`. Each percentage is `matching completed trades / all completed trades × 100`; scalp percentage contributes only when it reaches **30%**. A reversal is an opposite-side trade on the same symbol opened within **20 seconds** after the previous trade closed. A burst is opened within **2 seconds** of the preceding trade. The toxicity score does not itself create an Anti-Fraud detector event.
- **Client severity** = critical if any critical event exists, else high if any high event exists, else warning if any event exists, else info.
- **UI toxic-client score** = **40** per critical event, **25** per high event, and **10** per warning or info event. This is a UI sorting score, separate from the toxicity percentage and without a 100-point cap.
- **Group Risk Ranking** sorts groups by `critical events × 1000 + high events × 100 + all events` and shows the top **6** groups. It counts distinct affected account logins.
- **Summary events** counts all generated events; **critical** counts critical events; **accounts** counts distinct positive account logins with events. The response includes at most **2,000** events and **500** client summaries, so the displayed list can be shorter than the summary totals.
- Each emitted event includes `event_id`, `rule` (with legacy `detector` retained), `category`, `severity`, `login`, `timestamp`, `triggering_values`, `thresholds`, `evidence`, `data_quality`, and `related_event_ids`. Quality is `VERIFIED` or `PARTIAL` for emitted events; candidates with missing precise ticks are returned separately as `UNCHECKED` and do not increase event counts.
- **Small-balance outsized-profit signal** is added to a client summary, not as a detector event, when balance is positive and at most **500**, and open floating P&L is at least `max(200, balance × 1.5)`.
- IP provider, location, and VPS status in client summaries come only from cached IP intelligence during the Anti-Fraud scan; it does not wait for a network geolocation lookup.

### Live scan and freshness logic

- While the Anti-Fraud tab is active and groups are selected, the browser requests `/api/risk/antifraud` initially, then **15 seconds after a successful response** or **5 seconds after a warming response**. It cancels in-flight requests when the tab or scope changes. The UI request timeout is **300 seconds**.
- A normal request returns the in-memory snapshot during its cache lifetime. `ANTIFRAUD_CACHE_SECONDS` defaults to **120 seconds** and is **300 seconds in the current `.env`**; therefore a 15-second UI request does **not** mean a fresh MT5 scan every 15 seconds.
- If no snapshot exists, the API returns `status: warming` and starts a background scan. When a stored snapshot exists, the API may return it with `cache.stale: true`, `age_seconds`, and `refreshing: true` while a background scan runs. Each scope and settings combination has its own cache key and scan lock.
- The manual refresh button sends a new `refresh_token`, bypassing the normal cache and requesting a fresh scan. It can take longer because MT5 deals, news, positions, accounts, funding history, and candidate ticks must be read.
- Initial requests register persisted monitor scopes. The backend periodic Anti-Fraud supervisor can refresh these scopes when the tab is closed; see Command Center and persistent risk events. There is no dedicated Anti-Fraud WebSocket payload stream.
- Detector cards show **active**, **no data**, or **data required**. No deals makes deal-based detectors `no_data`; no news makes News Trading `no_data`; absent tick history produces a warning. `data_required` means the necessary source is not available in the current integration, not that the behavior is safe.
- Rejected Orders, Trade Requests Flood, IP Control, CID Control, Invalid Balances, Overdue Credits, Stealing Quotes, and Affiliate Cheating are currently **data required** and do not calculate alerts. The UI hides these cards from its active detector grid.
- If the configured MT5 manager is disconnected, the scan returns a warming/offline result and no new verified events. Always check `generated_at`, `cache.stale`, and `warnings` before treating an empty result as a clean account.

### Alert delivery logic

- The backend calls `notify_new_antifraud_events` only after a completed fresh scan. Cached responses do not resend alerts.
- Telegram delivery requires `TELEGRAM_ALERTS_ENABLED=true`, `TELEGRAM_ANTIFRAUD_ALERTS_ENABLED=true`, a bot token, and an Anti-Fraud chat ID or default chat ID.
- Each client summary with **critical** or **high** severity is considered for an account-level Telegram message. The message includes account/group, severity, toxicity, funding, P&L, position metrics, signals, and up to **3** recent evidence lines. The message title changes for low-deposit/high-profit or detected VPS signals.
- A digest of login, severity, deal count, total profit, event count, and gross open lots suppresses duplicate client messages in the current process. Digest records older than **24 hours** are removed; a material change can generate a new message before that.
- If **no** account-level messages were prepared, the backend considers up to **20** critical/high events for event-level messages, deduplicated by detector, login, position ID, and evidence. Warning/info events do not generate these Telegram alerts.
- Telegram messages are sent asynchronously, at most **20 per delivery batch**, with roughly **1.1 seconds** between sends. Delivery errors are logged. The in-process duplicate cache is not durable across server restarts.
- The Anti-Fraud page displays all returned event severities and evidence. Telegram alerting is a separate channel from these on-screen events.

### Code references

- Scan and detector implementation: `api.py` (`build_antifraud_snapshot`, `build_client_trade_lifecycles`, `analyse_client_lifecycles`).
- API caching and refresh: `api.py` (`get_antifraud_snapshot`).
- Telegram routing and duplicate suppression: `api.py` (`notify_new_antifraud_events`, `_send_telegram_messages`).
- Browser scan interval, settings, and rankings: `frontend/src/pages/AntiFraud.jsx`.

## Windows deployment

This deployment serves the React build and FastAPI from one process and one public port. MT5 credentials remain in `.env` on the server.

### Recommended network layout

- Public application: TCP `8780` temporarily, or HTTPS `443` through the server's existing reverse proxy.
- FastAPI: same application process; do not expose a separate backend port.
- RDP `3389`: allow only trusted administrator IP addresses.
- Do not expose SMB `445`, PostgreSQL `5432`, WinRM `5985/5986`, or MT5/database internals to the internet.

The supplied `netstat` shows that ports `80` and `443` are already occupied. Do not terminate those listeners until you identify their owning service with:

```powershell
Get-NetTCPConnection -State Listen -LocalPort 80,443 |
  Select-Object LocalAddress,LocalPort,OwningProcess
Get-Process -Id <PID>
```

### 1. Copy the project

Copy the clean project to a fixed path such as:

```text
C:\Apps\MT5Brokerage
```

Copy `.env` separately over the RDP session. Do not email it or place it in a public download location.

### 2. Install prerequisites

Install these on the RDP machine:

1. 64-bit Python 3.11.
2. Node.js LTS.
3. The broker/vendor MT5 Manager SDK Python package and native runtime matching Python 3.11 and Windows x64.
4. Microsoft Visual C++ runtime required by the MT5 SDK.

Confirm the RDP host can make outbound connections to the configured MT5 server and Telegram API.

### 3. Configure `.env`

Keep the existing MT5 and Telegram values. For direct temporary access, add:

```dotenv
PORT=8780
ALLOWED_ORIGINS=http://YOUR_SERVER_IP:8780
```

For trade-wise journal-proven IPs, configure a real server-journal directory:

```dotenv
MT5_SERVER_LOG_DIRS=D:\MT5ServerJournals
```

Without server journals or Admin journal permission, the analyser clearly labels MT5 `LastIP` as an inferred fallback.

### 4. Build and run

Open PowerShell as Administrator:

```powershell
Set-Location C:\Apps\MT5Brokerage
Set-ExecutionPolicy -Scope Process Bypass
.\setup_rdp.ps1 -PublicPort 8780 -PublicIp YOUR_SERVER_IP
.\start_production.cmd
```

Test locally on the RDP machine:

```powershell
Invoke-RestMethod http://127.0.0.1:8780/api/health
```

Then browse to:

```text
http://YOUR_SERVER_IP:8780
```

### 5. Cloud firewall

In the hosting provider firewall/security group, add inbound TCP `8780`. For testing, restrict the source to your office/home public IP. Avoid `0.0.0.0/0` unless the application must be globally accessible.

Restrict TCP `3389` to administrator IPs. Remove public rules for `445`, `5432`, `5985`, and `5986` unless a documented private-network dependency requires them.

### 6. Production HTTPS

For permanent production, use the service currently owning ports `80/443` as a reverse proxy to `http://127.0.0.1:8780`, attach a domain and TLS certificate, then bind the application to localhost. Set:

```dotenv
ALLOWED_ORIGINS=https://broker-console.example.com
```

Do not publish MT5 credentials, Telegram tokens, `.env`, `users.db`, server journals, or report exports through the web server.

### 7. Startup and verification

Run `start_production.cmd` through an approved Windows service supervisor or Task Scheduler under a dedicated least-privilege Windows account. After every restart verify:

```powershell
Invoke-RestMethod http://127.0.0.1:8780/api/health
Get-NetTCPConnection -State Listen -LocalPort 8780
```

Confirm Manager status, live groups, Telegram test delivery, report exports, and trade-IP evidence coverage in the UI.

## Local startup

1. Configure the single MT5 Manager in the project-local .env.
2. Build the React client if frontend source changed:

   cd frontend
   npm.cmd install
   npm.cmd run build
   cd ..

3. Start the FastAPI server from the project root:

   .\start_backend.cmd

4. Open http://127.0.0.1:8000 and check http://127.0.0.1:8000/api/ready.

The launcher reuses an existing server on port 8000. Use Ctrl+C to stop a server running in your terminal. Runtime output belongs in logs/. The project-local .env and users.db stay at the root because existing launch and storage paths use them.

## Google Sheets integration

The Dealing and Risk page can export its current live snapshot to the configured Google spreadsheet. Each sync replaces columns A:Z in these dedicated tabs:

- `RMS Summary`
- `RMS Accounts`
- `RMS VPS Users`
- `RMS High Profit` (live floating P&L, one row per account)
- `RMS Risk Watchlist`
- `RMS Toxic Traders` (persistent repeated high-risk observations)
- `RMS Open Positions`
- `RMS Symbol Exposure`

### One-time setup

1. In Google Cloud, create a service account and enable the **Google Sheets API** for its project.
2. Create a JSON key for that service account. Store it in a protected location on the server, outside this repository.
3. Open the target spreadsheet, click **Share**, and grant the service account's `client_email` **Editor** access.
4. Install the added Python dependencies with `pip install -r requirements.txt`.
5. Add these values to `.env` and restart the backend:

```dotenv
GOOGLE_SHEETS_ENABLED=true
GOOGLE_SHEETS_SPREADSHEET_ID=YOUR_SPREADSHEET_ID
GOOGLE_SERVICE_ACCOUNT_FILE=C:\secure\mt5-rms-google-service-account.json
```

As an administrator, load a live snapshot in **Dealing and Risk** and select **Sync Sheet**. The service account key never reaches the browser, and every sync is recorded in the RMS audit log.

Keep notes or manually maintained data outside columns A:Z of the RMS tabs; those columns are refreshed on each sync.

### Duplicate and toxic-trader handling

Every tab is a current-state export, so repeated syncs replace the prior RMS rows rather than append duplicates. Accounts are deduplicated by MT5 login, positions by ticket, and exposure by symbol.

An account receives a toxic observation whenever it is high/critical risk or has a risk score of at least 50. The RMS database counts only one observation per distinct live snapshot. In `RMS Toxic Traders`, accounts observed at least three times are labelled `FREQUENT TOXIC` and their rows are coloured red; newer observations are amber.

## Project structure

| Path | Purpose |
| --- | --- |
| `api.py` | FastAPI routes and application startup |
| `mt5_connection.py` | The single persistent MT5 Manager connection, quote worker, and snapshot cache |
| `risk_automation.py` | Risk rules and execution logic |
| `frontend/src/` | React pages, components, hooks, and data formatting |
| `frontend/dist/` | Built frontend served by FastAPI; generated by `npm.cmd run build` |
| `tests/` | Backend regression tests |
| `scripts/` | Administrative utilities |
| `docs/` | Guides, deployment notes, and this layout reference |
| `logs/` | Current runtime output; older root log files are in `logs/archive/` |
| `backups/` | Existing database backups |
| `tmp/` | Ignored test and temporary artifacts |
| `GoogleSheet/` | Existing local integration credentials; excluded from source and Docker build |

Keep `.env` and `users.db` at the project root for compatibility with current launch and storage paths. Both contain deployment data and are excluded from source control and Docker builds. Do not move the Manager SDK connection into a second worker process: the app currently operates with one Manager handle in the FastAPI process.

## SDK accuracy and limits

- Source reviewed: `D:\MetaTrader5SDK\Include\Bases\MT5APIDeal.h`, `MT5APITick.h`, `MT5APIPosition.h`, `Config\MT5APIConfigSymbol.h`, `Include\Classes\MT5APIMath.h`, and the installed Python `MT5Manager` type stubs.
- This audit uses the installed SDK as a field/API reference. It does not claim a live broker connection or validate broker-specific symbol configuration.

### Confirmed field mappings and fixes

- `IMTDeal::TimeMsc` and `MTTickShort::datetime_msc` provide the precise timestamps needed for Anti-Fraud latency comparisons. A candidate without a precise deal open time or valid bid/ask tick coverage is returned as `UNCHECKED`, not as a positive event.
- `IMTDeal::EnDealAction` uses `DEAL_AGENT_DAILY = 10`, `DEAL_AGENT_MONTHLY = 11`, and `DEAL_AGENT = 18`. The engine now recognizes all three as agent commission postings. `DEAL_INTERESTRATE = 12` is also mapped to the SDK name.
- `Profit = 0` is a valid reported value on an agent posting. The `Commission` field is read only if `Profit` is missing or null.
- SDK `Volume` uses **10,000 units per lot**; `VolumeExt` uses **100,000,000 units per lot**. Anti-Fraud position/deal calculations and the shared position cache now use the correct extended scale. Reports reading `VolumeExt` without `Volume` convert it back to legacy volume units before their existing lot calculation.
- `IMTPosition` exposes `ContractSize`, `PriceCurrent`, and `RateProfit`; symbol configuration exposes `CurrencyProfit`. Anti-Fraud exposure uses these existing fields to express notional in account currency where the conversion is known. It does not emit a volume/exposure alert when conversion, contract size, current price, or positive capital is missing.
- The existing Anti-Fraud route uses the project's `yielding_history_manager_session` proxy, which serializes SDK calls on the one configured persistent Manager connection. No second Manager connection was added.

### Verification and limits

- Automated checks cover exact/missing tick times, HFT classification, funding correlation and exclusions, concentration boundaries, exposure/currency-data gaps, `Profit = 0`, agent action 18, and `VolumeExt` conversion.
- Runtime MT5, group, tick, account, and P&L values cannot be verified while the RMS server is intentionally stopped or the configured Manager is unavailable.
- A broker may use a profit currency different from the deposit currency; a valid `RateProfit` or matching currency pair is required before Anti-Fraud compares calculated exposure with account equity/balance.

## Real-time architecture

### Executive result

The application uses the configured MT5 Manager identity on the backend. Browser-supplied MT5 credentials are compatibility fields and are ignored. Full-snapshot screens read atomic in-memory generations produced by the persistent Manager connection. In focused mode, Live Monitor reads the selected group scope through the same Manager connection and caches completed results briefly. Historical screens query Manager report/history APIs. When authoritative account metrics are missing in focused Live Monitor, equity and free margin use user balance plus credit as an estimate and the API reports the number of estimated accounts.

The 2026-08-01 change adds durable book-shift evidence and eliminates repeat performance-history scans when changing period tabs. The first P&L history scan is still bounded by the Manager server and selected history/group scope (the current UI requests 31 days); after completion Daily, Weekly, Monthly and Overall are instant views of one cached verified result.

### Data path and timing

1. `MT5ConnectionManager` maintains one persistent Manager API connection with user, group, symbol, position, order and time pump modes where the installed SDK exposes them.
2. A complete core generation is staged and published atomically. If any required account or position request is incomplete, the old verified generation remains visible; partial generations are not published.
3. Default source refreshes are positions 1 second, users/account equity 5 seconds, panels 2 seconds, groups 30 seconds, ticks 0.2 seconds in batches, symbols 60 seconds, and watchdog 10 seconds. Environment variables can tune these within code-enforced minimums.
4. Full-snapshot live endpoints filter the cached generation in memory. Focused Live Monitor performs scoped Manager reads and uses a short cache; its freshness depends on the number and size of selected groups.
5. Historical endpoints use MT5 Deals, Daily and Trades Summary report calls. These can be slower because the Manager server must scan the requested date range. Results are cached and clearly expose warnings/data-quality counts.
6. The frontend de-duplicates identical concurrent GET calls. Pages are lazy-loaded into separate production chunks.

“Real time” therefore means latest successfully published Manager snapshot, not invented tick-by-tick precision. The API exposes generation timestamps/status so stale or initializing states can be labelled.

### Tabs and sections

#### Dealing and Risk

- **Live Monitor:** KPIs, account risk, open positions and symbol exposure. The route prefers bulk Manager account metrics for all scoped accounts. If metrics are missing, estimated account counts and a warning are shown. Group and excluded-login filters apply before aggregation.
- **P&L Leaders:** live floating winners/losers use the current position cache. Realized Daily/Weekly/Monthly/Overall uses closing trade deals only; net realized P&L is Profit + Commission + Fee + Storage/Swap. Boundaries are UTC. Overall is a rolling 31-day window in the current UI. Focused mode reads its group catalog without waiting for an account-wide snapshot, publishes the one-day result first, then fills the 31-day result in the background. Deals without a usable close timestamp are counted in data quality but excluded from rankings.
- **Analyser:** selected-client history, behavior, trade duration, symbol performance and equity curve. Network evidence distinguishes journal-proven trade IPs from explicitly labelled last-login IP fallback.
- **Anti-Fraud:** configured detectors evaluate real account/deal/position/session evidence. Server cache age and refresh state are shown. Missing equity is not replaced with balance.
- **Risk Controls:** stress shocks real position notional and authoritative account equity; accounts without authoritative equity are excluded with a warning.

#### Manager

Shows the persistent connection generation: configured groups, accounts, positions, balance, equity, margin, free margin, floating P&L, summary and exposure panels. Totals include scoped accounts without positions. Financial fields are required for an atomic user generation.

#### Market Watch

Shows configured symbols plus latest Manager ticks. Symbol metadata refreshes separately from fast batched ticks. Tick source is reported as cached `TickLast` or unavailable; prices are not synthesized.

#### Operations

Provides live operational detectors/events, cases and history, review actions, exposure limits, acknowledgements, notification channels, audit records, CID/session evidence, intelligence enrichment and daily reconciliation. Writes are persisted in SQLite and administrative actions are attributed to the authenticated operator.

#### Risk Automation

Defines and simulates rules, runs evaluations, handles approvals and manual overrides, and shows execution history. Actions go through the Manager API and retain before/after evidence including account equity where available. Simulation does not mutate MT5.

#### PnL

Runs a deal report for selected groups and dates and supports export. Trade counts are based on actual MT5 deal semantics; warnings identify incomplete/unsupported Manager calls. Cached report payloads can be reused for export to avoid a second history scan.

#### Brokerage

- Filters: opening/closing equity dates, summary dates, exact selected groups, group-pattern rules, excluded logins and optional negative-equity clipping.
- Opening/closing equity uses MT5 Daily records. Explicit Equity wins when supplied; otherwise the official Daily report mapping uses `ProfitEquity`.
- Daily cash flow separates positive DailyBalance deposits, negative DailyBalance withdrawals, DailyCredit and DailyBonus.
- Broker P&L rule: `closing equity - opening equity - deposits - credit - bonus + withdrawals`.
- Net percentage divides broker P&L by positive opening equity; otherwise it is zero.
- Summary Equity, winners/losers, daily snapshot and Trades Summary all come from the same selected scope. Empty Manager results remain empty and are never populated with samples.
- Report execution is user-controlled because arbitrary historical ranges can be expensive; changing filters marks the displayed result stale until Run Report is used.

#### Controls

- Manager status and configured backend identity.
- A Book, B Book, Cent and Hybrid exact group presets. Saving one group into a book removes it from the other book presets.
- Telegram routes and alert thresholds.
- **Book shifting:** polls durable movement evidence every second. The backend observes every new atomic user generation (default equity/group detection latency up to about five seconds), compares login membership to its prior stored state, and records manager login, client login/name, old/new group, old/new configured book, equity at shift, Manager generation time and detection time. Initial discovery establishes a baseline and does not create a false movement. Group changes are recorded even when both groups map to the same book; A-to-B and B-to-A are therefore included. Equity is the authoritative account Equity in the first complete generation showing the new group.

### Accuracy and failure rules

- No fake/sample financial data is present in these paths.
- Required live account fields are Balance, Equity, Margin, Free Margin and Floating P&L. An incomplete bulk snapshot is rejected atomically.
- Position live P&L may update faster than account equity, but it is kept in a separate `Live Floating PnL` field and never overwrites authoritative Equity.
- Group membership is exact and case-preserving. Configured groups and observed user groups are merged into the catalog.
- Historical realized results include closing trade postings; wrappers that omit Entry are accepted only when non-zero Profit proves a realized posting.
- Stale cache, offline Manager, unsupported SDK capabilities and empty report results are surfaced as status/warnings rather than silently substituted.
- Book-shift accuracy is polling-based: the captured value is the first complete snapshot after the group change. MT5 does not provide an atomic “equity at the exact database update microsecond” through the polling wrapper, so this is the closest auditable real-time value and its source generation is stored.

### Historical verification performed

These results are retained from the earlier audit and were not rerun during this documentation consolidation.

- Focused backend suite: book shifts, performance leaders, financial integrity and group enumeration: 16 passed.
- Frontend production build: passed (2,366 modules transformed).
- New automated scenario: manager 1001, client 100112, `real/a` to `real/b`, captures A Book to B Book with exact snapshot equity 1175.25.

### Operational recommendations

- Keep Manager and web server clocks synchronized to UTC/NTP.
- Keep the backend process continuously running; shift detection cannot observe changes made entirely during downtime.
- Assign every production group to exactly one configured book to avoid `unassigned` classifications.
- For faster initial historical reports, narrow dates/groups where business requirements permit. Live panels should not be changed to synchronous native calls because that would make every browser refresh wait on MT5.

## API endpoint reference

Extracted from route decorators in `api.py`. All `/api` requests are subject to middleware; health/readiness/login exceptions and administrative checks are implemented in the source. A listed endpoint is not evidence of authorization for every user. Request details follow in the model reference. Handler names identify the implementation for developers.

| Method | Path | Handler | Request model |
|---|---|---|---|
| GET | `/api/health` | `health` | `?` |
| GET | `/api/ready` | `readiness` | `?` |
| POST | `/api/manager/connect` | `manager_connect` | `ManagerConnectRequest` |
| POST | `/api/manager/disconnect` | `manager_disconnect` | `?` |
| GET | `/api/manager/status` | `manager_status` | `?` |
| GET | `/api/managers` | `list_managers` | `?` |
| GET | `/api/manager/dashboard` | `manager_dashboard` | `?` |
| GET | `/api/manager/market-watch` | `manager_market_watch` | `?` |
| GET | `/api/manager/accounts` | `manager_accounts` | `?` |
| GET | `/api/settings/books` | `get_book_group_settings` | `?` |
| PUT | `/api/settings/books` | `update_book_group_settings` | `BookGroupSettings` |
| POST | `/api/settings/reset-database` | `reset_application_database` | `DatabaseResetRequest` |
| GET | `/api/settings/book-shifts` | `get_book_shift_events` | `?` |
| GET | `/api/book-pnl` | `get_book_pnl` | `?` |
| GET | `/api/bbook-pnl` | `get_bbook_pnl` | `?` |
| POST | `/api/auth/login` | `login` | `LoginRequest` |
| GET | `/api/auth/session` | `auth_session` | `?` |
| POST | `/api/auth/change-password` | `change_password` | `PasswordChangeRequest` |
| POST | `/api/auth/logout` | `logout` | `?` |
| POST | `/api/mt5/connect` | `connect_mt5` | `MT5Credentials` |
| POST | `/api/mt5/groups` | `fetch_groups` | `MT5Credentials` |
| POST | `/api/dealer-command-center` | `dealer_command_center` | `GroupScopedRequest` |
| POST | `/api/risk/live` | `get_live_risk` | `LiveRiskRequest` |
| POST | `/api/risk/alerts/notice` | `notice_live_risk_alert` | `AlertAcknowledgeRequest` |
| POST | `/api/risk/performance` | `get_risk_performance` | `PerformanceRequest` |
| GET | `/api/integrations/google-sheets/status` | `google_sheets_status` | `?` |
| POST | `/api/integrations/google-sheets/sync` | `sync_google_sheets` | `GoogleSheetsSyncRequest` |
| GET | `/api/notifications/telegram/status` | `telegram_status` | `?` |
| PUT | `/api/notifications/telegram/settings` | `update_telegram_settings` | `TelegramAlertSettings` |
| POST | `/api/notifications/telegram/test` | `telegram_test` | `TelegramTestRequest` |
| POST | `/api/risk/analyse` | `analyse_client_patterns` | `ClientPatternRequest` |
| POST | `/api/risk/client-live` | `get_client_live` | `ClientLiveRequest` |
| POST | `/api/risk/antifraud` | `get_antifraud_snapshot` | `AntiFraudRequest` |
| POST | `/api/risk/stress` | `get_stress_snapshot` | `StressTestRequest` |
| POST | `/api/reports/deal` | `get_deal_report` | `DealReportRequest` |
| POST | `/api/reports/deal/export` | `export_deal_report` | `DealExportRequest` |
| POST | `/api/reports/brokerage` | `get_brokerage_report` | `BrokerageReportRequest` |
| POST | `/api/reports/diff` | `get_diff_report` | `BrokerageReportRequest` |
| POST | `/analytics` | `get_analytics` | `AnalyticsRequest` |
| POST | `/api/analytics` | `get_analytics` | `AnalyticsRequest` |
| POST | `/api/reports/brokerage/export` | `export_brokerage_report` | `BrokerageExportRequest` |
| GET | `/api/risk-automation/overview` | `risk_automation_overview` | `?` |
| POST | `/api/risk-automation/rules` | `create_risk_rule` | `RiskRuleRequest` |
| PUT | `/api/risk-automation/rules/{rule_id}` | `update_risk_rule` | `RiskRuleRequest` |
| DELETE | `/api/risk-automation/rules/{rule_id}` | `delete_risk_rule` | `?` |
| POST | `/api/risk-automation/rules/{rule_id}/simulate` | `simulate_risk_rule` | `?` |
| POST | `/api/risk-automation/run` | `run_risk_automation` | `?` |
| POST | `/api/risk-automation/approvals/{approval_id}` | `decide_risk_approval` | `RiskApprovalDecision` |
| POST | `/api/risk-automation/manual-override` | `manual_risk_override` | `RiskManualOverride` |
| POST | `/api/operations/live` | `operations_live` | `OperationsRequest` |
| GET | `/api/operations/cases` | `list_cases` | `?` |
| GET | `/api/operations/risk-events` | `list_risk_events` | `?` |
| GET | `/api/operations/risk-events/{event_id}` | `risk_event_detail` | `?` |
| POST | `/api/operations/risk-events/{event_id}/status` | `update_risk_event_status` | `RiskEventActionRequest` |
| POST | `/api/operations/cid-audit` | `operation_cid_audit` | `CidAuditRequest` |
| POST | `/api/operations/cases` | `create_case` | `CaseCreateRequest` |
| PUT | `/api/operations/cases/{case_id}` | `update_case` | `CaseUpdateRequest` |
| DELETE | `/api/operations/cases/{case_id}` | `delete_case` | `?` |
| GET | `/api/operations/cases/{case_id}/history` | `case_history` | `?` |
| POST | `/api/operations/reviews` | `save_dealer_review` | `DealerReviewRequest` |
| GET | `/api/operations/limits` | `list_limits` | `?` |
| POST | `/api/operations/limits` | `create_limit` | `ExposureLimitRequest` |
| DELETE | `/api/operations/limits/{limit_id}` | `delete_limit` | `?` |
| POST | `/api/operations/alerts/acknowledge` | `acknowledge_alert` | `AlertAcknowledgeRequest` |
| GET | `/api/operations/audit` | `audit_log` | `?` |
| GET | `/api/operations/channels` | `list_channels` | `?` |
| POST | `/api/operations/channels` | `create_channel` | `NotificationChannelRequest` |
| DELETE | `/api/operations/channels/{channel_id}` | `delete_channel` | `?` |
| POST | `/api/operations/reconciliation/run` | `run_reconciliation` | `?` |
| GET | `/api/operations/reconciliation` | `reconciliation_history` | `?` |
| POST | `/api/operations/intelligence` | `operations_intelligence` | `IntelligenceRequest` |
| GET | `/api/operations/intelligence/{job_id}` | `operations_intelligence_status` | `?` |
| WEBSOCKET | `/api/operations/ws` | `operations_websocket` | `?` |
| GET | `/` | `serve_frontend_index` | `?` |
| GET | `/{full_path:path}` | `serve_frontend_spa` | `?` |

## Request model reference

Fields and defaults are extracted from the source, including inherited request fields through the named base class. `Field(...)` constraints are shown as declared. Compatibility credentials remain server-controlled. Secrets in requests must never be logged.

### LoginRequest

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `username` | `str` | `Field(min_length=1, max_length=120)` |
| `password` | `str` | `Field(min_length=1, max_length=1024)` |

### PasswordChangeRequest

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `current_password` | `str` | `Field(min_length=1, max_length=1024)` |
| `new_password` | `str` | `Field(min_length=12, max_length=1024)` |

### MT5Credentials

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `server` | `str` | `''` |
| `login` | `int` | `0` |
| `password` | `str` | `''` |

### GroupScopedRequest

Base: `MT5Credentials`

| Field | Type | Default / validation |
|---|---|---|
| `selected_groups` | `list[str]` | `Field(default_factory=list)` |
| `excluded_logins` | `list[int]` | `Field(default_factory=list, max_length=10000)` |
| `group_filter_rules` | `str` | `Field(default='', max_length=20000)` |
| `profile_name` | `str &#124; None` | `Field(default=None, max_length=160)` |

### DealReportRequest

Base: `GroupScopedRequest`

| Field | Type | Default / validation |
|---|---|---|
| `from_date` | `date` | `Required` |
| `to_date` | `date` | `Required` |

### DealExportRequest

Base: `DealReportRequest`

| Field | Type | Default / validation |
|---|---|---|
| `cached_report` | `dict[str, Any] &#124; None` | `None` |

### BrokerageReportRequest

Base: `GroupScopedRequest`

| Field | Type | Default / validation |
|---|---|---|
| `opening_equity_date` | `date` | `Required` |
| `closing_equity_date` | `date` | `Required` |
| `date_from` | `date &#124; None` | `None` |
| `date_to` | `date &#124; None` | `None` |
| `clip_negative_equity` | `bool` | `False` |

### BrokerageExportRequest

Base: `BrokerageReportRequest`

| Field | Type | Default / validation |
|---|---|---|
| `cached_report` | `dict[str, Any] &#124; None` | `None` |

### AnalyticsRequest

Base: `GroupScopedRequest`

| Field | Type | Default / validation |
|---|---|---|
| `opening_equity_date` | `date &#124; None` | `None` |
| `closing_equity_date` | `date &#124; None` | `None` |
| `date_from` | `date &#124; None` | `None` |
| `date_to` | `date &#124; None` | `None` |
| `period` | `str &#124; None` | `None` |
| `groups` | `list[str]` | `Field(default_factory=list)` |

### LiveRiskRequest

Base: `GroupScopedRequest`

| Field | Type | Default / validation |
|---|---|---|

### PerformanceRequest

Base: `GroupScopedRequest`

| Field | Type | Default / validation |
|---|---|---|
| `lookback_days` | `int` | `Field(default=365, ge=31, le=3650)` |

### DatabaseResetRequest

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `password` | `str` | `Field(min_length=1, max_length=128)` |
| `confirmation` | `str` | `Field(min_length=1, max_length=64)` |

### ClientPatternRequest

Base: `GroupScopedRequest`

| Field | Type | Default / validation |
|---|---|---|
| `client_login` | `int` | `Required` |
| `from_date` | `date` | `Required` |
| `to_date` | `date` | `Required` |
| `scalping_seconds` | `int` | `Field(default=180, ge=1, le=3600)` |

### ClientLiveRequest

Base: `GroupScopedRequest`

| Field | Type | Default / validation |
|---|---|---|
| `client_login` | `int` | `Required` |

### AntiFraudRequest

Base: `GroupScopedRequest`

| Field | Type | Default / validation |
|---|---|---|
| `refresh_token` | `int` | `Field(default=0, ge=0)` |
| `lookback_hours` | `int` | `Field(default=24, ge=1, le=168)` |
| `gap_seconds` | `float` | `Field(default=3.0, ge=0.1, le=300)` |
| `profit_threshold` | `float` | `Field(default=10.0, ge=0)` |
| `check_total_realized_pnl` | `bool` | `False` |
| `minimum_total_realized_pnl` | `float` | `0.0` |
| `maximum_duration_minutes` | `float` | `Field(default=5.0, ge=0.1, le=1440)` |
| `scalp_seconds` | `int` | `Field(default=180, ge=1, le=3600)` |
| `scalp_sequence_min` | `int` | `Field(default=3, ge=1, le=1000)` |
| `sharp_profit_threshold` | `float` | `Field(default=100.0, ge=0)` |
| `concentration_min_trades` | `int` | `Field(default=3, ge=2, le=1000)` |
| `large_order_lots` | `float` | `Field(default=1.0, ge=0)` |
| `large_account_lots` | `float` | `Field(default=5.0, ge=0)` |
| `large_order_exposure_ratio` | `float` | `Field(default=1.0, ge=0)` |
| `large_account_exposure_ratio` | `float` | `Field(default=5.0, ge=0)` |
| `mini_equity_threshold` | `float` | `100.0` |
| `stopout_margin_level` | `float` | `Field(default=100.0, ge=0)` |
| `news_window_minutes` | `float` | `Field(default=5.0, ge=0.1, le=120)` |
| `excluded_symbols` | `list[str]` | `Field(default_factory=list)` |

### RiskEventActionRequest

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `status` | `Literal['NEW', 'ACKNOWLEDGED', 'UNDER_REVIEW', 'ESCALATED', 'RESOLVED', 'CLOSED']` | `Required` |
| `assigned_to` | `str &#124; None` | `Field(default=None, max_length=120)` |
| `note` | `str` | `Field(default='', max_length=4000)` |
| `case_id` | `int &#124; None` | `Field(default=None, ge=1)` |

### StressTestRequest

Base: `GroupScopedRequest`

| Field | Type | Default / validation |
|---|---|---|
| `shock_percent` | `float` | `Field(default=-5.0, ge=-50.0, le=50.0)` |
| `margin_level_floor` | `float` | `Field(default=120.0, ge=0)` |
| `concentration_limit` | `float` | `Field(default=70.0, ge=0, le=100)` |
| `symbol_gross_limit` | `float` | `Field(default=1000000.0, ge=0)` |

### TelegramTestRequest

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `message` | `str` | `'MT5 Brokerage Telegram notifications are connected.'` |
| `route` | `str` | `Field(default='default', pattern='^(default&#124;vps&#124;scalpers&#124;win_rate&#124;risk&#124;antifraud)$')` |

### TelegramAlertSettings

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `enabled` | `bool` | `Required` |
| `risk_alerts` | `bool` | `Required` |
| `antifraud_alerts` | `bool` | `Required` |
| `profit_alert_pct` | `float` | `Field(ge=0, le=10000)` |
| `double_profit_alert_pct` | `float` | `Field(ge=0, le=10000)` |
| `high_lot_threshold` | `float` | `Field(gt=0, le=100000)` |
| `account_lot_threshold` | `float` | `Field(gt=0, le=100000)` |
| `channel_routes` | `dict[str, str]` | `Field(default_factory=dict)` |

### GoogleSheetsSyncRequest

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `live_snapshot` | `dict[str, Any]` | `Field(default_factory=dict)` |

### BookGroupSettings

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `a_book` | `list[str]` | `Field(default_factory=list, max_length=2000)` |
| `b_book` | `list[str]` | `Field(default_factory=list, max_length=2000)` |
| `cent` | `list[str]` | `Field(default_factory=list, max_length=2000)` |
| `hybrid` | `list[str]` | `Field(default_factory=list, max_length=2000)` |

### OperationsRequest

Base: `GroupScopedRequest`

| Field | Type | Default / validation |
|---|---|---|

### CidAuditRequest

Base: `MT5Credentials`

| Field | Type | Default / validation |
|---|---|---|
| `lookback_hours` | `int` | `Field(default=24, ge=1, le=24 * 365 * 30)` |
| `from_date` | `date &#124; None` | `None` |
| `to_date` | `date &#124; None` | `None` |
| `all_history` | `bool` | `False` |

### CaseCreateRequest

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `client_login` | `int` | `Field(gt=0)` |
| `title` | `str` | `Field(min_length=2, max_length=200)` |
| `assigned_to` | `str` | `Field(default='', max_length=120)` |
| `status` | `str` | `Field(default='open', pattern='^(open&#124;investigating&#124;monitoring&#124;resolved&#124;closed)$')` |
| `priority` | `str` | `Field(default='medium', pattern='^(low&#124;medium&#124;high&#124;critical)$')` |
| `summary` | `str` | `Field(default='', max_length=10000)` |
| `evidence` | `list[dict[str, Any]]` | `Field(default_factory=list, max_length=500)` |

### CaseUpdateRequest

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `assigned_to` | `str &#124; None` | `Field(default=None, max_length=120)` |
| `status` | `str &#124; None` | `Field(default=None, pattern='^(open&#124;investigating&#124;monitoring&#124;resolved&#124;closed)$')` |
| `priority` | `str &#124; None` | `Field(default=None, pattern='^(low&#124;medium&#124;high&#124;critical)$')` |
| `summary` | `str &#124; None` | `Field(default=None, max_length=10000)` |
| `evidence` | `list[dict[str, Any]] &#124; None` | `Field(default=None, max_length=500)` |
| `note` | `str` | `Field(default='', max_length=10000)` |

### ExposureLimitRequest

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `scope_type` | `str` | `Field(pattern='^(account&#124;group&#124;symbol)$')` |
| `scope_value` | `str` | `Field(min_length=1, max_length=160)` |
| `metric` | `str` | `Field(pattern='^(gross_lots&#124;floating_loss&#124;gross_exposure&#124;margin_level_below&#124;concentration_pct)$')` |
| `threshold` | `float` | `Field(gt=0, le=1000000000000)` |
| `warning_pct` | `float` | `Field(default=80, ge=1, le=100)` |
| `escalation_level` | `str` | `Field(default='warning', pattern='^(info&#124;warning&#124;high&#124;critical)$')` |
| `enabled` | `bool` | `True` |

### AlertAcknowledgeRequest

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `alert_key` | `str` | `Field(min_length=3, max_length=300)` |

### NotificationChannelRequest

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `name` | `str` | `Field(min_length=2, max_length=120)` |
| `channel_type` | `str` | `Field(pattern='^(telegram&#124;email&#124;webhook)$')` |
| `destination` | `str` | `Field(default='', max_length=1000)` |
| `min_severity` | `str` | `Field(default='warning', pattern='^(info&#124;warning&#124;high&#124;critical)$')` |
| `enabled` | `bool` | `True` |

### IntelligenceRequest

Base: `GroupScopedRequest`

| Field | Type | Default / validation |
|---|---|---|
| `lookback_days` | `int` | `Field(default=90, ge=7, le=730)` |

### DealerReviewRequest

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `client_login` | `int` | `Field(gt=0)` |
| `decision` | `str` | `Field(max_length=120)` |
| `notes` | `str` | `Field(default='', max_length=10000)` |

### RiskCondition

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `type` | `str` | `Field(default='custom', min_length=2, max_length=80)` |
| `field` | `str` | `Field(default='', max_length=120)` |
| `operator` | `str` | `Field(pattern='^(gt&#124;gte&#124;lt&#124;lte&#124;equals&#124;not_equals&#124;contains&#124;not_contains&#124;in&#124;not_in)$')` |
| `value` | `Any` | `Required` |
| `window_days` | `int` | `Field(default=0, ge=0, le=3650)` |

### RiskAction

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `type` | `str` | `Field(pattern='^(move_group&#124;disable_trading&#124;disable_account&#124;read_only&#124;restrict_symbols&#124;restrict_lot_size&#124;set_leverage&#124;add_label&#124;monitoring_list&#124;manual_review)$')` |
| `value` | `Any` | `None` |
| `target_group` | `str` | `Field(default='', max_length=240)` |
| `symbols` | `list[str]` | `Field(default_factory=list, max_length=200)` |

### RiskRuleRequest

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `name` | `str` | `Field(min_length=2, max_length=160)` |
| `description` | `str` | `Field(default='', max_length=2000)` |
| `enabled` | `bool` | `False` |
| `priority` | `int` | `Field(default=100, ge=1, le=10000)` |
| `source_group` | `str` | `Field(default='*', min_length=1, max_length=240)` |
| `target_group` | `str` | `Field(default='', max_length=240)` |
| `condition_logic` | `str` | `Field(default='AND', pattern='^(AND&#124;OR)$')` |
| `conditions` | `list[RiskCondition]` | `Field(min_length=1, max_length=20)` |
| `actions` | `list[RiskAction]` | `Field(min_length=1, max_length=20)` |
| `alert_events` | `list[str]` | `Field(default_factory=lambda: ['triggered', 'executed', 'failed', 'approval_required', 'reverted'], max_length=20)` |
| `delay_minutes` | `int` | `Field(default=0, ge=0, le=43200)` |
| `require_approval` | `bool` | `False` |
| `revert_when_clear` | `bool` | `False` |
| `simulation_mode` | `bool` | `True` |
| `cooldown_minutes` | `int` | `Field(default=60, ge=0, le=525600)` |
| `excluded_logins` | `list[int]` | `Field(default_factory=list, max_length=10000)` |

### RiskApprovalDecision

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `decision` | `str` | `Field(pattern='^(approved&#124;rejected)$')` |
| `note` | `str` | `Field(default='', max_length=4000)` |

### RiskManualOverride

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `client_login` | `int` | `Field(gt=0)` |
| `client_name` | `str` | `Field(default='', max_length=160)` |
| `current_group` | `str` | `Field(default='', max_length=240)` |
| `actions` | `list[RiskAction]` | `Field(min_length=1, max_length=20)` |
| `reason` | `str` | `Field(min_length=3, max_length=4000)` |

### ManagerConnectRequest

Base: `BaseModel`

| Field | Type | Default / validation |
|---|---|---|
| `server` | `str` | `Required` |
| `login` | `int` | `Required` |
| `password` | `str` | `Required` |

## Environment reference

Values below come from the checked-in `.env.example`, not private runtime `.env`. Examples are setup placeholders, not live broker data. Actual defaults can differ in source. Restart after code changes and verify health/readiness. Configure secrets privately.

| Variable | Example | Configuration notes |
|---|---|---|
| `MT5_MANAGER_SERVER` | `127.0.0.1:443` | Copy these values into .env to enable Telegram anti-fraud alerts. |
| `MT5_MANAGER_LOGIN` | `1000` |  |
| `MT5_MANAGER_PASSWORD` | `Set privately` |  |
| `MT5_MANAGER_AUTOCONNECT` | `true` |  |
| `MT5_USER_REFRESH_SECONDS` | `10` | One Manager connection is shared by all API routes and background workers. |
| `MT5_POSITION_REFRESH_SECONDS` | `2` |  |
| `MT5_TICK_REFRESH_SECONDS` | `0.5` |  |
| `MT5_TICK_REFRESH_BATCH_SIZE` | `100` |  |
| `MT5_TICK_LIVE_MAX_AGE_SECONDS` | `30` |  |
| `MT5_PANELS_REFRESH_SECONDS` | `2` |  |
| `MT5_FULL_SNAPSHOT_ENABLED` | `true` |  |
| `MT5_DEFAULT_LIVE_GROUP` | `(empty)` | Optional populated group to open first in the live-risk UI. |
| `MT5_LIVE_RISK_CACHE_SECONDS` | `60` |  |
| `MT5_LIVE_RISK_IP_LOOKUP_LIMIT` | `100` |  |
| `ANTIFRAUD_CACHE_SECONDS` | `300` |  |
| `ANTIFRAUD_MAX_TICK_CHECKS` | `3` |  |
| `ANTIFRAUD_BACKGROUND_SCAN_ENABLED` | `true` |  |
| `ANTIFRAUD_BACKGROUND_SCAN_SECONDS` | `300` |  |
| `MT5_GROUP_REFRESH_SECONDS` | `30` |  |
| `MT5_WATCHDOG_SECONDS` | `10` |  |
| `MT5_RECONNECT_BACKOFF_SECONDS` | `5` |  |
| `TELEGRAM_BOT_TOKEN` | `Set privately` |  |
| `TELEGRAM_CHAT_ID` | `replace_with_target_chat_id` |  |
| `TELEGRAM_ALERTS_ENABLED` | `true` |  |
| `TELEGRAM_RISK_ALERTS_ENABLED` | `true` |  |
| `TELEGRAM_ANTIFRAUD_ALERTS_ENABLED` | `true` |  |
| `BBOOK_SNAPSHOT_INTERVAL_SECONDS` | `3600` |  |
| `CENT_ACCOUNT_DIVISOR` | `100` |  |
| `PROFIT_ALERT_PCT_THRESHOLD` | `30` |  |
| `DOUBLE_PROFIT_PCT_THRESHOLD` | `100` |  |
| `HIGH_LOT_ALERT_THRESHOLD` | `1.0` |  |
| `ACCOUNT_LOT_ALERT_THRESHOLD` | `5.0` |  |
| `ALERT_DEDUPE_RETENTION_SECONDS` | `86400` |  |
| `LOW_DEPOSIT_ALERT_THRESHOLD` | `500` |  |
| `LOW_DEPOSIT_HIGH_PROFIT_MIN` | `200` |  |
| `LOW_DEPOSIT_PROFIT_MULTIPLE` | `1.5` |  |
| `MT5_SERVER_LOG_DIRS` | `C:\\MT5Server\\Logs;D:\\MT5Archive\\Logs` | Optional trade-IP evidence. Separate multiple MT5 server log directories with a semicolon on Windows. These must be server connection/request journals, not Manager terminal logs or access-server connectivity logs. |
| `IP_GEOLOCATION_ENABLED` | `true` |  |
| `PERFORMANCE_CACHE_SECONDS` | `60` | Caches historical P&L leader scans; the current frontend requests 31 days. Live floating P&L remains live. |
| `RUNTIME_MAINTENANCE_INTERVAL_SECONDS` | `1800` | Every 30 minutes, clear derived report/view caches, prune expired lookup and alert-deduplication entries, optimize SQLite, and remove stale app launcher logs older than 24 hours. Dated MT5 journals are retained for seven days; the live Manager data generation is always preserved. |
| `APP_LOG_RETENTION_HOURS` | `24` |  |
| `MT5_JOURNAL_RETENTION_DAYS` | `7` |  |
| `AUDIT_RETENTION_DAYS` | `90` |  |
| `APP_SESSION_SECRET` | `Set privately` | Optional API configuration Use a long random value and keep it stable across backend restarts/replicas. |
| `APP_SECRET_SALT` | `Set privately` |  |
| `SESSION_TTL_SECONDS` | `2592000` |  |
| `SESSION_COOKIE_SECURE` | `false` | Set true only when the application is served over HTTPS. |
| `LOGIN_RATE_LIMIT_ATTEMPTS` | `10` |  |
| `LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `900` |  |
| `LOGIN_RATE_LIMIT_MAX_KEYS` | `10000` |  |
| `ALLOWED_ORIGINS` | `(empty)` | Comma-separated browser origins. Leave empty when frontend and API are same-origin. |
| `GOOGLE_SHEETS_ENABLED` | `false` | Optional Google Sheets operational-data sync. Create a Google Cloud service account, enable the Google Sheets API, and share the spreadsheet with that service account's client_email as an Editor. Keep the downloaded JSON key outside source control and point GOOGLE_SERVICE_ACCOUNT_FILE at it. |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | `YOUR_SPREADSHEET_ID` |  |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | `C:\\secure\\mt5-rms-google-service-account.json` |  |
| `GOOGLE_SHEETS_AUTO_SYNC_ENABLED` | `true` | Keep the operational workbook current without creating duplicate rows. The default scope uses MT5_DEFAULT_LIVE_GROUP.  Use a comma-separated list here only when the sheet should track a different, explicitly bounded scope. |
| `GOOGLE_SHEETS_AUTO_SYNC_SECONDS` | `300` |  |
| `GOOGLE_SHEETS_AUTO_SYNC_GROUPS` | `(empty)` |  |

Additional variables supported by the backend and absent from `.env.example` (inspect source defaults before setting):

`ALLOW_INSECURE_WEBHOOKS`, `BOOK_SHIFT_OBSERVER_SECONDS`, `CID_OBSERVER_INTERVAL_SECONDS`, `DB_PATH`, `ENABLE_API_DOCS`, `LIVE_CACHE_STALE_SECONDS`, `MAX_REQUEST_BODY_BYTES`, `MT5_ENGINE_LOOP_SECONDS`, `MT5_SYMBOL_REFRESH_SECONDS`, `PASSWORD_HASH_ITERATIONS`, `PORT`, `RISK_AUTOMATION_INTERVAL_SECONDS`, `RMS_RESET_PASSWORD`, `SMTP_FROM`, `SMTP_HOST`, `SMTP_PASSWORD`, `SMTP_PORT`, `SMTP_TLS`, `SMTP_USERNAME`

## Database schema reference

SQLite schema declarations below come from initialization code in `api.py` and `risk_automation.py`, not from private production records. Startup may add columns/indexes to existing tables; consult subsequent ALTER/CREATE INDEX statements when upgrading. Use a consistent SQLite backup for a running WAL database; copying only `users.db` during writes can omit recent commits. Preserve schema and data before reset or upgrade.

### app_users

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS app_users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                username        TEXT    NOT NULL UNIQUE,
                password_hash   TEXT    NOT NULL,
                is_admin        INTEGER NOT NULL DEFAULT 0,
                must_change_pw  INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now')));
```

### audit_log

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL DEFAULT 'system',
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL DEFAULT '',
                entity_id TEXT NOT NULL DEFAULT '',
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now')));
```

### investigation_cases

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS investigation_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_login INTEGER NOT NULL,
                title TEXT NOT NULL,
                assigned_to TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                priority TEXT NOT NULL DEFAULT 'medium',
                summary TEXT NOT NULL DEFAULT '',
                evidence_json TEXT NOT NULL DEFAULT '[]',
                created_by TEXT NOT NULL DEFAULT 'system',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')));
```

### case_events

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS case_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL REFERENCES investigation_cases(id) ON DELETE CASCADE,
                action TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT 'system',
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now')));
```

### exposure_limits

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS exposure_limits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type TEXT NOT NULL,
                scope_value TEXT NOT NULL DEFAULT '*',
                metric TEXT NOT NULL,
                threshold REAL NOT NULL,
                warning_pct REAL NOT NULL DEFAULT 80,
                escalation_level TEXT NOT NULL DEFAULT 'warning',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_by TEXT NOT NULL DEFAULT 'system',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')));
```

### alert_acknowledgements

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS alert_acknowledgements (
                alert_key TEXT PRIMARY KEY,
                actor TEXT NOT NULL,
                acknowledged_at TEXT NOT NULL DEFAULT (datetime('now')));
```

### performance_snapshots

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS performance_snapshots (
                cache_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                generated_at TEXT NOT NULL DEFAULT (datetime('now')));
```

### antifraud_snapshots

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS antifraud_snapshots (
                cache_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                generated_at TEXT NOT NULL DEFAULT (datetime('now')));
```

### antifraud_monitor_scopes

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS antifraud_monitor_scopes (
                cache_key TEXT PRIMARY KEY,
                request_json TEXT NOT NULL,
                last_scan_at TEXT,
                last_error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now')));
```

### risk_events

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS risk_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                detector TEXT NOT NULL,
                login INTEGER,
                position_id INTEGER,
                symbol TEXT,
                account_group TEXT,
                severity TEXT NOT NULL,
                confidence TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'NEW',
                source TEXT NOT NULL,
                source_generation TEXT,
                detected_at TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                thresholds_json TEXT NOT NULL,
                actual_values_json TEXT NOT NULL,
                data_quality TEXT NOT NULL,
                acknowledged_by TEXT,
                acknowledged_at TEXT,
                resolved_by TEXT,
                resolved_at TEXT,
                assigned_to TEXT,
                case_id INTEGER);
```

### risk_event_actions

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS risk_event_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')));
```

### google_sheets_toxic_history

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS google_sheets_toxic_history (
                login INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL DEFAULT '',
                account_group TEXT NOT NULL DEFAULT '',
                last_risk_score REAL NOT NULL DEFAULT 0,
                last_severity TEXT NOT NULL DEFAULT 'normal',
                toxic_reason TEXT NOT NULL DEFAULT '',
                observations INTEGER NOT NULL DEFAULT 0,
                first_seen TEXT NOT NULL DEFAULT (datetime('now')),
                last_seen TEXT NOT NULL DEFAULT (datetime('now')),
                last_snapshot_at TEXT NOT NULL DEFAULT '');
```

### notification_channels

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS notification_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                channel_type TEXT NOT NULL,
                destination TEXT NOT NULL DEFAULT '',
                min_severity TEXT NOT NULL DEFAULT 'warning',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')));
```

### daily_reconciliations

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS daily_reconciliations (
                report_date TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                metrics_json TEXT NOT NULL DEFAULT '{}',
                warnings_json TEXT NOT NULL DEFAULT '[]',
                source_updated_at REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')));
```

### book_group_settings

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS book_group_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                a_book_json TEXT NOT NULL DEFAULT '[]',
                b_book_json TEXT NOT NULL DEFAULT '[]',
                cent_json TEXT NOT NULL DEFAULT '[]',
                hybrid_json TEXT NOT NULL DEFAULT '[]',
                updated_by TEXT NOT NULL DEFAULT 'system',
                updated_at TEXT NOT NULL DEFAULT (datetime('now')));
```

### book_membership_state

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS book_membership_state (
                login INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL DEFAULT '',
                account_group TEXT NOT NULL,
                book TEXT NOT NULL DEFAULT 'unassigned',
                equity REAL NOT NULL,
                floating_pnl REAL NOT NULL DEFAULT 0,
                source_updated_at REAL NOT NULL,
                observed_at TEXT NOT NULL DEFAULT (datetime('now')));
```

### book_shift_events

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS book_shift_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manager_login INTEGER NOT NULL,
                login INTEGER NOT NULL,
                client_name TEXT NOT NULL DEFAULT '',
                from_group TEXT NOT NULL,
                to_group TEXT NOT NULL,
                from_book TEXT NOT NULL,
                to_book TEXT NOT NULL,
                equity_at_shift REAL NOT NULL,
                floating_pnl_at_shift REAL NOT NULL DEFAULT 0,
                source_updated_at REAL NOT NULL,
                detected_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(login, from_group, to_group, source_updated_at));
```

### bbook_pnl_snapshots

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS bbook_pnl_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                source_updated_at REAL NOT NULL,
                floating REAL NOT NULL DEFAULT 0,
                closed_swaps_comm REAL NOT NULL DEFAULT 0,
                ib_payout REAL NOT NULL DEFAULT 0,
                account_count INTEGER NOT NULL DEFAULT 0,
                groups_json TEXT NOT NULL DEFAULT '[]',
                UNIQUE(source_updated_at));
```

### book_pnl_snapshots

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS book_pnl_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book TEXT NOT NULL CHECK (book IN ('a_book','b_book','cent','hybrid')),
                captured_at TEXT NOT NULL,
                source_updated_at REAL NOT NULL,
                floating REAL NOT NULL DEFAULT 0,
                balance REAL NOT NULL DEFAULT 0,
                equity REAL NOT NULL DEFAULT 0,
                deposits REAL NOT NULL DEFAULT 0,
                withdrawals REAL NOT NULL DEFAULT 0,
                closed_profit REAL NOT NULL DEFAULT 0,
                swaps REAL NOT NULL DEFAULT 0,
                commissions REAL NOT NULL DEFAULT 0,
                closed_swaps_comm REAL NOT NULL DEFAULT 0,
                ib_payout REAL NOT NULL DEFAULT 0,
                account_count INTEGER NOT NULL DEFAULT 0,
                groups_json TEXT NOT NULL DEFAULT '[]',
                UNIQUE(book, source_updated_at));
```

### book_pnl_daily

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS book_pnl_daily (
                book TEXT NOT NULL CHECK (book IN ('a_book','b_book','cent','hybrid')),
                report_day TEXT NOT NULL,
                deposits REAL NOT NULL DEFAULT 0,
                withdrawals REAL NOT NULL DEFAULT 0,
                net_in_out REAL NOT NULL DEFAULT 0,
                balance REAL NOT NULL DEFAULT 0,
                equity REAL NOT NULL DEFAULT 0,
                closed_profit REAL NOT NULL DEFAULT 0,
                swaps REAL NOT NULL DEFAULT 0,
                commissions REAL NOT NULL DEFAULT 0,
                closed_swaps_comm REAL NOT NULL DEFAULT 0,
                opening_floating REAL NOT NULL DEFAULT 0,
                closing_floating REAL NOT NULL DEFAULT 0,
                floating_delta REAL NOT NULL DEFAULT 0,
                shift_corrections REAL NOT NULL DEFAULT 0,
                ib_payout REAL NOT NULL DEFAULT 0,
                total_book_pnl REAL NOT NULL DEFAULT 0,
                account_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY(book, report_day));
```

### cid_observations

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS cid_observations (
                login INTEGER NOT NULL,
                computer_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                account_group TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                first_seen TEXT NOT NULL DEFAULT (datetime('now')),
                last_seen TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (login, computer_id));
```

### cid_login_events

Declared in `api.py`.

```sql
CREATE TABLE IF NOT EXISTS cid_login_events (
                event_key TEXT PRIMARY KEY,
                occurred_at TEXT NOT NULL,
                login INTEGER NOT NULL,
                computer_id TEXT NOT NULL,
                ip_address TEXT NOT NULL DEFAULT '',
                device_type TEXT NOT NULL DEFAULT '',
                build INTEGER NOT NULL DEFAULT 0,
                access_server TEXT NOT NULL DEFAULT '',
                ping_ms REAL,
                country TEXT NOT NULL DEFAULT '',
                city TEXT NOT NULL DEFAULT '',
                isp TEXT NOT NULL DEFAULT '',
                raw_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')));
```

### risk_automation_rules

Declared in `risk_automation.py`.

```sql
CREATE TABLE IF NOT EXISTS risk_automation_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 0,
                    priority INTEGER NOT NULL DEFAULT 100,
                    source_group TEXT NOT NULL DEFAULT '*',
                    target_group TEXT NOT NULL DEFAULT '',
                    condition_logic TEXT NOT NULL DEFAULT 'AND',
                    conditions_json TEXT NOT NULL DEFAULT '[]',
                    actions_json TEXT NOT NULL DEFAULT '[]',
                    alert_events_json TEXT NOT NULL DEFAULT '[]',
                    delay_minutes INTEGER NOT NULL DEFAULT 0,
                    require_approval INTEGER NOT NULL DEFAULT 0,
                    revert_when_clear INTEGER NOT NULL DEFAULT 0,
                    simulation_mode INTEGER NOT NULL DEFAULT 1,
                    cooldown_minutes INTEGER NOT NULL DEFAULT 60,
                    excluded_logins_json TEXT NOT NULL DEFAULT '[]',
                    created_by TEXT NOT NULL DEFAULT 'system',
                    updated_by TEXT NOT NULL DEFAULT 'system',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL);
```

### risk_rule_states

Declared in `risk_automation.py`.

```sql
CREATE TABLE IF NOT EXISTS risk_rule_states (
                    rule_id INTEGER NOT NULL REFERENCES risk_automation_rules(id) ON DELETE CASCADE,
                    client_login INTEGER NOT NULL,
                    matched INTEGER NOT NULL DEFAULT 0,
                    first_matched_at TEXT,
                    last_evaluated_at TEXT NOT NULL,
                    last_executed_at TEXT,
                    original_group TEXT NOT NULL DEFAULT '',
                    last_values_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(rule_id, client_login));
```

### risk_execution_history

Declared in `risk_automation.py`.

```sql
CREATE TABLE IF NOT EXISTS risk_execution_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id INTEGER REFERENCES risk_automation_rules(id) ON DELETE SET NULL,
                    rule_name TEXT NOT NULL,
                    client_login INTEGER NOT NULL,
                    client_name TEXT NOT NULL DEFAULT '',
                    account_equity REAL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    previous_group TEXT NOT NULL DEFAULT '',
                    new_group TEXT NOT NULL DEFAULT '',
                    detected_json TEXT NOT NULL DEFAULT '{}',
                    actions_json TEXT NOT NULL DEFAULT '[]',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    actor TEXT NOT NULL DEFAULT 'system',
                    created_at TEXT NOT NULL);
```

### risk_approval_queue

Declared in `risk_automation.py`.

```sql
CREATE TABLE IF NOT EXISTS risk_approval_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id INTEGER NOT NULL REFERENCES risk_automation_rules(id) ON DELETE CASCADE,
                    client_login INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    requested_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by TEXT NOT NULL DEFAULT '',
                    decision_note TEXT NOT NULL DEFAULT '');
```

### risk_client_controls

Declared in `risk_automation.py`.

```sql
CREATE TABLE IF NOT EXISTS risk_client_controls (
                    client_login INTEGER PRIMARY KEY,
                    labels_json TEXT NOT NULL DEFAULT '[]',
                    monitoring INTEGER NOT NULL DEFAULT 0,
                    restricted_symbols_json TEXT NOT NULL DEFAULT '[]',
                    max_lot_size REAL,
                    manual_review INTEGER NOT NULL DEFAULT 0,
                    updated_by TEXT NOT NULL DEFAULT 'system',
                    updated_at TEXT NOT NULL);
```

## Verification and maintenance

For a documentation-only update, validate links, section coverage and the remaining documentation files. For an application release, run the backend and frontend checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
Set-Location frontend
npm.cmd run lint
npm.cmd run build
```

Browser tests are in `frontend/e2e/application.spec.js`; supply the documented `E2E_USERNAME` and `E2E_PASSWORD` for an authorized test account. Inspect `frontend/playwright.config.js` for server configuration. Missing test credentials can cause tests to skip; that is not a passing live workflow verification.

Verify `/api/health` for process health and `/api/ready` for actual Manager readiness. Then inspect source age, real group/account/position coverage, symbols/ticks where available, report quality and export consistency. A successful build does not establish financial reconciliation or MT5 connectivity.

Rapid tab switching: old cancelled requests must not update newly selected scopes or clear a newer request's pending/loading state. Command Center, P&L Leaders, Risk Controls and Anti-Fraud contain response ownership/cancellation guards. Capture the precise console/API error if a problem remains; history scans can take much longer than live snapshot reads.

Runtime maintenance clears derived caches and expires lookup/digest entries while retaining the live generation. Log and journal retention is configurable. Audit retention and database backups need an explicit operational policy. The supplied startup scripts do not create a multi-process deployment or guarantee an automatic backup schedule. Keep a restart/restore procedure, verify a consistent backup, and retain audit evidence required by your brokerage.

Global search, Ctrl+K command palette, a five-role RBAC matrix, and every detector from a planning brief should not be assumed implemented. The route inventory, current UI, source availability and detector data status define the available tool.

## Historical consolidation proposal

The following is a retained design proposal, not an instruction to remove application components or a description of completed removals.


No component in this list has been removed. The Book Shifting monitor was relocated from Controls to its own sidebar page; its records and backend service were preserved.

### Low-risk candidates

| Candidate | Why it may be unnecessary | Dependency/risk | Recommendation |
|---|---|---|---|
| Legacy route aliases `/brokerage`, `/diff`, `/dashboard`, `/analytics`, `/equity-report` | They only redirect to current routes and have no sidebar entry | Old bookmarks or external links may still use them | Retain temporarily; remove only after access-log confirmation |
| Controls connection summary | Sidebar already polls and displays the same Manager connection status | Removing it reduces diagnostic detail available to administrators | Keep unless a simpler Controls page is preferred |
| Duplicate manual refresh controls on pages already auto-refreshing | Can appear redundant | Manual refresh is useful after reconnects and during incident response | Keep; clarify automatic versus forced refresh |

### Consolidation candidates requiring workflow approval

| Candidate | Overlap | Risk before removal | Recommendation |
|---|---|---|---|
| Operations Alert Center vs Dealing and Risk alerts | Both surface risk conditions | Operations adds acknowledgement/case workflow; Dealing and Risk is live trading context | Keep both, but link an alert to its Operations case |
| Dealing and Risk P&L Leaders vs PnL report | Both show profit/loss | Leaders are live/rolling rankings; PnL is date-bound report/export | Keep both and use distinct labels |
| Manager exposure panels vs Dealing and Risk Live Monitor | Both aggregate current accounts/positions | Manager is operational Manager API health/book view; Dealing and Risk applies risk logic | Keep both unless dealer roles are formally merged |
| Operations Intelligence vs Client Analyser network panels | Both show IP/provider information | Evidence scopes differ and removal could hide investigation history | Keep; share a common evidence-link component later |
| Telegram controls and Operations Channels | Both configure notifications | Telegram controls are global runtime routes; Channels are stored operational destinations | Consolidate only after migrating all saved destinations and permissions |

### Components that should not be removed

- Stale/offline/data-quality warnings: these prevent old data being mistaken for real time.
- Source timestamps and evidence labels: required for financial and investigation accuracy.
- Group filters and excluded-login rules: necessary to reproduce scoped totals.
- Audit log, cases, acknowledgements and reconciliation history: required for accountability.
- Controls book mapping: required to classify group changes into A/B/Cent/Hybrid books.

### Suggested approval sequence

1. Collect route usage and operator feedback for at least one normal reporting cycle.
2. Approve individual candidates, not a blanket cleanup.
3. Add regression tests for the retained workflow before deletion.
4. Remove UI entry points first while retaining compatibility routes for one release.
5. Remove backend code only after confirming no frontend, export, scheduled job or external consumer calls it.

## Documentation provenance

Consolidated all 11 former Markdown files in `docs`. The former documentation index is replaced by the product overview and contents above. Historical audit verification statements describe their original work, not new tests performed during this consolidation. Current architecture corrections take precedence over dated audit timing and deployment-specific statements.
