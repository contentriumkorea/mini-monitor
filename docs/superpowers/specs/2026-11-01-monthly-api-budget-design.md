# Start-date anchored monthly API budget

User approved changing the previous cumulative spending view to a recurring monthly budget. Keep the configured total and original start date; aggregate only the current cycle. This does not reset the OpenAI account, spending history, or prepaid balance.

Use UTC calendar-month anniversaries, not rolling 30-day windows or the first of each month. Clamp missing days to the month's last day without drifting the original anchor: January 31 -> February 28/29 -> March 31. The end boundary is exclusive. Unused budget is not carried over. UTC midnight is 09:00 Korea time.

The existing worker calculates the cycle on startup, checks it while waiting (at most 60 seconds), and checks again before accepting results. UI snapshot polling also invalidates expired values immediately. Cancel/discard replies from an older cycle, show pending rather than a fabricated zero, and fetch the new cycle. Keep the normal 600-second polling and 60-second manual/configuration admission interval. A natural cycle change may trigger its own immediate refresh.

Show the effective start and next reset in the settings summary. Do not rewrite JSON dates, touch keys, or change Codex. Existing configured dates become monthly anchors under the approved behavior. No additional toggle is required.

Verification covers exact midnight, year rollover, leap/non-leap February, day-31 anchoring, UTC conversion, previous-month response invalidation, unchanged total/original date, settings summary, and minimum request interval.

This task changes source and tests only; installed 0.2.12 and public release remain untouched pending a separate distribution request.

## Verification

- Initial monthly tests: 7 failing against cumulative behavior; implemented calendar periods and worker invalidation, then passed.
- Minimum configuration request interval regression failed first, then passed after preserving the configured delay when assigning initial period metadata.
- Final API/backend/display/integration/monthly group: 58 passed. Settings module separately: 86 passed.
- Whole suite run: 1141 passed, 1 skipped (stale packaging artifact), 86 setup errors at `test_setup.py::_shared_tk_interpreter` due to the pre-existing repeated-interpreter Tk theme-load failure. The settings tests passed when run in a fresh process. Two final summary/cached-value tests were added after whole-suite collection and passed in the final feature run.
- No real account requests, credential inspection, installer build, publication or installed-program replacement.
