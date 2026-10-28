# Anti-Fraud tab: fraud indicators and calculations

This document describes the **Fraud indicators** category implemented in the current Anti-Fraud tab. It is a detector reference, not a finding that any client committed fraud. The current active fraud-category rule is **Latency Timing Anomaly** (`latency_arbitrage`). Other trading, funding, hedging, profitability, and scalping rules are classified as risk or behavioral indicators.

For every card shown under Fraud, Behavioral, and Risk indicators, see [All Anti-Fraud indicators](ANTI_FRAUD_ALL_INDICATORS.md).

## Data flow and scan scope

- The frontend sends the selected MT5 groups, saved thresholds, and optional excluded symbols to `POST /api/risk/antifraud`.
- The backend uses the single configured MT5 Manager connection. It fetches scoped MT5 deals for the lookback window, builds completed trade lifecycles, then requests historical ticks only for qualifying latency candidates.
- A completed lifecycle requires an opening BUY/SELL deal and a closing deal for the same MT5 position ID, usable timestamps, and reported closing `Profit`. An open position or a close with missing `Profit` cannot create a positive closed-trade P&L signal.
- A trade's **normalized realized P&L** is the sum across its position's deals of `Profit + Commission + Fee + Storage/Swap`. This includes entry and exit charges. `Profit = 0` is a valid reported value; missing profit is different from zero.
- **Holding time (seconds)** = closing deal time − opening deal time. The lifecycle uses the first opening deal and last closing deal; this duration is calculated from the deal timestamps in seconds.
- The scan keeps exact MT5 position ID, symbol, login, first opening deal `TimeMsc`, opening time, holding time, and normalized realized P&L as evidence.
- Selected groups, excluded logins, and excluded symbol patterns restrict the scan. The fraud rule cannot inspect a trade outside that scope.

## Latency Timing Anomaly (`latency_arbitrage`)

### Candidate gate

A completed trade is a **candidate for a tick check** only when all applicable conditions hold:

- `normalized realized P&L >= profit_threshold`.
- `holding time <= maximum_duration_minutes × 60`.
- If **Check total realized P&L** is enabled: the account's sum of normalized completed-trade P&L in the lookback must be `>= minimum_total_realized_pnl`.
- The trade is inside the selected lookback, group, login, and symbol scope.

These conditions **do not create an alert**. They only select trades for historical tick verification. Candidates are sorted by realized P&L, highest first. The backend checks at most `ANTIFRAUD_MAX_TICK_CHECKS` candidates per scan (default **3**, allowed environment range **0–40**). A candidate outside that cap is not tick-verified by that scan.

### Historical tick verification

- The backend calls the existing MT5 `TickHistoryRequest(symbol, from, to)` API. Its requested time range is from `open_time − max(4 × gap_seconds, 30 seconds)` through `open_time + 3 seconds`.
- A usable historical tick must have a positive timestamp and positive bid and ask. Duplicate timestamps are removed and the remaining tick times are sorted.
- MT5 opening deal `TimeMsc` must exist. There must be at least two valid tick times with coverage across the opening time: `first_tick_time < open_time_msc <= last_tick_time`.
- For each pair of consecutive valid ticks, **tick gap (seconds)** = `(current_tick_time_msc − previous_tick_time_msc) / 1000`.
- A timing-anomaly event is emitted only if **both** `tick gap >= gap_seconds` **and** `current_tick_time_msc == trade_open_time_msc`. The equality is exact at millisecond precision: the trade opened at the first recorded quote after that gap.
- The event is a **warning for dealer review**, not proof of arbitrage or fraud. A market-data gap alone is insufficient.

### Missing or incomplete data

