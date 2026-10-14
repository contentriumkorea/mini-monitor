# Online installer verification — 2026-10-14

Published fixed asset: `installer-v1/Mini-Monitor-Setup.exe`.
Size: 474112 bytes. Public SHA-256:
`5c7aa8f7269f17ead460b5eb2dd35f84ab067693750de7112309ce17fcff74a9`.
GitHub Actions run: https://github.com/contentriumkorea/mini-monitor/actions/runs/38542089760
All cloud build/verification/real install/rollback/removal/publication steps succeeded.

## Evidence

- Same compiled installer installed signed fixture 1.2.3 then 1.2.4; executable hash unchanged.
- 49 client/transaction checks passed: signature tampering, external URLs, reserved/traversal paths, duplicate JSON, unexpected ZIP entries, case aliases, oversized metadata fields, truncation, cancellation before/during download, downgrade refusal, health failure rollback, real 30-second child timeout and confirmed exit before directory rename.
- Malicious adjacent weak-named verifier DLL regression: bypass reproduced before fix; rejected after explicit embedded-byte loading.
- Wrong complete CLI version regression: suffix match reproduced before fix; exact version comparison now rejects it.
- Real public 0.2.10 network installation, full inventory, CLI, shortcut/registry, unknown-file protection, update lock, registration rollback, reinstall and removal passed locally and on GitHub Windows runner.
- Standalone setup was copied into an empty QA directory for live tests, without dependency sidecars. Existing user app, configuration hash and startup registration were preserved.
- Full Python suite: 1164 passed, 1 skipped. Skip is pre-existing packaged artifact built from an older source snapshot.
- Public installer downloaded; SHA-256 matches GitHub asset digest.
- After bootstrap publication, app Latest remained v0.2.10; all four historical v0.2.10 asset hashes/sizes unchanged.
- Actual old 0.2.9 updater detected 0.2.10 and pending notification; actual 0.2.10 updater returned idle/latest with no notification.
- README and README_KO primary download now point to the fixed lightweight installer, not historical offline Setup.

## Decisions and limits

- Continued approved native implementation without a redundant approval cycle; future interface adjustments remain reversible.
- Same-version setup performs an explicit repair reinstall instead of no-op; cost is downloading the current package again.
- App-only publisher uses Python and existing signature validation instead of an additional PowerShell wrapper; maintainers need the project Python environment and authorized publishing credentials.
- Suffix-only health match was upgraded from review Minor to Important because it could accept a wrong version; strict CLI output is now required.
- Fresh strongest-model read-only review completed. Both Important findings were addressed. The asynchronous Kill race was established by code analysis, not a deterministic pre-fix runtime failure; the timeout lifecycle fixture passes after waiting for exit.
- Redirect host policy is tested; arbitrary live redirect chains and every Windows/4K machine were not exercised. Crypto internals were not independently audited; vetted pinned upstream code is used. EXE remains Authenticode-unsigned and may show Windows reputation warnings.
- The fixed installer is not rebuilt for normal app releases. Security/Windows compatibility changes to the installer still require a deliberate separate installer release.
- Current app version remains 0.2.10: this delivery changes setup/distribution, not application functionality.
