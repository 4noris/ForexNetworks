# Live UI transport

All JSON data reads use one session-authenticated `/api/live/ws` connection per browser tab. This includes initial loads, Manager status, accounts, Market Watch, calendar, settings lookups, client analysis, live risk, operations, historical analytics, and report queries. Manager Overview/dashboard, Accounts, Market Watch, and scoped Dealing & Risk also receive incremental pushed updates. The transport uses RMS's existing authoritative backend views; it does not run PositionsTracker64.exe, load its DLL, copy its licence, or calculate replacement broker P&L.

The first frame for each subscription is a complete `snapshot`. Subsequent `delta` frames carry a base version and replacement values at JSON paths. Structural array changes replace the array, so closed positions disappear. Large deltas become snapshots. A complete snapshot is sent at least every 30 seconds. Current prices and P&L are the values published by RMS's Manager cache; the stream does not make them fresher than that cache.

The frontend updates React Query directly on new pushed frames. Existing refresh calls read the local stream snapshot while healthy. Other data reads use multiplexed `request`/`response` messages with correlation IDs on the same socket, including the initial load. Filtered reports keep the same request bodies and response shapes. Each request goes through the existing backend routes and their authentication, administrator checks, Pydantic validation, filtering and Manager context, in-process without an internal network request.

If an upgrade is unavailable before a query is sent, HTTP is used after at most 1.5 seconds. A query interrupted after sending raises an error instead of silently repeating a potentially expensive report/job. Abort signals and request timeouts cancel individual queries. Long reports do not block other replies or heartbeats: up to four queries execute concurrently, with at most 32 pending per connection. The response limit is 64 MiB; larger reports must use narrower filters or file exports. This changes transport, not the work needed to compute a report.

A missing stream version clears local data and reconnects for a new snapshot, with backoff up to 30 seconds. Hidden tabs close idle sockets but let pending queries finish. Logout and data-changing API requests clear stream data. Idle subscriptions are removed after 45 seconds. Authentication and session renewal, settings changes, trade/approval actions, export creation and file downloads keep their existing HTTP paths. The explicit POST read allowlists in `services/websocket_reads.py` and `frontend/src/lib/readRequests.js` must stay in sync when new filtered queries are added. GET JSON reads are supported automatically; arbitrary URLs, traversal paths, auth routes, writes and download endpoints are rejected by the server.

Dealing & Risk pushed snapshots are available only with a complete, fresh full Manager cache. Focused mode, warming, incomplete or stale cache states use the existing scope-fetching route via a WebSocket request/reply, without introducing background SDK scans. Account, symbol, selected groups, excluded logins and risk-category filters retain their existing route behavior.

The backend verifies the session cookie on connect and every 15 seconds, rejects sessions requiring password changes and checks browser Origin against the same host or `ALLOWED_ORIGINS`. Reverse proxies must forward `/api/live/ws` upgrades and preserve the browser's host/protocol. Cross-origin deployments must list their UI origin explicitly. No credential is put in a WebSocket URL. There are at most eight subscriptions per connection and 64 short-lived shared read views, keyed by authenticated user, topic and scope. Blocking view construction runs outside the event loop.

Restart the backend and build/reload the frontend to activate. Set `VITE_LIVE_STREAM=false` at frontend build time to use HTTP only. No .env values or MT5 connection settings need changing.

Verification:

```powershell
python -m pytest tests/test_live_stream.py tests/test_websocket_reads_auth.py tests/test_live_risk.py tests/test_market_watch.py tests/test_auth_security.py -q
Set-Location frontend
node --test src/lib/liveTransport.test.mjs
npm.cmd run lint
npm.cmd run build
```

Tests use simulated cache data. Live broker throughput, browser rendering latency and proxy behavior must be measured in the running deployment; no speedup figure is claimed from unit tests.
