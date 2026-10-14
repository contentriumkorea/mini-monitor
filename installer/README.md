# Fixed online Windows installer

Build `scripts/Build-Installer.ps1` with the Windows .NET Framework 4.8 compiler.
No application ZIP is embedded. The pinned managed Ed25519 verifier/license and
small standalone removal tool are embedded; `dependencies.json` records provenance
and SHA-256. The build enforces <=1 MiB. Executables are not Authenticode signed.

Setup resolves the official GitHub Latest stable application every run, verifies
the existing Ed25519 signature, downloads the ZIP, and verifies every file before
replacement. Internet is required. Network/rate-limit failures can be retried.
No unsigned fallback or custom feed CLI exists. Same-version reinstall is allowed
to repair registration; newer installed versions are never downgraded, even when
in-app updates leave the registry DisplayVersion stale.

Per-user installation defaults to `%LOCALAPPDATA%\Programs\Mini-Monitor`.
Start menu, optional desktop shortcut and Windows Apps removal entry are created.
Login/settings/startup choices remain unchanged. Unknown/modified files block
replacement/removal. The app must be closed; it is not force-killed. Only setup's
own short-lived CLI health-check child may be killed after its 30-second timeout.

Staging is on the same volume, using the app updater's exclusive lock.
Cancellation is available before replacement; the short commit is noncancellable.
Download/verification failures preserve the app. Replacement/health/registration
failures restore the previous app and registration. Old/failed candidates remain
recoverable backups, not arbitrary directories to sweep. The sibling
`Mini-Monitor-setup` holds uninstall metadata outside the signed app inventory.
Removal keeps user data; its copied temporary EXE is left to Windows temp cleanup.

## Separate release lanes

- **Installer engine only:** explicit `installer/release-request.json` change or
  workflow dispatch builds/tests/publishes `installer-v1`, with `latest=false`.
  Normal app releases must not touch the request file. Assets are not overwritten;
  engine security/compatibility fixes require a deliberate new bootstrap release.
- **Application only:** sign with existing `scripts/sign_release.py` on the
  authorized signing machine. Place exactly `Mini-Monitor.zip`,
  `update-v2-manifest.json`, `update-v2-manifest.sig` in a dedicated bundle folder.
  With PYTHONPATH=src, run `python scripts/publish_app_release.py <bundle>` to
  validate. Add `--publish --notes-file <notes>` with an authorized GitHub CLI or
  Actions token and an existing app tag. Publication stays draft until all three
  assets upload, then becomes Latest. No Setup, legacy aliases, or private key
  is uploaded. The fixed bootstrap automatically follows the new Latest.
- Historical v0.2.10 offline Setup remains historical, not the online installer.

## Verification

`scripts/Test-OnlineInstaller.ps1 -Live` checks signed fixtures and the real public
descriptor. `scripts/Test-Installer.ps1` performs real download/install, inventory,
CLI, shortcut/registry, lock, rollback, reinstall and removal in a unique QA root.
It refuses to affect an existing installer registration and checks that current
user app/config/startup are unchanged. `pytest tests/test_publish_app_release.py`
checks app-only publication gating. Actual app updates retain their own existing
health/rollback/cleanup behavior.
