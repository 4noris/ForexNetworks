# Plain live values and loading

Number updates now replace the displayed value directly. The shared number
component has no animation effect, background highlight, shadow, padding or
count interpolation. Missing-value handling and source precision are retained.

Frontend work that delayed visible data was reduced:

- Manager and Command Center queries stop when their retained page is hidden.
- Query-based live consumers now register explicit stream observers. Removing
  the final observer immediately unsubscribes the server view; other observers
  of that same view keep their shared subscription.
- Returning observers reactivate the stream and reuse a recent verified cached
  snapshot while the new snapshot arrives. Inactive streams are not restarted
  merely because the shared socket reconnects.
- Live Monitor retains its dated snapshot and graph when returning to the same
  profile, groups and filters. Changing that scope still clears the old view.
- Manager's clock pauses offscreen and checks actual source age on return.

This is a frontend-only deployment. The native MT5 connection was kept running,
and the production HTML and hashed entry assets were checked against the current
build. Broker timestamps, source refresh cadence, calculations and history were
not modified to suggest faster data than the source provides.

Validation: lint and native TypeScript contracts passed; 14 JavaScript tests
passed. Browser coverage checks plain exact numbers, hidden-page subscription
release, stopped offscreen polling and same-scope value retention. The full
desktop/mobile results are in `frontend/plain-live-values-e2e-final.log`.
The full browser run passed 58 checks; 4 audit-login checks were skipped.
