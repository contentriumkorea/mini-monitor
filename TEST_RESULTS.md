# Mini Monitor 0.2.0 verification record

This file is a release gate, not evidence that unrun checks passed. Fill in exact command outputs **before the final build**, then freeze release inputs. The final build stores a fingerprint of this file and all source inputs in `BUILD-INFO.json`; any subsequent source or document edit requires a new build.

| Gate | Result | Evidence / limitation |
|---|---|---|
| Fresh full source suite | PENDING | `.\.venv\Scripts\python.exe -m pytest -q` |
| Package/configuration/license/integrity tests | PENDING | `scripts\Build.ps1` runs pre- and post-build checks |
| Frozen PE GUI + CLI, onedir inventory, update helper/key | PENDING | `dist\Mini-Monitor\BUILD-INFO.json`, `SHA256SUMS.txt` |
| Packaged CLI version, read-only diagnosis and synthetic previews | PENDING | Use `Mini-Monitor-CLI.exe`; do not open a COM port |
| Bounded no-serial GUI launch and clean exit | PENDING | `Mini-Monitor.exe --no-serial --desktop-smoke 8` |
| Native dashboard, setup and overlay visual review | PENDING | Actual-size synthetic screenshots, opacity 0/50/100 |
| Signed two-frozen-version update and failure rollback | PENDING | Isolated disposable install trees; no user install modification |
| GitHub stable Release and downloaded ZIP SHA-256 | PENDING | Verify after publication |
| Live account authentication and rate-limit agreement | NOT TESTED | Requires user browser authentication; no automated login interaction |
| Physical USB screen and COM transmission | NOT TESTED | No serial/device writes during this release verification |
| Windows executable Authenticode signature | NOT SIGNED | Ed25519 update manifest signing does not sign the PE files |

Historical test counts, captures, USB experiments and previous `AI-Mini-Monitor` artifact hashes do not establish 0.2.0 release acceptance. The new release must use its own fresh test, native visual, frozen-binary and downloaded-asset evidence. No existing running app or earlier installation is stopped or deleted for QA.
