# In-app alerts and Book Shifting clear control

- The bell in the app header shows the 50 most recently created, persisted Anti-Fraud alerts, including Deposit Alert. It updates when the authenticated Operations WebSocket reports new risk events. A lightweight 60-second HTTP refresh covers temporary WebSocket disconnections; neither path scans MT5.
- Unread state is stored per signed-in user in the browser. Opening the bell does not acknowledge or resolve a dealer risk event; use Operations for case actions.
- Controls → Book Shifting history → Clear shift history deletes only recorded `book_shift_events`. It requires an authenticated administrator and the typed phrase `CLEAR BOOK SHIFTS`; the action is written to the RMS audit log.
- Current `book_membership_state` is retained so the next MT5 snapshot does not recreate deleted historical events. Group presets and Book P&L history remain intact. New group movements will be recorded normally after the clear.
