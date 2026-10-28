# Anti-Fraud tab: all indicator cards and calculations

This reference matches the detector cards currently shown in **Dealing & Risk → Anti-Fraud**. The three headings classify the **type of signal**, not whether a client has committed fraud. A card count is the number of events returned by the current scan for that rule, not the number of unique clients.

## How to read the cards

- **Fraud indicators:** Timing anomalies that need dealer investigation. The current active rule is Latency Timing Anomaly; it is an indicator, not proof.
- **Behavioral indicators:** Trading patterns and profit concentration. Ordinary scalping or a concentrated profitable result is not fraud by itself.
- **Risk indicators:** Trading, exposure, account, funding, and operational conditions. Profitability, hedging, and withdrawals are not fraud by themselves.
- **Green check:** The detector is active and returned **zero** events in this scan.
- **Red count:** The detector returned that many events in this scan. One client can contribute multiple events, especially for position-level rules.
- **NO DATA:** The detector had no usable source data for this scan, such as no deals or no MT5 news. It is not a negative finding.
- **DATA REQUIRED:** The Manager integration does not provide the required journal or ledger source for that rule; the detector cannot make a positive finding.
- The **Evidence Report** below the cards contains the triggering records. `VERIFIED`, `PARTIAL`, and `UNCHECKED` describe evidence quality. An `UNCHECKED` latency candidate is not a positive event.

## Common inputs and calculations

- The scan covers the selected MT5 groups, excludes specified logins and symbol patterns, and normally reads the last `lookback_hours` of scoped deals (**24 hours** by default).
- A **completed trade** joins opening and closing BUY/SELL deals with the same MT5 position ID. It needs valid timestamps and reported closing `Profit`.
- **Realized P&L per completed trade** = sum across its position deals of `Profit + Commission + Fee + Storage/Swap`. A reported `Profit = 0` is valid; a missing closing `Profit` excludes that lifecycle from positive P&L rules.
- **Holding time** = last closing deal time − first opening deal time, in seconds. **Account realized P&L** = sum of normalized P&L for its completed trades in the lookback.
- **Gross positive realized P&L** = sum of completed-trade P&L values strictly greater than zero. This is the denominator for both concentration rules; losses do not reduce it.
- The UI calls several amount thresholds “USD,” but the Anti-Fraud code compares numeric MT5 deal/account amounts directly. It does **not** convert mixed account currencies for these detector thresholds. Select one currency group or verify account currency before comparing clients.

## Fraud indicators

### Latency Timing Anomaly (`latency_arbitrage`)

- **Candidate:** Completed-trade P&L `>= profit_threshold` (**10** default), holding time `<= maximum_duration_minutes × 60` (**5 minutes** default), and optionally account realized P&L `>= minimum_total_realized_pnl` when **Check total realized P&L** is enabled.
- **Tick check:** The highest-P&L candidates are checked, up to `ANTIFRAUD_MAX_TICK_CHECKS` per scan (**3** default). Valid historical ticks require positive bid, ask, and timestamp. Exact MT5 opening `TimeMsc` and tick coverage around it are required.
- **Formula:** `gap_seconds_measured = (current_tick_time_msc − previous_tick_time_msc) / 1000`. Emit a **warning** only when `gap_seconds_measured >= gap_seconds` (**3 seconds** default) **and** `current_tick_time_msc == trade_open_time_msc` exactly.
- **Missing data:** Missing precise opening time, historical ticks, or complete valid bid/ask coverage produces an **UNCHECKED candidate**, never a positive latency event. A valid check with no matching gap is negative for this rule. A tick gap alone does not prove arbitrage.
- Full evidence and boundary details are in [Fraud Indicators](ANTI_FRAUD_FRAUD_INDICATORS.md).

## Behavioral indicators

| Card | Event condition and measurement | Defaults / severity |
|---|---|---|
| **Short-Hold Scalping** (`short_hold_scalping`) | Per account, count completed trades with `P&L > 0` and `holding time <= scalp_seconds`. Emit when count `>= scalp_sequence_min` **unless** the HFT condition below also holds. Evidence includes count, intervals between opening times, maximum hold, and sum of positive P&L for qualifying trades. | `scalp_seconds=180`, `scalp_sequence_min=3`; **warning**. Three short profitable trades are **not** labelled HFT. |
| **High-Frequency Scalping** (`scalpers_hft`) | Same qualifying profitable short-hold trades, sorted by opening time. `rapid_intervals = count(0 <= next_open − previous_open <= 2 seconds)`. Emit HFT only if trade count `>= max(scalp_sequence_min, 10)` **and** rapid intervals `>= 5`. | At least **10** trades and **5** rapid intervals with default settings; **high**. This is a behavioral classification, not a fraud verdict. |
| **Single-Trade Profit Concentration** (`single_trade_profit_concentration`) | `largest_positive_trade_P&L / gross_positive_realized_P&L >= 0.80`, with enough completed trades and gross positive P&L. The percentage is multiplied by 100 for display. | `concentration_min_trades=3`, gross winning P&L `>= LOW_DEPOSIT_HIGH_PROFIT_MIN` (**200** default); **warning**. |
| **Symbol Profit Concentration** (`symbol_profit_concentration`) | Sum positive completed-trade P&L per symbol; `largest_symbol_positive_P&L / gross_positive_realized_P&L >= 0.80`. | At least **3** completed trades, **2** winning trades, gross winning P&L `>= 200` by default; **warning**. |

