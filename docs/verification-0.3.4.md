# 0.3.4 local flicker correction

No public release is authorized by this bug report. This is a locally installed validation candidate.

## Cause and proof

The visible preview refreshes once per second. It invalidated the entire settings window; repeated stable statuses still sent WM_SETTEXT, and monitor-off discovery replaced the stopped caption again within the same tick. The window-class erase brush could also be exposed before paint.

Regressions observed RED then GREEN:

- Changed preview invalidates only the bitmap rectangle; identical frame schedules no paint.
- Ten identical account updates send zero extra WM_SETTEXT messages, previously ten.
- A repeated monitor-off/discovered-device tick changes the caption zero times, previously twice.
- WM_ERASEBKGND does not overwrite the existing pixels with the class brush.
- Parent paint clips child controls. Existing DPI, corner, text, selection and preview integrity tests remain green.

The first local build had253 native tests pass and24 opt-in environment cases ignored. Its signed local update reached healthy_backup_retained and startup acknowledgement; installed executable matched the candidate and the configuration hash was unchanged. The tray-hidden process could not be resolved by agent-desktop, including --force; after the user's explicit request to terminate it autonomously, native process management stopped only the PID with the verified installation path. No physical input was used.

Synthetic --live preview captures at946x673 had no difference in the left settings or top output controls; differences were restricted to changing CPU digits at(602,331)-(622,360). Still captures and repaint-region tests do not prove absence of every temporal flash; user optical confirmation remains required.

Initial ZIP SHA256 `32eaf19c91c3f893082a9c58d2bf11523e2eff0afe4666fd1a3cae3d75ed09ed` in build/local-bundle-v0.3.4. This earlier local candidate was superseded by the follow-up below. No source/configuration/account files from the user's data folders were included.

## Follow-up: preview-only flash and physical USB wipe

The user confirmed the remaining flash was in the settings preview, and separately reported top-to-bottom text replacement on the physical display.

- The preview painter directly exposed gradient/panel/empty-preview drawing before StretchDIBits. A real enhanced-metafile capture of the painter reproduced those intermediate operations. After buffering, the destination records only one completed BitBlt; existing native pixel/color/alignment tests still pass. PrintWindow onto an enhanced-metafile DC did not record any paint and was rejected as an invalid observation method; the test instead invokes the actual shared painter with the recording DC.
- USB delta detection combined all changed pixels into one bounding rectangle. The worker test changed two distant pixels and emitted198,108 bytes before correction, rather than16 bytes. Reusing bounded connected-region detection for packed RGB565 now sends two8-byte patches. The worker retains its baseline only after every patch succeeds; cancellation and failed writes keep existing fail-closed semantics.
- Synthetic six-value dashboard changes replay exactly to the target RGB565 image in both orientations. Release-mode measurements: landscape12 regions/14,256 bytes/403 microseconds planning; portrait6 regions/14,148 bytes/410 microseconds. These are synthetic measurements, not a physical-display latency guarantee.
- Preview buffers are temporary per paint; compatible DC/bitmap objects are deselected and released. No extra polling thread or update frequency was added.

The physical device has a streaming bitmap command in the audited protocol; this change reduces unnecessary scanned area, not a claim of hardware atomic frame presentation. A first connection or orientation change still requires a full frame. Optical confirmation of the final local candidate remains required.

## Final local application

- Full cargo test:255 passed,24 opt-in cases ignored,0 failures (`build/local034-regional-tests.log`). Clippy all-targets with denied warnings, release bins/examples build, format and diff checks passed.
- Final signed bundle: `build/local-bundle-v0.3.4-regional`; ZIP SHA256 `385867beaa6a61403b59808563a2029ed70e6b6a576f9e2c795f80ec92d190ce`. It validated against the pinned key and current installation inventory before the exact installed process was stopped.
- Verified updater journal reached healthy_backup_retained with startup acknowledgement. Installed CLI reports0.3.4, application SHA256 matches the final candidate, and configuration SHA256 is unchanged across replacement. Windows uninstall DisplayVersion is0.3.4.
- Agent-desktop semantic invocation enabled the actual USB monitor. Fresh accessibility observations report physical device connected, account status Ready and taskbar enabled; no account values were printed or captured.
- Main app20.5-second sample with settings preview and USB enabled: average machine CPU0.65%, working set75.5MiB (peak76.4), private memory31.7MiB (peak32.7). This short sample excludes Codex child-process resources and does not prove long-term absence of leaks.
- No public release/tag/upload/push was performed. The user subsequently confirmed both preview flash and USB scan appearance were normal.

