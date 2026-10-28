"""Quote-backed post-execution markout. Positive values favor the client.

This module is deliberately source-agnostic: callers supply verified MT5 deal
and tick records. A missing or stale quote never becomes a zero markout.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timezone
import math
from statistics import median
from typing import Any


def _field(row: Any, *names: str) -> Any:
    for name in names:
        if isinstance(row, dict) and name in row:
            return row[name]
        value = getattr(row, name, None)
        if value is not None:
            return value
    return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _millis(row: Any) -> int | None:
    milliseconds = _number(_field(row, "TimeMsc", "time_msc", "DatetimeMsc", "datetime_msc"))
    if milliseconds is not None and milliseconds > 0:
        return int(milliseconds)
    seconds = _number(_field(row, "Time", "time", "Datetime", "datetime"))
    return int(seconds * 1000) if seconds is not None and seconds > 0 else None


def normalize_executions(deals: list[Any], login: int, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    """Keep executed buy/sell deals, including partial fills and exits."""
    rows = []
    tickets = {}
    for deal in deals:
        try:
            deal_login = int(_field(deal, "Login", "login"))
            action = int(_field(deal, "Action", "action"))
            ticket = int(_field(deal, "Deal", "deal", "Ticket", "ticket"))
        except (TypeError, ValueError):
            continue
        timestamp = _millis(deal)
        price = _number(_field(deal, "Price", "price"))
        symbol = str(_field(deal, "Symbol", "symbol") or "").strip()
        if (deal_login != login or action not in (0, 1) or ticket <= 0
                or timestamp is None or not start_ms <= timestamp <= end_ms
                or price is None or price <= 0 or not symbol):
            continue
        fingerprint = (timestamp, action, price, symbol, *(_field(deal, key) for key in ("VolumeExt", "Volume", "Profit", "Commission", "Storage", "Swap", "Fee")))
        if ticket in tickets:
            if tickets[ticket] != fingerprint:
                raise ValueError(f"Deal {ticket} has conflicting execution records.")
            continue
        tickets[ticket] = fingerprint
        extended = _number(_field(deal, "VolumeExt", "volume_ext"))
        raw_volume = _number(_field(deal, "Volume", "volume"))
        lots = extended / 100000000 if extended is not None and extended > 0 else (
            raw_volume / 10000 if raw_volume is not None else None)
        rows.append({"deal": ticket, "login": login, "position_id": _field(deal, "PositionID", "position_id"),
                     "symbol": symbol, "side": "BUY" if action == 0 else "SELL",
                     "time_ms": timestamp, "time_utc": datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat(),
                     "execution_price": price, "volume_lots": lots if lots is not None and lots > 0 else None,
                     "contract_size": _number(_field(deal, "ContractSize")),
                     "rate_profit": _number(_field(deal, "RateProfit")),
                     "profit": _number(_field(deal, "Profit")),
                     "commission": _number(_field(deal, "Commission")),
                     "swap": _number(_field(deal, "Storage", "Swap")),
                     "fee": _number(_field(deal, "Fee"))})
    return sorted(rows, key=lambda row: (row["time_ms"], row["deal"]))


def normalize_ticks(ticks: list[Any]) -> tuple[list[int], list[float]]:
    by_time = {}
    for tick in ticks:
        stamp = _millis(tick)
        bid = _number(_field(tick, "Bid", "bid"))
        ask = _number(_field(tick, "Ask", "ask"))
        if stamp is None or bid is None or ask is None or bid <= 0 or ask < bid:
            continue
        by_time[stamp] = (bid + ask) / 2
    times = sorted(by_time)
    return times, [by_time[stamp] for stamp in times]


def calculate_markouts(executions: list[dict[str, Any]], ticks_by_symbol: dict[str, list[Any]],
                       horizons_seconds: tuple[int, ...] = (1, 5, 10, 30, 60, 120, 300, 600, 900),
                       max_quote_lag_ms: int = 2000) -> dict[str, Any]:
    """Compare arrival mid with first quote at/after each horizon.

    Arrival quote must be at/before execution. Future quote must be at/after
    target and within max_quote_lag_ms. No interpolation or carried-forward
    future quote is used. Execution-to-future mid includes paid spread.
    """
    if not horizons_seconds or any(h <= 0 for h in horizons_seconds):
        raise ValueError("Markout horizons must be positive seconds.")
    indexed = {symbol: normalize_ticks(ticks) for symbol, ticks in ticks_by_symbol.items()}
    rows = []
    for execution in executions:
        stamp = execution["time_ms"]
        times, mids = indexed.get(execution["symbol"], ([], []))
        baseline_index = bisect_right(times, stamp) - 1
        baseline_ok = baseline_index >= 0 and stamp - times[baseline_index] <= max_quote_lag_ms
        baseline_mid = mids[baseline_index] if baseline_ok else None
        sign = 1 if execution["side"] == "BUY" else -1
        measures = {}
        for horizon in horizons_seconds:
            target = stamp + horizon * 1000
            future_index = bisect_left(times, target)
            future_ok = future_index < len(times) and times[future_index] - target <= max_quote_lag_ms
            future_mid = mids[future_index] if future_ok else None
            client_bps = (sign * (future_mid - baseline_mid) / baseline_mid * 10000
                          if baseline_mid is not None and future_mid is not None else None)
            execution_bps = (sign * (future_mid - execution["execution_price"])
                             / execution["execution_price"] * 10000 if future_mid is not None else None)
            measures[str(horizon)] = {
                "client_mid_bps": round(client_bps, 4) if client_bps is not None else None,
                "execution_to_mid_bps": round(execution_bps, 4) if execution_bps is not None else None,
                "future_quote_time_ms": times[future_index] if future_ok else None,
                "status": "complete" if client_bps is not None else "missing_arrival_quote" if not baseline_ok else "missing_future_quote",
            }
        rows.append({**execution, "arrival_mid": baseline_mid,
                     "arrival_quote_time_ms": times[baseline_index] if baseline_ok else None,
                     "horizons": measures})
    summary = {}
    for horizon in horizons_seconds:
        key = str(horizon)
        values = [row["horizons"][key]["client_mid_bps"] for row in rows
                  if row["horizons"][key]["client_mid_bps"] is not None]
        weighted = [(row["horizons"][key]["client_mid_bps"], row.get("volume_lots")) for row in rows
                    if row["horizons"][key]["client_mid_bps"] is not None
                    and row.get("volume_lots") is not None and row["volume_lots"] > 0]
        weight_total = math.fsum(weight for _, weight in weighted)
        summary[key] = {"covered": len(values), "total": len(rows),
                        "coverage_pct": round(len(values) / len(rows) * 100, 1) if rows else 0,
                        "average_client_mid_bps": round(math.fsum(values) / len(values), 4) if values else None,
                        "median_client_mid_bps": round(median(values), 4) if values else None,
                        "volume_weighted_client_mid_bps": round(math.fsum(value * weight for value, weight in weighted) / weight_total, 4) if weight_total else None,
                        "positive_share_pct": round(sum(value > 0 for value in values) / len(values) * 100, 1) if values else None}
    by_symbol = defaultdict(list)
    for row in rows:
        by_symbol[row["symbol"]].append(row)
    symbols = [{"symbol": symbol, "executions": len(group),
                "markout_60s_bps": round(math.fsum(values) / len(values), 4) if values else None,
                "covered_60s": len(values)}
               for symbol, group in sorted(by_symbol.items())
               for values in [[item["horizons"]["60"]["client_mid_bps"] for item in group
                               if "60" in item["horizons"] and item["horizons"]["60"]["client_mid_bps"] is not None]]]
    return {"summary": summary, "symbols": symbols, "executions": rows,
            "method": "Signed client markout in basis points, arrival mid to first valid mid at/after horizon. Positive favors client; missing quotes remain unavailable."}