## Risk indicators: trades and accounts

| Card | Event condition and measurement | Defaults / severity |
|---|---|---|
| **Sharp Deals** (`sharp_deals`) | A completed trade has normalized P&L `>= sharp_profit_threshold` and holding time `<= maximum_duration_minutes × 60`. One event per qualifying trade. | Profit **100**, duration **5 minutes**; **high**. |
| **News Trading** (`news_trading`) | Match each trade opening against a timed economic calendar release for either currency in its symbol. The MT5 terminal bridge exports the built-in Calendar and the scanner reads it automatically; a manually imported JSON calendar is a fallback. Evidence records offset, title, currency, impact, hold time, and trade P&L. | **5-minute** window; **high** for high-priority releases, otherwise **warning**. The Calendar page shows all-day and tentative entries, but the detector skips them because they have no exact release time. `DATA REQUIRED` when no fresh calendar is available. Proximity alone is not proof of abuse. |
| **Achieved Profit** (`achieved_profit`) | Account normalized realized P&L in the lookback `>= max(minimum_total_realized_pnl, sharp_profit_threshold)`. | **100** by default because minimum total is **0** and sharp threshold is **100**; **warning**. Profitability alone is a risk signal. |
| **Hedge Accounts** (`hedge_accounts`) | The account has at least one open BUY and one open SELL position on the **same symbol**. Counts both sides in evidence. | One event per qualifying account/symbol; **warning**. Hedging alone is never fraud evidence. |
| **Failed Stop Outs** (`failed_stopouts`) | An account with open positions has `equity <= 0` **or** `0 < margin_level <= stopout_margin_level`. | Margin level **100%** default; **critical**. Quality is `PARTIAL` if equity or margin-level source is missing. |
| **Mini Accounts** (`mini_accounts`) | An account with open positions has known `equity <= mini_equity_threshold`. | Equity **100** default; **warning**. No event from missing equity. |

## Risk indicators: open-volume exposure

- **Position exposure in account currency** = `lots × MT5 contract size × current price × currency conversion`. Contract size comes from the position or `SymbolGet`; conversion uses `RateProfit`, or `1` only when account and symbol profit currency match. Missing contract size, price, or conversion means exposure is **unavailable**.
- **Capital denominator** = positive equity if available; otherwise positive balance. Without positive capital, no large-volume event is emitted. Raw lots are a supporting gate, not a monetary exposure measure by themselves.

| Card | Event condition and measurement | Defaults / severity |
|---|---|---|
| **Large Volumes by Account** (`large_volume_account`) | Sum open lots and converted exposure over all positions for an account. Emit if `gross_lots >= large_account_lots`, **all** position exposures are available, and `gross_exposure / capital >= large_account_exposure_ratio`. | At least **5 lots** and exposure/capital **5×**; **warning**. |
| **Large Volumes by Order** (`large_volume_order`) | For each open position, emit if `position_lots >= large_order_lots`, exposure is available, and `position_exposure / capital >= large_order_exposure_ratio`. | At least **1 lot** and exposure/capital **1×**; **warning**. |

## Risk indicators: funding and profit

- Recent **deposits** are positive MT5 `DEAL_BALANCE` postings; recent **withdrawals** are the absolute value of negative `DEAL_BALANCE` postings. Credit actions are not counted as deposits.
- If a balance posting comment indicates `credit`, `reversal`, `transfer`, `correction`, `bonus`, `adjust`, `cancel`, or `internal`, funding is considered uncertain for that login and the funding alerts below are suppressed. These MT5 postings alone cannot prove the external payment origin; emitted funding events are marked `PARTIAL`.
- `LOW_DEPOSIT_ALERT_THRESHOLD` defaults to **500**; `LOW_DEPOSIT_HIGH_PROFIT_MIN` to **200**; `LOW_DEPOSIT_PROFIT_MULTIPLE` to **1.5**. They are backend environment settings. Lifetime funding is requested for accounts with recent realized P&L at least the configured minimum. **Starting capital** is the pre-lookback balance-ledger proxy plus lookback deposits, not a verified external cash-flow ledger.