- If `TickHistoryRequest` is unavailable, fails, or returns insufficient valid bid/ask coverage, the candidate is recorded in `unchecked_candidates` with status **UNCHECKED** and a reason. No positive latency event is created.
- If precise MT5 opening `TimeMsc` is missing, the candidate is **UNCHECKED** even if second-resolution open time exists.
- If valid ticks cover the open time but no consecutive pair meets both the gap and exact-time conditions, the candidate was checked and is **negative** for this rule.
- The response reports `latency_candidates`, `latency_checked`, warnings, and detector status. If there are candidates but no valid historical ticks, the detector cannot claim a verified positive.

## Parameters used by the fraud rule

| Parameter | Current default | Meaning |
|---|---:|---|
| `lookback_hours` | 24 hours | MT5 deal-history window; API accepts 1–168 hours. |
| `profit_threshold` | 10 | Minimum normalized realized P&L for a tick-check candidate. The UI labels this amount “USD”; the detector compares the numeric MT5 deal P&L directly and does not convert mixed account currencies. |
| `maximum_duration_minutes` | 5 minutes | Maximum completed-trade holding time for a tick-check candidate; API accepts 0.1–1440 minutes. |
| `gap_seconds` | 3 seconds | Minimum measured gap between consecutive valid historical ticks; API accepts 0.1–300 seconds. |
| `check_total_realized_pnl` | false | Adds an account-level realized-P&L gate to candidate selection. |
| `minimum_total_realized_pnl` | 0 | Account-level P&L threshold used only when the preceding switch is enabled. |
| `ANTIFRAUD_MAX_TICK_CHECKS` | 3 | Maximum highest-P&L candidates checked for ticks in one scan; environment setting, clamped to 0–40. |
| `excluded_symbols` | empty | Symbol patterns excluded before lifecycle and detector evaluation. |

The threshold controls in the Anti-Fraud tab are stored in that browser for the selected profile and sent with the next scan. The backend validates their ranges. Some controls shown alongside these settings, such as scalp lifetime, concentration, lots, equity, and margin level, belong to **behavioral or risk rules** and are not additional proof for the latency rule.

## Event fields, quality, and persistence

- A positive latency event contains `rule`, `detector`, `category=fraud_indicator`, severity `warning`, login, position ID, symbol, timestamp, evidence text, triggering values, thresholds, and `data_quality=VERIFIED`.
- Triggering values include exact opening milliseconds, measured gap seconds, normalized realized P&L, and holding seconds. Thresholds include minimum gap, minimum profit, and maximum duration.
- An unchecked candidate is **not** persisted as a positive risk event. Positive events are correlated and deduplicated before persistence. Stored events retain first/last seen times and dealer workflow status across scans and server restarts.
- Browser scanning normally retries on a timer: roughly **15 seconds** after a completed scan, or **5 seconds** when a scan is not ready. Cached results may be returned before a new historical scan completes. The server can also refresh previously monitored scopes in the background; its default interval is **300 seconds**, minimum **60 seconds**, for the next due scope.
- A displayed result is “live” only to the extent of its MT5 source time and completed scan. Review the scan time, cache status, warnings, `latency_checked`, and unchecked candidates before taking action.

## Execution-behavior checks that are not active positives

- **Rejected Orders** requires retained server request logs.
- **Trade Requests Flood** requires timestamped server request logs.
- **IP/CID Control** requires connection or login journals with matching evidence.
- **Stealing Quotes** requires gateway or Data Feed Center journals.
- The current Anti-Fraud detector list marks these as **Data required**. The code does not infer a positive execution-abuse finding from missing journals, ordinary profitability, hedging, or lots alone.

## Related documentation and code

- [Dealing & Risk](DEALING_RISK.md) explains the full tab and the other risk/behavior views.
- [Command Center](COMMAND_CENTER.md) explains persisted risk-event review and dealer status actions.
- Source of truth: `build_client_trade_lifecycles`, `build_antifraud_snapshot`, `request_tick_history`, and `get_antifraud_snapshot` in `api.py`; UI settings in `frontend/src/pages/AntiFraud.jsx`.
