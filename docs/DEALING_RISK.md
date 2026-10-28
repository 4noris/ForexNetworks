# Dealing & Risk

This screen uses the single configured MT5 Manager connection.

- **Open:** `/dealing-risk`.
- **Choose scope:** The **Live Groups** selector controls the monitoring scope. The page starts with a configured default group when available. Selection and other tab state stay in memory when switching between previously opened main pages or inner tabs.
- **Live Monitor:** `POST /api/risk/live` supplies account, position, symbol, summary, and alert data. It prefers a published Manager cache generation and can use a focused scope scan or short-lived scope cache when a complete generation is unavailable. The page listens for Manager WebSocket updates and also has a timed refresh fallback. It shows source age and warns when required account metrics are estimated or unavailable.
- **P&L Leaders:** `POST /api/risk/performance` computes realized-P&L rankings from MT5 deal history. A cached result may appear while the historical scan runs. The response reports its history window and data quality; a short progressive result is not the same as a complete lookback result.
- **Analyser:** Investigate a specific client from the selected scope using `POST /api/risk/analyse` and client-live data. Links from Command Center can open an account directly. Use the displayed evidence and source status when reviewing a client.
- **Exposure:** Uses the live monitor snapshot for position, symbol, and account concentration views, and requests historical performance where needed. Some derived exposure figures may be indicative if MT5 contract specifications or conversion inputs are missing; inspect their displayed data quality before using them for a decision.
- **Book Suggestions:** Combines live account information with available realized-P&L history to suggest A-Book, B-Book, or Hybrid review. A suggestion is a review aid; opening this tab does not move an account to another MT5 group or book.
- **Anti-Fraud:** `POST /api/risk/antifraud` scans the selected scope using the saved detector thresholds and available MT5 trades, deals, positions, account data, and tick evidence. The result includes evidence and data-quality indicators. Missing or incomplete MT5 evidence must not be read as a verified positive. Completed events are correlated and persisted so dealer status survives restarts. Configured background scope scans can continue without an open browser tab.
- **Fraud-indicator formulas:** See [Anti-Fraud fraud indicators](ANTI_FRAUD_FRAUD_INDICATORS.md) for the exact latency candidate gates, tick-gap calculation, thresholds, missing-data behavior, and execution checks that require additional source journals.
- **Every detector card:** See [All Anti-Fraud indicators](ANTI_FRAUD_ALL_INDICATORS.md) for the behavioral and risk formulas, threshold defaults, funding classification, exposure calculations, badge meanings, and Toxic clients count.
- **Alert action:** Live Monitor can mark an alert as noticed. Persistent Anti-Fraud event status and notes are handled in Command Center's event panel and stored separately from MT5 trade data.

## Tab switching and freshness

- A main page or Dealing & Risk inner tab **stays mounted after it has been opened**. Switching away hides it; switching back reuses its current filters, component state, and live subscriptions instead of remounting it.
- A tab opened for the first time may still need to load its code and fetch its first dataset. Subsequent switches reuse the running view. A full browser refresh starts a new frontend session.
- WebSocket messages signal new Manager generations. They do not guarantee that every historical scan has finished. Check the source time, cache status, warnings, and data-quality fields on the relevant screen.
- Hidden browser windows may reduce timed refresh work. Returning to the browser resumes the normal refresh path.

## Access and implementation

- An authenticated application session is required.
- Frontend: `frontend/src/pages/DealingRisk.jsx` and its tab components.
- API: `api.py`; Manager cache in `mt5_connection.py`.
- See [Command Center](COMMAND_CENTER.md) for overview KPIs and persistent risk events, and [MT5 RMS Complete Guide](MT5_RMS_COMPLETE_GUIDE.md) for wider operations.
