"""Incremental full-portfolio money, with exact float partials and real currencies."""
from collections import defaultdict
import math

from .native_totals import ExactFloatSum
from .live_money import NATIVE_FIELDS


def amount(row, field):
    value = row.get(field)
    if field == "Live Floating PnL" and value is None:
        value = row.get("Floating PnL")
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError("Authoritative native account money is unavailable")
    return value


class NativeGroupMoney:
    def __init__(self):
        self.buckets = {}

    def replace(self, old, new):
        if (old is not None and new is not None and old.get("Group") == new.get("Group")
                and old.get("Currency") == new.get("Currency")):
            key = (str(new.get("Group", "")), str(new.get("Currency") or "UNKNOWN").strip().upper())
            bucket = self.buckets[key]
            for name, field in NATIVE_FIELDS.items():
                before, after = amount(old, field), amount(new, field)
                if before != after:
                    bucket[name].add(-before)
                    bucket[name].add(after)
            return
        for row, sign in ((old, -1), (new, 1)):
            if row is None:
                continue
            key = (str(row.get("Group", "")), str(row.get("Currency") or "UNKNOWN").strip().upper())
            bucket = self.buckets.get(key)
            if bucket is None:
                bucket = {"accounts": 0, **{name: ExactFloatSum() for name in NATIVE_FIELDS}}
                self.buckets[key] = bucket
            bucket["accounts"] += sign
            for name, field in NATIVE_FIELDS.items():
                bucket[name].add(sign * amount(row, field))
            if bucket["accounts"] == 0:
                self.buckets.pop(key)

    def snapshot(self):
        # Immutable partials let readers combine groups without either rescanning
        # all inactive users or rounding each group before calculating the total.
        return {key: {"accounts": bucket["accounts"],
                      **{name: tuple(bucket[name].partials) for name in NATIVE_FIELDS}}
                for key, bucket in self.buckets.items()}


def portfolio_summary(buckets, selected_groups, group_currencies, excluded_rows=()):
    grouped = defaultdict(lambda: {"accounts": 0, **{name: [] for name in NATIVE_FIELDS}})
    for (group, currency), bucket in buckets.items():
        if group not in selected_groups:
            continue
        code = str(group_currencies.get(group) or currency or "UNKNOWN").strip().upper()
        result = grouped[code]
        result["accounts"] += bucket["accounts"]
        for field in NATIVE_FIELDS:
            result[field].extend(bucket[field])
    for row in excluded_rows:
        group = str(row.get("Group", ""))
        if group not in selected_groups:
            continue
        code = str(group_currencies.get(group) or row.get("Currency") or "UNKNOWN").strip().upper()
        result = grouped[code]
        result["accounts"] -= 1
        for name, field in NATIVE_FIELDS.items():
            result[name].append(-amount(row, field))
    currencies = {}
    totals = {field: [] for field in NATIVE_FIELDS}
    for code, values in grouped.items():
        if not values["accounts"]:
            continue
        currencies[code] = {"accounts": values["accounts"]}
        for field in NATIVE_FIELDS:
            totals[field].extend(values[field])
            currencies[code][field] = None if code == "UNKNOWN" else round(math.fsum(values[field]), 2)
    return {"scoped_accounts": sum(row["accounts"] for row in currencies.values()),
            "currency_totals": currencies, "currencies": sorted(currencies),
            **{field: round(math.fsum(values), 2) for field, values in totals.items()}}