## Follow-up: stable GPU model wrapping

The user reported the GPU model gaining a line when its percentage changed from one digit to two. The details width was derived from the live number's actual width. A native raster regression failed at NVIDIA GeForce RTX5070 Ti,9% to10%, landscape/page0.

The shared dashboard now reserves a two-digit percentage cell when wrapping details, while keeping the visible number right-aligned and retaining space for wider values. Regression comparisons cover0/1/8/9/10/11/88/99%, two GPU names, both orientations, API layout on/off, and both detail pages. Model/detail pixels and pagination stay identical across those values. Percent baseline and long-model paging tests still pass. Synthetic landscape/portrait BMP renders were visually inspected.

The first parallel full run failed the existing `smoke_panel_is_neutral_rounded_and_releases_its_gdi_objects` process-wide GDI count comparison (11 before,9 after). That test passed alone; the unchanged test shares its process with other tests allocating/freeing GDI objects. The full serial suite passed256 tests,24 opt-in cases ignored,0 failures (`build/local034-stable-details-serial-tests.log`). No unrelated production/test changes were made for this concurrency-sensitive check. Clippy all-targets, release build, formatting and diff checks passed.

Local candidate ZIP SHA256 `9d6c299a150902824ea3ed5ab90b8a21120fb7f52485a7ad45599d674e40e0a7`, in `build/local-bundle-v0.3.4-stable-details`. The signed updater reached healthy_backup_retained with startup acknowledgement; installed executable matches this candidate and configuration hash stayed unchanged. The actual USB monitor was re-enabled through an observed agent-desktop semantic control after restart. Public release remains unchanged; current physical optical confirmation of model stability is not yet available.

## Follow-up: complete single-line names and API detail order

The user clarified that hardware names must not wrap at all, including the GPU suffix Ti, and requested TOTAL above USED for API details. This supersedes hardware-name pagination, while preserving pagination for other overflowing details.

CPU/GPU model names now use a dedicated full-width row above the value area; temperature/power occupy the two subsequent left-side rows. Names fitting the row retain the original glyph size. Unusually long names are proportionally area-downsampled over their entire glyph rather than truncated or split. The model remains visible on every details page. Non-hardware details retain the two-digit width reservation.

Regressions observed before correction: the full unabridged GPU glyph comparison failed at x128,y4 where the missing suffix area contained only background; the one-row test detected wrapped model ink; API details were USED/TOTAL rather than TOTAL/USED. All are now green. The one-row detector was tightened to distinguish blue-grey model ink from near-white percentage antialiasing; no production behavior was changed to satisfy the original false positive.

Full serial native suite257 passed,24 opt-in cases ignored,0 failures (`build/local034-single-line-model-tests.log`). Clippy all-targets with denied warnings, release bins/examples build, format and diff checks passed. Native synthetic BMP previews in landscape and portrait were visually inspected: RTX5070 Ti is complete on one line and API TOTAL precedes USED. The full standard-model raster is compared against independently rendered unabridged native text, including every Ti pixel.

Signed local bundle: `build/local-bundle-v0.3.4-single-line-model`, ZIP SHA256 `ec685adf1b0aecb0b22d1017bd38a2673d43db0d450b6577cad8c3b2f0869c98`. Existing configuration/account data is excluded from the package. No public release or push was performed.

The installed app was gracefully exited through its observed agent-desktop Quit control; no forced termination or physical input was needed. The verified local updater reached healthy_backup_retained with startup acknowledgement. Installed application hash matches this candidate, configuration hash is unchanged, and the process remains running. The restarted settings window was then tray-hidden before the USB-enable action could be observed/invoked. Agent-desktop reports no addressable window; the user was asked to reopen settings. Final physical reconnection and account-status readback for this candidate remain pending until that window is available.
