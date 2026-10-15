# Taskbar drag and desktop refresh verification

## Scope

User requested fixing termination while dragging the taskbar bar and reducing
CPU/RAM overhead. No release, installer replacement, credential access, or live
application restart was performed. Base: f6eae54d9a30fc0dc87363ea3f3f18187bbd7690.

## Evidence and implementation

- Windows Application Error records for Mini-Monitor.exe on October 15 show
  ucrtbase.dll / 0xc0000409 termination. This confirms termination, not the
  precise native stack or a proven single cause.
- The native WNDPROC previously called position presentation, persistence, and
  Tk widget updates synchronously. It now only accumulates a Python integer;
  the existing 100 ms Python UI poll applies the coalesced position outside
  WNDPROC. Hide/explicit position changes discard stale queued movement.
- Render frames only on new sensor revision, account/connection change, or
  renderer replacement. USB cadence, serial completion processing and shell
  visibility polling remain unchanged. Between-sample EMA animation is no
  longer rendered; fresh sensor data still arrives at the configured interval.
- Identical overlay/preview frames reuse existing presentation; unchanged tray
  labels do not rebuild native menus. This reduces allocation churn but does
  not remove Python/.NET or login-runtime baseline memory.

## Tests

Regression tests were observed failing before the relevant changes: deferred
drag/coalescing, queued movement cancellation, redundant tray refresh, redundant
rendering, redundant native overlay upload, and redundant Tk preview allocation.

An isolated native-window test sends 20 down/move/up sequences through Tcl's
Windows message loop, then updates Tk from the deferred callback. It passes.
Restoring the old immediate drag handler makes its callback-boundary assertion
fail. The user's exact fatal native abort was not reproduced in this harness;
physical drag on an installed rebuilt binary remains unverified.

- Focused final run: 338 passed (controller, overlay, setup, taskbar,
  native taskbar, tray, desktop session).
- Complete suite: 1168 passed, 1 skipped, 2 failed (before the final additional
  queued-drag lifecycle test; that test passes in the focused final run).
- Repeated isolated run confirms the two failures remain:
  - test_update_cleanup.py::test_stage_recycle_failure_can_be_retried_after_backup_was_recycled
  - test_update_helper.py::test_helper_accepts_only_correct_version_ack_or_preserves_backup[0.2.0-2000-0-healthy_backup_retained]
- Both fail at cleanup returning 0 instead of 1. Updater production code was
  not changed. Do not describe the entire suite as green or release-ready.
- Packaging snapshot test skips because the existing binary predates changes.
- compileall and git diff --check pass.
- Separate read-only code review identified pending input surviving hide/position
  changes. A failing regression and the cancellation fix address that finding.

## Performance

The user's unchanged installed process measured about 26.35% of one CPU core,
251.07 MiB working set and 211.66 MiB private memory over 10 seconds. This is
not system-normalized Task Manager CPU percentage and has no installed-after
comparison because that application was not replaced.

Reproducible isolated benchmark with synthetic sensor readings and an offscreen
native overlay, fresh processes, 2-second warmup and 10-second measurement:

| Screen pipeline | HEAD baseline | Working tree |
| --- | ---: | ---: |
| One-core CPU percent | 14.24 | 2.66 |
| Peak working set MiB | 77.59 | 73.24 |
| End private memory MiB | 56.94 | 54.55 |
| Rendered frames | 121 | 20 |
| Native uploads | 88 | 20 |

Run `.venv/Scripts/python.exe scripts/benchmark_desktop_refresh.py --baseline-ref HEAD`
and without `--baseline-ref` for the working tree. These measurements exclude
the hardware/.NET collector, USB transport and account subprocess; do not
extrapolate the roughly 81% CPU reduction to total installed application usage.
