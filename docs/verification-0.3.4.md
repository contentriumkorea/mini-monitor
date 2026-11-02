# 0.3.4 local flicker correction

No public release is authorized by this bug report. Existing installed0.3.3 remains until normal shutdown permits local replacement.

## Cause and proof

The visible preview refreshes once per second. It invalidated the entire settings window; repeated stable statuses still sent WM_SETTEXT, and monitor-off discovery replaced the stopped caption again within the same tick. The window-class erase brush could also be exposed before paint.

Regressions observed RED then GREEN:

- Changed preview invalidates only the bitmap rectangle; identical frame schedules no paint.
- Ten identical account updates send zero extra WM_SETTEXT messages, previously ten.
- A repeated monitor-off/discovered-device tick changes the caption zero times, previously twice.
- WM_ERASEBKGND does not overwrite the existing pixels with the class brush.
- Parent paint clips child controls. Existing DPI, corner, text, selection and preview integrity tests remain green.

Full native suite253 passed,24 opt-in environment cases ignored. Clippy all-targets, format, diff and local signed-bundle checks passed. The local bundle's signature/archive/current-install inventory validated successfully in verify-only mode.

Synthetic --live preview captures at946x673 had no difference in the left settings or top output controls; differences were restricted to changing CPU digits at(602,331)-(622,360). Still captures and repaint-region tests do not prove absence of every temporal flash; user optical confirmation remains required.

ZIP SHA256 `32eaf19c91c3f893082a9c58d2bf11523e2eff0afe4666fd1a3cae3d75ed09ed` in build/local-bundle-v0.3.4. No source/configuration/account files from the user's data folders were included.

Installation is pending manual normal shutdown: agent-desktop reports no addressable window for the tray-hidden installed process. No physical input, focus fallback or forced termination was used. The synthetic preview is closed after inspection.
