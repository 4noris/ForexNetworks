# Callback-first live collection

The collector subscribes to the same MT5 position, user, deal, order, quote and configuration callbacks. Position callbacks deliver current authoritative state continuously. Complete position/order reconciliation runs every two seconds instead of competing with callbacks on every account-refresh cycle. Account metrics refresh on a separate 250 ms schedule, with an idle period after work rather than an unbounded catch-up loop. Clean-position events mark their account dirty for a final account read.

Native JSON uses locale-independent `std::to_chars` with 17 significant digits for floating-point values. A compiled round-trip test verifies signed/unsigned 64-bit bounds, exact double values, minimum subnormal values, escaped strings, raw JSON and the existing non-finite-to-null wire behavior (downstream financial validation remains strict). In a 100,000-record serialization benchmark, stream formatting took 1504 ms and the new formatter took 156 ms. This is serialization cost, not total latency.

WebSocket view caching now tracks the published native epoch and sequence. A current position callback can invalidate a view even when the full-book verification timestamp has not advanced. The verification timestamp is preserved: no clock or stale-source threshold was adjusted. Legacy readers retain the prior observation-time cache marker.

A real WebSocket regression test verifies that a callback version change delivers a new value before the old two-second cache hold expires, while `source_updated_at` remains unchanged. Queue burst/order tests and related stream/native tests passed. No test notification or trade was sent.

After deployment, 24 public readiness checks measured full-book source observation age. These are source freshness observations, not broker-to-browser round trips and not a permanent latency guarantee. A two-second full-book reconciliation is separate from continuous callback delivery.

{
  "samples": 24,
  "readiness_failures": 0,
  "minimum_seconds": 0.696,
  "median_seconds": 2.909,
  "p95_seconds": 3.316,
  "maximum_seconds": 3.517
}
