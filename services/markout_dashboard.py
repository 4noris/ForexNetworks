"""Evidence-backed monetary markout; never infer hedge allocation from client deals."""
import math


def dashboard(result, currency, symbol_modes):
    rows = result["executions"]
    curves = []
    notionals = []
    for row in rows:
        lots, contract, rate = (row.get(key) for key in ("volume_lots", "contract_size", "rate_profit"))
        valid = (currency == "USD" and symbol_modes.get(row["symbol"]) in (0, 2, 4, 5, 32, 38)
                 and all(value is not None and math.isfinite(value) and value > 0 for value in (lots, contract, rate)))
        notional = lots * contract * row["execution_price"] * rate if valid else None
        row["notional_usd"] = notional
        notionals.append(notional)
    for horizon in result["summary"]:
        covered = [(row["horizons"][horizon]["execution_to_mid_bps"] * row["notional_usd"] / 10000,
                    row["notional_usd"]) for row in rows if row["notional_usd"] is not None
                   and row["horizons"][horizon]["execution_to_mid_bps"] is not None]
        volume = math.fsum(value[1] for value in covered)
        pnl = math.fsum(value[0] for value in covered)
        curves.append({"horizon": int(horizon), "dpm": pnl / volume * 1_000_000 if volume else None,
                       "covered": len(covered), "total": len(rows)})
    cumulative = []
    totals = {key: 0.0 for key in ("profit", "commission", "swap", "fee", "net")}
    for row in rows:
        for key in ("profit", "commission", "swap", "fee"):
            value = row.get(key) if currency == "USD" else None
            totals[key] = totals[key] + value if totals[key] is not None and value is not None else None
        totals["net"] = sum(totals[key] for key in ("profit", "commission", "swap", "fee")) if all(totals[key] is not None for key in ("profit", "commission", "swap", "fee")) else None
        cumulative.append({"time": row["time_ms"], **totals})
    return {"currency": currency or None, "trades": len(rows),
            "volume_usd": math.fsum(notionals) if notionals and all(value is not None for value in notionals) else None,
            "dpm": curves[-1]["dpm"] if curves else None,
            "total_attribution_usd": None, "internalised_pct": None, "residual_usd": None,
            "data_sources": {"execution_and_ticks": "MT5 Manager",
                             "hedge_allocation": "not_connected"},
            "curves": curves, "cumulative": cumulative,
            "unavailable_reason": "LP/SI attribution, internalisation and undistributed residual require execution-level hedge/allocation records. MT5 client deals alone do not establish them.",
            "monetary_method": "Client execution-to-future-mid P&L per USD million of covered execution notional. Uses deal-time contract size and RateProfit; USD deposit accounts and supported linear symbol calculation modes only. No historical FX rate is invented."}
