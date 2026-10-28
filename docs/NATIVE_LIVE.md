# Native MT5 live data

Start the existing RMS UI and native services from the project directory:

```powershell
.\.venv\Scripts\python.exe native/start_native.py
```

The launcher loads project configuration and starts the C++ Manager SDK collector,
Go gateway on port 8000, and Python report API on loopback port 8001. The gateway
owns its child processes in a Windows job so stopping it also stops that instance's
API and collector. Do not launch a second instance on the same ports.

## Data path

The C++ collector subscribes to real Manager account, user, order, position and
quote events. It reads positions and authoritative account financial fields from
the synchronized SDK cache; missing account metrics use direct server requests.
Initial synchronization still requires complete account coverage. Equity and
margin are never reconstructed from balances or position profit.

The Go gateway keeps the latest state and per-entity versions. Python consumes
`/internal/native/changes` as compressed NDJSON, acknowledging each applied batch.
Only one batch is in flight. After a complete position row has been acknowledged,
changed-field patches carry updates such as profit and price without repeating
trade identity and contract metadata. Patches require a verified per-stream
baseline; new connections and schema changes use full rows. Baselines have both
entry and byte limits, and eviction falls back to full rows. Intermediate updates to one entity coalesce to its
latest state; explicit deletions are retained. The compatibility raw-event
history is bounded by both 32,768 events and 16 MiB, and discarded backing-array
references are cleared. It is independent of state replication. The gateway
uses a 384 MiB soft GC budget unless `GOMEMLIMIT` is explicitly configured. A source epoch change, invalid
cursor or pruned deletion history requires a new verified snapshot. Historical
deal reports continue to use the historical reporting path.

The internal stream and ACK endpoint require loopback access and the launcher's
private bearer token. They are not browser endpoints. Browser subscriptions and
read requests use the existing authenticated `/api/live/ws` connection and retain
the application's user/group authorization checks.

Python publishes immutable generations, updating changed account rows, login
indexes and financial totals incrementally. Verified profile/account/published
rows share immutable storage instead of retaining three full financial books.
Identity checks preserve newer callbacks during compaction, and only explicit
account-authoritative columns can override profile fields. Publisher allocation
failures trigger recovery rather than silently ending the publication thread. Floating-point partial sums preserve
small amounts across repeated replacements. The margin ranking heap is bounded.
The reporting SDK connection pumps group/symbol/time metadata, avoiding a second
copy of the native user/position/order book; direct report and recipient
verification requests remain available.

Book-shift monitoring reacts to native profile revisions and execution-book
configuration changes. It persists affected membership rows with equity and
floating P&L from the same verified generation, and retries failed persistence.
Unchanged membership rows retain their previous observation timestamps. The
observer retains only name/group identities, releasing obsolete financial
generations before a reconnect. CID
monitoring reads identities by the actual online login index instead of copying
the full account and position book.

## Freshness and recovery

`/api/ready` checks actual account/position generation age. Ten seconds remains a
failure threshold, not a target or a replacement timestamp. Native diagnostics
distinguish source observation age, stream read/apply/ACK time and Python
publication time. Measure sustained application and browser latency separately
from source callback latency.

On a brief interruption, Live Monitor retains the preceding same-scope verified
view and displays **Updates delayed · last verified ... ago**. It does not append
that interrupted response as a new chart observation. A scope change clears the
old view; a first load without verified data remains unavailable. Genuine SDK
disconnects and incomplete data still remain visible.

History archives are bound to their original Manager source. An archive/source
mismatch is independent of live streaming: choose a source-specific archive
instead of clearing or reassigning an existing broker's history.

## Verification

Regression tests cover coalesced bursts beyond the old raw event ring, exact
deletions, ACK flow control, lossless Go-to-Python gzip delivery, decompression
limits, interrupted streams, memory recovery, aggregate precision, immutable
generations, membership changes, CID lookup scope and WebSocket authorization.
The frontend retention test covers both desktop and mobile viewports.

For the optional cross-language Go test, set `RMS_TEST_PYTHON` to the RMS venv's
Python executable and `RMS_TEST_ROOT` to the project directory before running
`go test ./...` in `native/source`. Test records are confined to test fixtures;
live screens receive Manager data only.
