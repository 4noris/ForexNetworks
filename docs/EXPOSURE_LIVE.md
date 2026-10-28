# Live Exposure workspace

Open **Dealing and Risk → Exposure**, or `/dealing-risk?tab=exposure`.

The workspace includes buy/sell/net/gross lots, floating P&L, position counts,
symbol-level volume-weighted opening prices and bid/ask quotes, and an expandable
group → client → position hierarchy. Filters select symbol, group, login/name,
direction, configured limit status and history range. Enter locates an exact
login or unique matching client name. Sorting and expansion survive live updates.

Group volume, observed gross/net exposure, observed floating P&L, symbol volume,
top clients and utilization alerts use the same filtered native position book.
Account concentration, account/symbol exposure, and aged/24-hour position totals
remain available below. Daily closed-P&L rankings load when their section opens;
historical deal requests do not run merely to view live exposure.

## Real data and scope

`POST /api/risk/exposure` and the authenticated `exposure` topic on `/api/live/ws`
read the existing immutable native cache. The calculation iterates open positions
and uses the login index; it does not scan or copy inactive account records or
make new SDK requests. Live Monitor's separate 500-row response is not used to
calculate this workspace. Its subscription is paused while Exposure is active.

The shared stream coalesces views with a 250 ms minimum refresh interval. This
is a scheduling interval, not a guaranteed broker-to-browser latency.
The source timestamp is preserved. A reconnect retains an explicitly delayed
same-scope verified view. Missing records produce partial-coverage warnings.

All matching positions contribute to totals before display limits. Expanded
client rows are paginated and bounded to 2,000 returned rows per view; position
details are paginated separately. The footer reports complete totals and visible
detail counts. Narrow a group/login filter when the expanded-client budget is
reached. Tickets and logins are strings in exposure responses, preserving 64-bit
identities in JavaScript.

Floating P&L comes from MT5 position profit plus its reported swap. Commission is
shown only when supplied. No price-based profit estimate or synthetic positions
are used. Prices are weighted by real position lots within each symbol. Bid/ask
timestamps are checked against the existing SDK reference clock with one second
of tolerance for TimeServer's second precision.

Lots and position counts include all matching deposit currencies. Monetary
totals use only the chosen actual account currency; no invented FX conversion or
addition of USD and cent units is performed. Client/position detail shows its
own account currency. Missing monetary fields remain unavailable.

## Saved risk limits

Administrators can add/edit/delete **net_lots** and **gross_lots** policies in the
Limits dialog using existing audited Operations endpoints. Operator views can
read utilization but cannot mutate limits. Settings writes remain HTTP actions,
outside the WebSocket read allowlist.

The absolute threshold is critical at 100%; `warning_pct` determines warning
utilization. `*` supplies a default per client/group/symbol; an exact policy
overrides that metric's default. Individual clients' signed lots are never
offset against other clients for wildcard policies. Unconfigured limits display
**Not set** instead of a fabricated risk status. Utilization is evaluated on the
complete selected book before display filters, so a BUY/SELL filter cannot hide
an existing breach. This workspace monitors configured policy; it does not place
trades or change execution permissions.

## Observed history

The 15-minute, 1-hour, 4-hour and 24-hour selectors show actual observations
collected while the corresponding authorized scope is viewed. No historical
series is generated from current positions. Initially the chart collects data.
Selected-group floating can be compared with the other selected groups under
the same remaining filters and currency.

History is in memory and resets on application restart. It is isolated by user,
Manager source and filters; changing table expansion or sort does not reset it.
Storage is bounded to 32 scopes with recent second observations, older minute
observations and a 24-hour horizon. Charts return at most 240 actual points.
Delayed or partial observations are not added as fresh history.

## Verification

Backend tests cover reconciliation beyond 500 records, weighted prices, currency
separation, source-quality gaps, pagination, exact 64-bit identities, wildcard
and exact limits, authenticated writes, read-only WebSocket routing and bounded
per-user history. Desktop/mobile browser tests cover drill-down, incoming
WebSocket P&L, filters, locating a client, limits persistence, keyboard focus and
existing Live Monitor currency/reconnect behavior.

A read-only check on the current live SDK book used 88 open positions and 50
active accounts across six groups. Median aggregation time over three reads was
1.82 ms. This measures engine calculation on that book, excluding SDK requests,
WebSocket serialization/delivery and browser rendering. No orders, notifications
or policy changes were sent by the check.
