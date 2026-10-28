# Dashboard accuracy and live delivery — 29 October 2026

The live dashboard reads the configured MT5 Manager through the existing native
C++ collector, Go gateway and authenticated WebSocket service. Test fixtures and
CSV files are not production live sources. Historical panels use recorded source
history; unavailable records or hedge allocations are not fabricated.

## Corrected display and calculations

- Shared number displays render the received amount immediately. The update
  animation highlights its background for 180ms; it does not interpolate money
  or position counts and does not run React renders on every animation frame.
- Missing, invalid and non-finite numbers display `Unavailable`, while a genuine
  zero remains zero. Shared currency, percentage and compact formatters follow
  the same rule.
- Live Monitor and Command Center retain up to eight decimal places in lots.
  Both SDK and cache builders preserve fractional volume before calculating
  totals, and use stable summation for portfolio lots. Small quoted prices retain
  eight decimal places rather than being rounded to zero at six places.
- The scoped SDK fallback no longer reconstructs missing account equity or free
  margin from balance plus credit, nor presents incomplete account money as a
  zero total. Its incomplete totals remain unavailable.
- Manager Overview separates deposit currencies instead of adding USD, USC and
  other account units under a dollar label. Its currency picker controls money
  cards, aggregate margin and account watchlists. Account/position counts still
  cover the complete published scope. Native group partials avoid an extra full
  account scan when the matching aggregate generation is available.
- The Manager equity-versus-balance comparison is no longer labeled as a monthly
  trend without monthly evidence. Money displays keep two decimal places, and
  lot displays preserve source precision.
- Card entrances are short, keep numbers visible, respect reduced motion and
  cancel their animation on unmount. Live P&L uses steps between recorded
  observations instead of a smoothed curve through unobserved prices.

## Stream changes

Native live-risk and Exposure views have a 100ms scheduling/cache floor, replacing
250ms. Work remains shared and bounded; actual cadence includes build, encoding
and network costs. Heavy historical reports keep their separate schedules.

The native-cadence test exposed a receive race: repeatedly cancelling a 10ms ASGI
receive could consume a subscription message without processing it, while global
heartbeats continued. The connection now keeps one pending receive task and
polls its completion without cancelling it. Shutdown cancels and awaits that
receiver with the outstanding query/subscription tasks.

The actual MT5 verification timestamp is preserved. A 100ms WebSocket schedule
does not establish 100ms broker-to-screen freshness, and does not turn an old
source observation into current data. Full-book verification age may span seconds
while newer source callbacks are delivered between full reconciliations.

## Validation

- Complete isolated backend suite: 439 passed.
- JavaScript units, including missing-value formatting: 13 passed.
- Frontend lint and production build: passed.
- Native TypeScript contracts: passed.
- Full desktop/mobile browser suite: 56 passed, 4 audit-login checks skipped.
- After restart, the current frontend build was served and 15 consecutive native
  readiness checks passed with no tracebacks or native publication errors. Full
  source verification age ranged from 0.795 to 4.579 seconds during that sample.
- Native callback test: three changed generations arrive with median local
  subscription latency below 240ms while the original source timestamp remains
  unchanged. This uses an isolated in-memory test source, not a broker latency
  measurement.
- Browser checks inspect every displayed amount during an update, exercise zero
  and missing values, verify eight-decimal lots and reduced motion, and check
  Manager USD/USC switching. Full results are in
  `frontend/dashboard-accuracy-e2e-final.log`.

Live notifications and trades are not sent as part of these tests. Existing
historical archive source restrictions and requirements for real hedge-allocation
records remain in force.
