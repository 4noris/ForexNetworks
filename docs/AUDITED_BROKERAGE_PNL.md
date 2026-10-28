# Audited Brokerage P&L

The **Brokerage → Audited Broker P&L** view uses the existing RMS MT5 Manager connection. It retrieves daily equity snapshots and deal history in a background job. The existing Brokerage Overview remains available.

## Calculation

- Dates and deal windows use UTC.
- Client net P&L = `(closing equity − opening equity) − deposits − credit in − additional funding + withdrawals + credit out`.
- Each deal is assigned to at most one funding category. Credit, bonus, balance deposits and withdrawals, posted charges, corrections, and trading results are distinguished using MT5 action, signed Profit, comment, and optional reviewed rules.
- B-Book broker P&L = `−client net P&L`.
- A-Book broker P&L = `−posted client commission − posted client swap`. This view does not include LP costs or markup income.
- Amounts remain at source precision during calculation. Values are rounded only for display/export.
- Currency totals are kept separate. Accounts with changing or unknown currency are flagged.

## Data quality

- Missing opening or closing equity and failed deal-history requests produce unavailable P&L, never a zero substitute.
- The All Clients total is unavailable when an account in scope lacks a required input. A separate complete-input cohort is shown for investigation.
- Today's closing equity is provisional. Optional live equity fallback is labeled provisional and is used only for today's date.
- The transaction audit records each included deal, classification, raw amount, and reviewed-rule status. Retrieval diagnostics show request errors per group.
- Group membership comes from MT5's current user list plus returned historical rows. Accounts deleted or moved out of the selected groups may be absent if the server does not return them; inspect coverage and warnings before using historical totals.

## Using the report

1. Select one or more groups and the opening and closing equity dates.
2. Choose A-Book or B-Book. Optionally filter specific logins or use separate transaction dates.
3. Run the audited report. Brokerage stays responsive while one queued worker reads MT5 history.
4. Review data-quality warnings, the account and transaction tables, and download the full Excel audit.

The API caches completed jobs for 15 minutes per user and exact request. A forced rerun refreshes the result. At most four scans can be running or queued; the fifth request receives a retry response. The Excel export contains all rows, while the browser table limits the displayed rows for speed.

`config/transaction_rules.json` contains optional reviewed classification rules. It starts empty. Add rules only after verifying the MT5 deal ticket, login, action, signed amount, and reason; overlapping rules cause the affected classification to require review.
