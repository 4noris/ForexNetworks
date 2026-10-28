# Command Center

This screen uses the single configured MT5 Manager connection.

- **Open:** `/command-center` (also the `/` landing page).
- **Purpose:** A quick view of accounts, open exposure, current risk, stored Anti-Fraud events, and recent audit changes for the selected MT5 groups.
- **Choose scope:** Use **Live Groups** to select one or more groups. The scope applies to the account and position figures. An empty selection does not start a live calculation.
- **Data source:** `POST /api/dealer-command-center` reads one published MT5 Manager cache generation. It does not run a new MT5 history scan for every page refresh.
- **Freshness:** The header shows connection status, source time, generation, selected group count, and data age. A generation older than 60 seconds is marked **stale**. The page requests a new view on a Manager WebSocket generation update and has a 30-second fallback while the browser tab is visible. **Refresh** requests a new view manually.
- **Main KPIs:**
  - **Total accounts:** Selected, non-excluded MT5 users.
  - **Active accounts:** Distinct selected account logins with at least one open position.
  - **Open positions:** Count of selected open positions.
  - **Gross lots:** Sum of non-negative open-position volumes. Lots are displayed as a size measure, not treated as monetary exposure.
  - **Equity, balance, floating P&L, margin used, free margin:** Sums of the corresponding account metrics from the same Manager generation. Financial totals display **Unavailable** when selected accounts have unknown or mixed deposit currencies; the app does not silently add different currencies.
  - **Critical/high risk accounts:** Counts derived from the live risk classification for the selected scope.
  - **Open cases:** Count of investigation cases that are not resolved or closed in the application database. This count is currently system-wide, not filtered to the selected MT5 groups, and is not an MT5 field.
- **Live symbol exposure:** Buy and sell lots, net lots, position count, floating P&L, distinct account count, gross notional, and concentration. Gross notional uses position lots, MT5 contract size, current price, and profit-currency conversion. It shows **Data required** when any required input is unavailable; concentration is unavailable when the complete total cannot be calculated.
- **Top risk accounts:** Non-normal accounts from the live risk result, with account metrics and severity. Clicking a login opens that account in the Dealing & Risk analyser.
- **Persistent risk events:** Stored Anti-Fraud evidence for accounts in the selected scope. Open an event to inspect evidence, actual values, thresholds, quality status, and dealer status. Authorized administrators can assign it, add a factual note, and change its status. Status changes are audited.
- **What changed?:** Recent audit actions and stored risk events. The box is short and scrollable; its scrollbar is hidden.

## Tab switching and freshness

- A main page or Dealing & Risk inner tab **stays mounted after it has been opened**. Switching away hides it; switching back reuses its current filters, component state, and live subscriptions instead of remounting it.
- A tab opened for the first time may still need to load its code and fetch its first dataset. Subsequent switches reuse the running view. A full browser refresh starts a new frontend session.
- WebSocket messages signal new Manager generations. They do not guarantee that every historical scan has finished. Check the source time, cache status, warnings, and data-quality fields on the relevant screen.
- Hidden browser windows may reduce timed refresh work. Returning to the browser resumes the normal refresh path.

## Access and implementation

- An authenticated application session is required. Updating a persistent risk event requires operations administrator permission.
- Frontend: `frontend/src/pages/DealerCommandCenter.jsx` and `frontend/src/components/RiskEventPanel.jsx`.
- API: `POST /api/dealer-command-center` in `api.py`; Manager cache in `mt5_connection.py`.
- See [Dealing & Risk](DEALING_RISK.md) for investigation tabs and [MT5 RMS Complete Guide](MT5_RMS_COMPLETE_GUIDE.md) for wider operations.
