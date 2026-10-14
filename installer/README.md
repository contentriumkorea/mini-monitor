# Mini Monitor offline Windows installer

This locally built installer wraps the unchanged, verified 0.2.10 release ZIP.
GitHub Actions builds, tests and publishes it to the 0.2.10 release. Build locally with `scripts/Build-Installer.ps1` using
the Windows .NET Framework 4.8 C# compiler; no external setup tool is required.
The ZIP hash is pinned in the build script and installer. A future payload
requires updating both version and hash deliberately.

The wizard installs for the current user to `%LOCALAPPDATA%\Programs\Mini-Monitor`
by default, creates a Start menu entry and a Windows Apps removal entry, and
offers an optional desktop shortcut. It requires no administrator elevation.
After installation, the user can choose Run or Close. The app's startup
preference and `%LOCALAPPDATA%\AI-Mini-Monitor` data are never altered by setup.
The standalone EXE is not Authenticode signed.

Uninstaller files live in the sibling `Mini-Monitor-setup` directory, keeping
the app's strict update inventory valid. Setup and removal take the existing
updater's exclusive lock. Running target applications must be closed by the
user; setup never force-kills them. Full inventory verification occurs before
replacement/removal, and installation uses same-volume staging and renames.
Registration failures roll back the program and registration. Existing program
backups remain as `.mini-monitor-before-setup-*`; failed candidate programs are
retained for diagnosis. Arbitrary old portable installations are not deleted.
Use the existing portable location to upgrade it, or select a fresh location.

Reinstalling a newer package at the registered path updates the program;
downgrades and modified/unknown files are rejected. App-managed updates keep
working; their existing health/rollback/cleanup policy is unchanged. The
Windows Apps version label represents the last setup version, not subsequent
in-app updates. Removal verifies the *current* inventory, so added files from
an in-app update are handled. User settings, login data, unrelated shortcuts,
and startup commands targeting another installation remain intact.

`scripts/Test-Installer.ps1` runs actual silent installation, installed CLI,
updater inventory, changed/unknown-file protection, update-lock exclusion,
failure rollback, reinstall and removal checks. It requires no existing
installer-managed registration and uses a unique QA directory/Start menu group.
It also verifies that the existing user app, configuration hash and startup
registration remain unchanged. No login secrets are accessed.

The removal launcher copies its small executable to the user temporary
directory before removal to avoid deleting its own running image. This small
temporary executable is left for normal Windows temporary-file cleanup.
