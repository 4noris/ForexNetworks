"""Currency-separated live money totals; never add or infer exchange rates."""
from collections import defaultdict
import math


NATIVE_FIELDS = {"balance":"Balance", "equity":"Equity", "margin":"Margin",
                 "margin_free":"Free Margin", "floating_pnl":"Live Floating PnL"}


def currency_money_totals(rows, *, native=False):
    groups = defaultdict(list)
    for row in rows:
        currency = str(row.get("Currency" if native else "currency") or "UNKNOWN").strip().upper() or "UNKNOWN"
        groups[currency].append(row)
    fields = NATIVE_FIELDS if native else {key:key for key in NATIVE_FIELDS}
    totals = {}
    for currency, accounts in groups.items():
        result = {"accounts":len(accounts)}
        for key, field in fields.items():
            if currency == "UNKNOWN":
                result[key] = None
                continue
            def amounts():
                for row in accounts:
                    value = row.get(field)
                    if native and field == "Live Floating PnL" and value is None:
                        value = row.get("Floating PnL")
                    if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value):
                        raise ValueError("Authoritative account money is missing")
                    yield value
            try:
                value = math.fsum(amounts())
                result[key] = round(value,2) if math.isfinite(value) else None
            except (ValueError,OverflowError):
                result[key] = None
        totals[currency] = result
    return totals
