# Live-view latency work

Live Monitor already receives authenticated WebSocket snapshots and deltas. Its displayed age remains the age of the actual native MT5 account/position observation; this change does not refresh timestamps artificially or relax stale-source checks.

The native projection now maintains exact floating-point partial sums of account money by group and original currency. Group moves, account edits, position P&L changes and account deletions replace the old contribution with the new contribution. Each published cache generation includes an immutable aggregate snapshot.

Live Monitor reads indexed profiles for active accounts and any explicitly requested account, and combines group aggregates for the complete portfolio. Explicit exclusions are subtracted once. All scoped inactive accounts remain in portfolio totals. Group currency metadata takes precedence over profile currency. Unknown-currency money remains unavailable. Missing or mismatched aggregate coverage falls back to the full reader.

Plain Live Monitor JSON is checked for non-finite numbers without recursively copying every row. Other report types keep the existing FastAPI conversion. WebSocket framing, authentication, sequence checks and full account/position coverage remain in place.

The frontend uses the shared live subscription as its primary delivery path. It waits 1.5 seconds for the initial snapshot before trying a fallback read, and no longer creates a second operations socket to schedule redundant risk reads. Timed fallback remains available when the stream has not delivered current data.

An isolated synthetic benchmark with 100,000 portfolio accounts, 579 active accounts and 4,817 positions measured median view construction plus JSON preparation at 845.71 ms before and 101.93 ms after (three runs each). This is backend processing time on this workstation, not a broker-to-browser latency guarantee. Actual age also depends on the SDK, native reader and machine load.


The C++ collector also coalesces live queued updates by entity instead of retaining an ordinary replay queue of intermediate P&L changes. Bootstrap records and completion order are preserved. Full replacements, account cycle completions, source status and clean-login operations are ordering barriers. Deal events are unkeyed and remain lossless. Partial writer drains preserve the index for records in later barrier segments. The existing queue-size overflow/resynchronization policy remains in force.

The compiled native output-buffer test covers one million position updates, latest-state deletion, partial drains, bootstrap completion, replacement/cycle/clean barriers and separate deal add/update events. The prior collector binary is retained locally as a rollback artifact.


After installing the tested live queue, six public readiness observations reported native source ages of 4.14, 1.27, 1.47, 1.39, 1.88 and 1.78 seconds while the source held about 173,000 accounts. The first reading was during settling; the following five were 1.27?1.88 seconds. These observations measure native source freshness, not an authenticated browser round trip or a permanent latency bound.
