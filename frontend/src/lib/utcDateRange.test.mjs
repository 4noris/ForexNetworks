import assert from "node:assert/strict";
import { test } from "node:test";
import { boundedUtcRange, utcDaysBefore } from "./utcDateRange.js";

test("changing either boundary always keeps both dates inside seven inclusive UTC days", () => {
  assert.deepEqual(boundedUtcRange("2026-10-01", "2026-10-07", "to", "2026-10-08", "2026-10-28"), ["2026-10-02", "2026-10-08"]);
  assert.deepEqual(boundedUtcRange("2026-10-01", "2026-10-27", "from", "2026-10-10", "2026-10-28"), ["2026-10-10", "2026-10-16"]);
  assert.deepEqual(boundedUtcRange("2026-10-10", "2026-10-16", "to", "2026-10-01", "2026-10-28"), ["2026-10-01", "2026-10-01"]);
  assert.deepEqual(boundedUtcRange("2026-10-20", "2026-10-26", "from", "2026-10-30", "2026-10-28"), ["2026-10-28", "2026-10-28"]);
  assert.equal(utcDaysBefore("2026-03-02", 6), "2026-02-24");
});
