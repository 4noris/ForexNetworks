import assert from "node:assert/strict";
import { test } from "node:test";
import { formatNumber, formatCurrency, formatPercent, formatCompact } from "./formatters.js";

test("missing or invalid source values cannot become financial zero", () => {
  for (const value of [null, undefined, "", "  ", false, true, NaN, Infinity, -Infinity, "bad", {}, []]) {
    for (const formatter of [formatNumber, formatCurrency, formatPercent, formatCompact]) {
      assert.equal(formatter(value), "Unavailable");
    }
  }
  assert.equal(formatNumber(0), "0.00");
  assert.equal(formatCurrency(0), "$0.00");
  assert.equal(formatPercent(0), "0.00%");
  assert.equal(formatCompact(0), "0");
  assert.equal(formatNumber(.001, 8), "0.00100000");
});
