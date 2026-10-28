# Live-view recovery

The browser previously treated any socket frame, including a heartbeat or another
view's response, as proof that its cached Live Monitor data was fresh. A stalled
subscription could therefore keep returning the same cached snapshot. Successful
writes also cleared active subscription entries, leaving their listeners behind.

The shared transport now checks each active view independently every second.
Live views without completed reads for three seconds are resubscribed on the same
socket. Heavy historical reports retain a longer deadline. A silent connection is
reconnected after 15 seconds even when the page makes no additional queries.

The server emits a small `progress` frame with the subscription ID and version
when a read completes with unchanged data. This avoids repeatedly transmitting
the entire book merely to prove delivery is working. Acknowledgements never
alter `source_updated_at`, and cannot make an old source observation fresh.
Live-risk, exposure and client-live cache reads reject observations over ten
seconds old, allowing scoped queries to recover rather than reusing stale data.

Successful configuration writes invalidate cached values and refresh active
subscriptions; authentication changes still clear the private transport. Live
Monitor cancels a pending fallback query when a current stream update arrives,
and rejects snapshots older than its current verified observation. A fallback
has an eight-second deadline, replacing the previous two-minute deadline.

This corrects browser delivery stalls. It does not guarantee millisecond MT5
observations: upstream callbacks, full-book reconciliation, MT5 connectivity,
server load and network latency still determine actual source freshness.

Validation covers heartbeat-only stalls, source-old snapshots, unchanged-view
acknowledgements, mutation invalidation, out-of-order responses and dated outage
values. The desktop/mobile browser regression uses a complete 579-account,
4,817-position fixture, injects a 60-second-old snapshot while sending healthy
heartbeats, and requires automatic recovery with updated position prices.
