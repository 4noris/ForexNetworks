"""Scope-exact monitoring limits. Missing valuation is never treated as zero."""
from decimal import Decimal
from services.historical import number


def limit_value(limit, accounts, positions):
    scope, key, metric = limit["scope_type"],str(limit["scope_value"]),limit["metric"]
    if key == "*" and metric in {"net_lots", "gross_lots"}:
        from collections import defaultdict
        by_login = {row["login"]:row.get("group") for row in accounts}
        grouped = defaultdict(list)
        for row in positions:
            identity = row.get("symbol") if scope=="symbol" else row["login"] if scope=="account" else by_login.get(row["login"])
            grouped[identity].append(row)
        values=[]
        for selected in grouped.values():
            if any(number(row.get("volume")) is None or (metric=="net_lots" and row.get("action") not in {"BUY","SELL"}) for row in selected):
                return None,"Position volume or direction unavailable"
            values.append(abs(sum((number(row["volume"])*(1 if metric=="gross_lots" or row["action"]=="BUY" else -1) for row in selected),Decimal(0))))
        return (float(max(values)) if values else None),"Highest individual scope lot exposure; wildcard policies never offset different clients, groups or symbols"
    rows = [row for row in accounts if (str(row["login"]) == key if scope == "account" else row.get("group") == key)] if scope != "symbol" else accounts
    logins = {row["login"] for row in rows}
    selected = [row for row in positions if (row.get("symbol") == key if scope == "symbol" else row["login"] in logins)]
    if scope == "symbol":
        logins = {row["login"] for row in selected}
        rows = [row for row in accounts if row["login"] in logins]
    if not rows and not selected:
        return None,"No matching active account or position records"
    def total(values):
        values = [number(value) for value in values]
        return float(sum(values,Decimal(0))) if all(value is not None for value in values) else None
    if metric == "net_lots":
        if any(row.get("action") not in {"BUY","SELL"} for row in selected):
            return None,"Position direction unavailable"
        value=total((number(row.get("volume"))*(1 if row["action"]=="BUY" else -1) if number(row.get("volume")) is not None else None) for row in selected)
        return (abs(value) if value is not None else None),"Absolute signed buy-minus-sell lots in the complete matching scope"
    if metric == "gross_lots":
        return total(row.get("volume") for row in selected),"MT5 gross lots in the complete matching scope"
    if metric in {"gross_exposure","floating_loss"}:
        currencies={row.get("currency") for row in rows}
        if len(currencies) != 1 or not next(iter(currencies),None) or "UNKNOWN" in currencies:
            return None,"Select accounts in one verified deposit currency"
        if metric == "gross_exposure":
            return total(row.get("notional") for row in selected),"Verified account-currency notional; supported contract mode, price and conversion required"
        values=[number(row.get("floating_pnl")) for row in rows]
        return (float(sum((max(-value,Decimal(0)) for value in values),Decimal(0))) if all(v is not None for v in values) else None),"Negative floating P&L in account currency"
    if metric == "margin_level_below":
        levels=[]
        for row in rows:
            equity,margin=number(row.get("equity")),number(row.get("margin"))
            if equity is not None and margin is not None and margin > 0:
                levels.append(equity/margin*100)
        return (float(min(levels)) if levels else None),"Lowest account equity / margin percentage; accounts without used margin excluded"
    if metric == "concentration_pct":
        values=[number(row.get("concentration_pct")) for row in rows]
        return (float(max(v for v in values if v is not None)) if any(v is not None for v in values) else None),"Highest per-account symbol share of gross lots"
    return None,"Unsupported metric for this scope"
