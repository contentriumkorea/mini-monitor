# 0.3.1 verification

Scope: native settings/theme only. Existing installed app, accounts, startup settings and hardware outputs were left unchanged.

## Evidence

- Rust suite: 243 passed; 24 opt-in device/account/environment tests ignored.
- Native settings paint/command suite: 13 tests passed on 10 consecutive runs. Synchronous client paint is used instead of optional DWM full-content capture caching for inactive test windows.
- Preference forms: 5 tests; native selection/toggle/apply, Escape, password masking and high-DPI geometry covered.
- Relevant Python package/release suite: 19 passed.
- Clippy all targets, format and diff checks passed.
- Agent-desktop headless screenshots of synthetic main, taskbar, overlay and API forms inspected. No focus/physical-input fallback used; UIA on this host returned a truncated root-only tree, so semantic live interaction was not claimed.
- Shared smoke panel: 100 warmed repaints with stable GDI count.
- 15.0085-second inactive synthetic settings/form sample: zero CPU time increase, 17.28 MiB working set, 3.37 MiB private memory. This is not the complete monitoring process, a sustained workload test or a before/after comparison.
- Signed local release validated with the native updater: signature, ZIP inventory, isolated extraction, staged CLI version and newer-than-0.3.0 check passed.
- 313 packaged files; scanned text/source metadata had no real-key/private-key/user-profile-path patterns. Package uses explicit inputs, not user account/configuration folders.
- Fixed installer remains 474,112 bytes; SHA256 `0e00e7fa935ac00dd8705c6b220a072426c2a7aa831d7b26dc451b12ef2da914`.

## Review findings resolved

One independent review identified short-workarea overlap and light high-contrast combo contrast. Regression tests were observed failing before adaptive short layout and system-color pairs were implemented. Related selected/disabled combo cues use the same palette. No deferred minor finding remains.

## Rulings and limits

- The user's headless-only policy supersedes older physical UI permissions. Existing installation was preserved; unavailable UIA actions were not replaced with focus or input injection. Cost: physical keyboard/mouse interaction remains to be confirmed.
- Actual mixed-DPI monitor transitions, OS high-contrast switching, continuous whole-window repaint endurance and full monitoring-process performance are not inferred from pure geometry or synthetic samples. Cost: those environment-specific results remain unverified.
- The user explicitly approved continuous implementation and GitHub publication; no duplicate integration approval was requested. The fixed installer and unrelated release channels are not replaced. Existing local installation is not silently replaced by this UI/publication task.
