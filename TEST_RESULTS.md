# Mini Monitor 0.2.0 verification record

This is the verification snapshot at documentation freeze, not a claim that later release steps have already passed. The final build fingerprints this file and the other source inputs in `BUILD-INFO.json`; editing them after that build requires rebuilding. Final-artifact tests, downloaded-asset hashes and the public Release URL are recorded with the published Release rather than retroactively changing this source snapshot.

| Gate | Result at documentation freeze | Evidence / limitation |
|---|---|---|
| Fresh source suite | **800 passed, 1 skipped** | `.\.venv\Scripts\python.exe -m pytest -q` on 2026-10-04, 61.55 s. The single packaging-artifact test skipped because the existing candidate was built before the last source change. A new final build and post-build suite are required. |
| Candidate frozen build | **Passed** | `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Build.ps1` on source commit `8c2c949`: pre-build packaging checks 29 passed/1 expected artifact skip; post-build 29 passed/0 skipped. The onedir candidate had 1,525 files and both GUI and CLI PE files. This candidate predates a later notification fix and is **not** the final release build. |
| Candidate CLI and synthetic previews | **Passed** | Frozen `Mini-Monitor-CLI.exe --version` reported 0.2.0. With isolated temporary configuration and app data, read-only `--diagnose`, `--render-previews`, and seven `--preview` orientation/unknown-VRAM cases completed; generated PNGs had the expected 480×320 or 320×480 native dimensions. Diagnostics were not published. No COM port was opened. |
| Candidate bounded GUI launch | **Passed** | Frozen `Mini-Monitor.exe` ran with a unique temporary `--config`, isolated app-data directories, `--minimized --no-serial --desktop-smoke 8`; it exited 0 in about 10 s. The real HKCU Run value was unchanged. This is a no-serial launch check, not a live USB or account test. |
| Native dashboard, setup and overlay review | **Passed for source/native QA** | Synthetic native-size dashboard and portrait captures were inspected; focused source UI QA covered setup, overlay and opacity 0/50/100. Final frozen-binary visual confirmation remains a release gate. |
| Signed two-frozen-version update and rollback | **Passed for local candidate fixtures** | In disposable trees, an actual frozen 0.1.9 GUI parent exited, the Windows PowerShell helper installed an actual frozen 0.2.0 build, and the next launch acknowledged the target version. A separately re-signed fixture with an intentionally invalid target GUI PE rolled back to frozen 0.1.9 after launch failure. External config, a user-data marker and the real HKCU Run value were unchanged. This used a local archive-download fixture, **not** a live GitHub GUI update test; repeat against the final build is required. |
| Final frozen build, complete post-build suite and public download | **Not yet run at documentation freeze** | Final `BUILD-INFO.json`, source closure, signature, archive, SHA-256, installed-binary checks and GitHub Release/download verification must be completed after this document is frozen. |
| Live account authentication and rate-limit agreement | **Not tested** | Requires user browser authentication; no automated login interaction. |
| Physical USB screen and COM transmission | **Not tested** | No serial/device writes during release verification. |
| Windows executable Authenticode signature | **Not signed** | Ed25519 update-manifest signing does not sign the PE files. |

Existing `dist\AI-Mini-Monitor`, any running older app and user configuration were not modified for this QA. Local candidate reports and fixture archives are ignored QA artifacts, not release assets. Historical test counts or previous build hashes do not establish final 0.2.0 acceptance.

## 0.2.1 correction and release gate

After 0.2.0 was briefly published, a read-only live GitHub API probe revealed HTTP 415 on `/releases/latest`: the updater sent the release-asset `Accept: application/octet-stream` header to the JSON API. The 0.2.0 Release was returned to draft without rewriting its tag or assets. A new endpoint-specific regression test first reproduced that HTTP 415 behavior; the 0.2.1 source sends `application/vnd.github+json` only for the API URL while retaining the asset media type and existing URL, size and signature validation. The 0.2.0 test counts above remain historical evidence, **not** 0.2.1 acceptance. Final 0.2.1 build, full tests, frozen update/rollback QA, public download hashes and live read-only update discovery must be measured separately and reported with its Release.

## 0.2.3 source verification snapshot (2026-10-08)

The five-card dashboard, account-login-only settings, full integer credit display,
compact controls, and configurable transparent taskbar bar passed the complete
source suite: **992 passed, 1 skipped in 58.50 seconds**. The skip is the old
packaging artifact fingerprint; the final build must pass the fresh artifact gate.
Landscape and portrait credit displays were rendered and inspected at native size.
Public asset hashes and the live installed 0.2.2-to-0.2.3 update result are recorded
in the GitHub Release after publication, rather than predicted here.