| Card | Event condition and measurement | Defaults / severity |
|---|---|---|
| **Lookback Deposit / High Profit** (`low_deposit_high_profit`) | `0 < lookback_deposits <= 500` and lookback realized P&L `>= max(200, lookback_deposits × 1.5)`. | **warning**; funding quality `PARTIAL`. |
| **Lifetime Deposits / High Profit** (`lifetime_deposit_high_profit`) | `0 < lifetime_deposits <= 500` and lookback realized P&L `>= max(200, lifetime_deposits × 1.5)`. | **high**; funding quality `PARTIAL`. |
| **Starting Capital / High Profit** (`starting_capital_high_profit`) | `0 < starting_capital <= 500` and lookback realized P&L `>= max(200, starting_capital × 1.5)`. | **high**; funding quality `PARTIAL`. |
| **Repeated Funding** (`repeated_funding`) | Count distinct positive, unambiguous lookback balance postings; emit at **3 or more**. Evidence includes count and total deposit amount. | **warning**; funding quality `PARTIAL`. |
| **Deposit Alert** (`deposit_alert`) | Each distinct positive MT5 balance deal at or above the configured `deposit_alert_threshold` is alerted, even if the account has no completed trades. Requires a deal ticket, timestamp inside the lookback, finite amount, and no transfer/credit/bonus/reversal/correction indication. Amount is compared in the account currency; no FX conversion is assumed. | Default threshold **1,000**; **warning**; quality `PARTIAL` because MT5 does not verify the external payment origin. The threshold is set in Anti-Fraud settings and each deal is deduplicated by ticket across scans. |
| **Profit Withdrawal Pressure** (`profit_withdrawal_pressure`) | Lookback realized P&L `> 0` and withdrawals `>= max(500, realized_P&L × 0.80)`. | **high**; financial risk, not direct fraud evidence; quality `PARTIAL`. |
| **Deposit / Withdrawal Churn** (`funding_churn`) | Lookback deposits `>= 500`, withdrawals `>= 500`, and `min(deposits, withdrawals) / max(deposits, withdrawals) >= 0.80`. Displayed match percent = that ratio × 100. | **warning**; quality `PARTIAL`. |

- **Correlation:** If the three “Deposit/High Profit” variants qualify for one account in the same scan, only the highest-priority one is kept: Starting Capital, then Lifetime Deposits, then Lookback Deposit. The retained event records the correlated rules. Other funding rules remain separate.

## Risk indicator: agent commissions

- **Agent Commissions** (`agent_commissions`) counts MT5 daily, monthly, and instant agent-commission actions in the scanned deal set.
- **Amount per posting** = `Profit` when the field is present, **including zero**. Only if `Profit` is missing/null does it use `Commission`. **Total** = sum of those posting amounts.
- Emits one **info** summary event with the posting count and total. Its synthetic login is `0`, so it is not a specific client's fraud allegation.

## Toxic clients count and event details

- **Toxic clients (N)** counts distinct nonzero logins with at least one event in the **returned** Anti-Fraud snapshot. It is not a count of proven fraud, and it can include behavioral and risk events.
- The modal sorts clients by a display priority score: **40** points per critical event, **25** per high event, and **10** per other event. This is a UI sorting score, not an MT5 metric or fraud probability.
- The backend can return at most **2,000** events and **500** client summaries in one response. A displayed count therefore describes that returned result, not necessarily all events in a much larger scan.
- Events carry rule, severity, login, time, exact triggering values, thresholds, evidence, data-quality status, and related event IDs when applicable. Positive events are deduplicated for persistence; `UNCHECKED` candidates are never persisted as positive detections.

## Settings and source

- User-editable Anti-Fraud thresholds are saved in this browser per selected profile and sent with scans. Changing a threshold affects the next scan, not the existing result.
- `ANTIFRAUD_CACHE_SECONDS` defaults to **120 seconds** in code (the sample `.env.example` sets **300**). Cached responses can appear while a fresh historical scan runs. The UI uses a periodic scan loop, and the server can refresh saved scopes in the background.
- Source of truth: `build_client_trade_lifecycles`, `build_antifraud_snapshot`, and `get_antifraud_snapshot` in `api.py`; card layout and Toxic clients scoring in `frontend/src/pages/AntiFraud.jsx`.
