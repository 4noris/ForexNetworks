# Complete Live Monitor book

`POST /api/risk/live` and its authenticated `live-risk` WebSocket subscription return every matching active account, open position, symbol and large position. Operational filters run against the full scope and do not apply a second 500-row cap. Portfolio money totals still include all scoped accounts, including inactive accounts, and remain separated by currency.

The snapshot builders retain an explicit optional `row_limit` for callers that deliberately request a ranked subset. Their default is unlimited. Live Monitor explicitly requests unlimited rows. Truncation messages are generated only when a caller supplies a limit and the response actually exceeds it.

Native-cache scoring builds profile/network details only for accounts with positions and any explicitly requested account. Inactive accounts remain in financial totals. Metadata-only group-catalog reads do not traverse the full user catalog. This avoids unnecessary work on large Manager catalogues.

Live Monitor payload arrays use stable account login / position ticket order so price or risk changes do not reorder thousands of records in WebSocket patches. Position tickets are strings to preserve 64-bit IDs. Complete book subscriptions coalesce builds with a minimum 250 ms interval; actual delivery also depends on source freshness, computation and connection speed. No fixed end-to-end latency is promised.

The Complete live book section provides Accounts and Positions tabs, search across all loaded records, sorting and a fixed-height scrolling table. Only the visible window of up to 24 records is rendered at once; all records remain loaded and searchable, with their full logical table row count exposed to assistive technology. Incoming snapshots retain the current search and scroll position. Charts and explicitly named top-risk summaries remain ranked over the complete dataset.

Tests cover 579 active accounts, 4,817 positions, 120 symbols, full filtered scopes, financial totals including inactive users, exact large ticket IDs, records beyond row 500, scrolling to the last record and live updates to that record. Test fixtures are isolated from production MT5 data.
