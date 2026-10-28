"""Bounded, incremental aggregates of already validated native account rows."""
import heapq
import math


class ExactFloatSum:
    """Retain non-overlapping partials so replacing rows does not accumulate drift."""
    def __init__(self):
        self.partials = []

    def add(self, value):
        value = float(value)
        index = 0
        for partial in self.partials:
            if abs(value) < abs(partial):
                value, partial = partial, value
            high = value + partial
            if not math.isfinite(high):
                raise ValueError("Native aggregate exceeds finite numeric range")
            low = partial - (high - value)
            if low:
                self.partials[index] = low
                index += 1
            value = high
        self.partials[index:] = [value] if value else []

    def value(self):
        return math.fsum(self.partials)


class NativeAccountTotals:
    FIELDS = {"total_balance": "Balance", "total_equity": "Equity", "total_margin": "Margin",
              "total_free_margin": "Free Margin", "total_floating": "Live Floating PnL"}

    def __init__(self):
        self.sums = {name: ExactFloatSum() for name in self.FIELDS}
        self.enabled = 0
        self.margin_versions = {}
        self.heap = []
        self.ordinal = 0
        self.version = 0

    def replace(self, key, old, new):
        for name, field in self.FIELDS.items():
            if old is not None and new is not None:
                before, after = old[field], new[field]
                # Most live account cycles change floating P&L/equity while
                # balance or margin stay fixed. Preserve the exact sum instead
                # of subtracting and reinserting an unchanged finite amount.
                if before == after and math.isfinite(float(before)):
                    continue
            if old is not None:
                self.sums[name].add(-float(old[field]))
            if new is not None:
                self.sums[name].add(new[field])
        self.enabled += int(bool(new and new.get("Enabled"))) - int(bool(old and old.get("Enabled")))
        if new is None:
            self.margin_versions.pop(key, None)
            return
        margin = float(new["Margin"])
        previous = self.margin_versions.get(key)
        if previous is not None and previous[0] == margin:
            return
        order = previous[1] if previous is not None else self.ordinal
        if previous is None:
            self.ordinal += 1
        self.version += 1
        self.margin_versions[key] = (margin, order, self.version)
        heapq.heappush(self.heap, (-margin, order, key, self.version))

    def read(self, rows, positions):
        if len(self.heap) > max(1024, 2 * len(self.margin_versions)):
            self.heap = [(-margin, order, key, version)
                         for key, (margin, order, version) in self.margin_versions.items()]
            heapq.heapify(self.heap)
        top = []
        retained = []
        while self.heap and len(top) < 5:
            entry = heapq.heappop(self.heap)
            negative, order, key, version = entry
            if self.margin_versions.get(key) != (-negative, order, version):
                continue
            retained.append(entry)
            row = rows[key]
            top.append({"login": int(row["Login"]), "name": str(row.get("Name", "")),
                        "group": str(row.get("Group", "")), "margin": round(float(row["Margin"]), 2),
                        "margin_level": round(float(row["Margin Level %"]), 2),
                        "floating_pnl": round(float(row["Floating PnL"]), 2)})
        for entry in retained:
            heapq.heappush(self.heap, entry)
        return {"total_users": len(rows), "enabled_users": self.enabled, "total_positions": len(positions),
                **{name: round(total.value(), 2) for name, total in self.sums.items()}, "top_margin_accounts": top}
