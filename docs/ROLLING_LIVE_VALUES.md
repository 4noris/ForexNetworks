# Rolling live values

The number display rolls outgoing/current digits vertically without a background
highlight or shadow. It uses only the prior and newly received glyphs, keeps the
DOM text and numeric value equal to the latest real amount, and does not calculate
intermediate financial amounts. Rapid updates cancel and retarget transitions;
duration is bounded to 160ms, and reduced-motion settings disable the effect.

Source timestamps remain the real MT5 verification times. Full-position
reconciliation currently runs every two seconds, with native callbacks delivered
between completed reconciliations. A 100ms browser-view schedule is not a promise
of 100ms broker-to-screen freshness. Source delays stay visible.

Native totals skip redundant subtraction/addition for unchanged finite account
fields. Exact sums and changes, deletions and previous published metrics remain
covered by regression tests. A local synthetic 173,000-account/1,560-active-account
benchmark reduced this function's replacement cycle from 10.312ms to 7.113ms;
this is not an end-to-end or broker-feed performance measurement.
