"""Apply operational filters to the complete live book, retaining account scope."""
from collections import Counter, defaultdict
from decimal import Decimal
from .live_money import currency_money_totals


def scope_live_payload(payload, account_login=None, symbol="", risk_category=""):
    if not (account_login or symbol or risk_category):
        return payload
    accounts = payload.get("accounts", [])
    positions = payload.get("positions", [])
    symbol_logins = {p["login"] for p in positions if p["symbol"] == symbol} if symbol else None
    accounts = [a for a in accounts if (not account_login or a["login"] == account_login)
                and (symbol_logins is None or a["login"] in symbol_logins)
                and (not risk_category or a["severity"] == risk_category)]
    logins = {a["login"] for a in accounts}
    positions = [p for p in positions if p["login"] in logins and (not symbol or p["symbol"] == symbol)]
    def total(rows, field):
        return float(sum((Decimal(str(row[field])) for row in rows), Decimal(0))) if all(row.get(field) is not None for row in rows) else None
    by_login = defaultdict(list)
    by_symbol = defaultdict(list)
    for position in positions:
        by_login[position["login"]].append(position)
        by_symbol[position["symbol"]].append(position)
    scoped_accounts = []
    for source in accounts:
        rows = by_login[source["login"]]
        scoped_accounts.append({**source,"positions":len(rows),"gross_lots":total(rows,"volume"),
                                "floating_pnl":total(rows,"profit") if symbol else source.get("floating_pnl")})
    summary = dict(payload.get("summary", {}))
    summary.update({"scoped_accounts":len(accounts),"active_accounts":len(by_login),"open_positions":len(positions),
                    "gross_lots":total(positions,"volume"),"buy_lots":total([p for p in positions if p["action"] == "BUY"],"volume"),
                    "sell_lots":total([p for p in positions if p["action"] == "SELL"],"volume"),
                    "currencies":sorted({a.get("currency") or "UNKNOWN" for a in accounts}),
                    "floating_pnl":total(scoped_accounts,"floating_pnl"),
                    "currency_totals":currency_money_totals(scoped_accounts),
                    "critical_accounts":sum(a["severity"] == "critical" for a in accounts),
                    "flagged_accounts":sum(a["severity"] != "normal" for a in accounts)})
    for field in ("balance","equity","margin","margin_free"):
        summary[field] = total(accounts,field)
    symbols = []
    for name, rows in by_symbol.items():
        buy = total([p for p in rows if p["action"] == "BUY"],"volume")
        sell = total([p for p in rows if p["action"] == "SELL"],"volume")
        symbols.append({"symbol":name,"buy_lots":buy,"sell_lots":sell,"gross_lots":buy+sell,
                        "net_lots":buy-sell,"floating_pnl":total(rows,"profit"),"positions":len(rows)})
    large = [p for p in positions if p["volume"] >= summary.get("high_lot_threshold",0)]
    summary["large_positions"] = len(large)
    ips = Counter(a.get("last_ip") for a in accounts if a.get("last_ip"))
    network = dict(payload.get("network", {}))
    points = [{**p,"accounts":ips[p["ip"]]} for p in network.get("map_points",[]) if p.get("ip") in ips]
    network.update({"map_points":points,"shared_clusters":[p for p in points if p["accounts"] > 1],
                    "countries":[{"country":name,"ips":count} for name,count in Counter(p.get("country") or "Unknown" for p in points).items()],
                    "providers":[{"provider":name,"ips":count} for name,count in Counter(p.get("provider") or "Unknown" for p in points).items()]})
    summary.update({"located_ips":len(points),"vps_accounts":sum(a.get("vps_status") == "detected" for a in accounts),"countries":len(network["countries"])})
    payload.update({"summary":summary,"accounts":scoped_accounts,"positions":positions,"symbols":symbols,
                    "large_positions":large,"network":network,
                    "filters":{"account_login":account_login,"symbol":symbol,"risk_category":risk_category},
                    "scope_definition":"Risk category selects active accounts by their overall account signals. Symbol selects positions and accounts trading that symbol; balance, equity and margin remain account-wide."})
    payload["warnings"] = [w for w in payload.get("warnings", [])
                           if not w.startswith("Showing ") and not w.startswith("Detailed rows are limited")]
    summary["net_lots"] = summary["buy_lots"] - summary["sell_lots"]
    return payload
