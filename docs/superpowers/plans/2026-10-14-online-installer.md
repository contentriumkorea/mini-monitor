# Online installer implementation plan

> **For agentic workers:** Use superpowers:executing-plans; test each change before implementation.

**Goal:** Ship one small fixed installer that installs the latest signed application, independently of future app releases.

**Architecture:** Keep the per-user transactional install/uninstall engine. Replace its embedded ZIP with a bounded GitHub downloader and Ed25519-verified release descriptor; publish bootstrap separately from application assets.

**Tech Stack:** C#/.NET Framework 4.8, WinForms, vetted managed Ed25519 library, PowerShell, existing Python release signing and GitHub Actions.

**Spec:** docs/superpowers/specs/2026-10-14-online-installer-design.md

## Global Constraints

- Fixed `installer-v1/Mini-Monitor-Setup.exe`, not Latest; target <= 1 MB.
- App releases contain `Mini-Monitor.zip`, `update-v2-manifest.json`, `update-v2-manifest.sig` only.
- Existing public key, signed schema and updater lock remain compatible. No unsigned fallback or arbitrary user feed URL.
- No changes to this user's installed app, settings, login, manufacturer distribution, or unrelated worktree.
- Preserve prior installation on verification, download, cancellation, registration and health-check failure.

## Review Focus

1. Redirects and signed paths must not escape allowed hosts or extraction root (Task 1 rejection tests).
2. Same installer must not downgrade an app whose uninstall registry version is stale (Task 2 version tests).
3. Cancellation must not cross the atomic replacement boundary (Task 2 cancellation tests).
4. Installer release must not become app Latest (Task 3 public endpoint assertions).
5. Downloaded package inventory must match signature, including unexpected ZIP entries and Windows path aliases (Task 1 fixtures).

### Task 1: Verified release acquisition

**Files:** installer/ReleaseClient.cs; installer/ReleaseClientTests.cs; scripts/Test-OnlineInstaller.ps1; scripts/Build-Installer.ps1; installer/dependencies.json.

**Interfaces:** `ReleaseClient.Latest(CancellationToken)` returns `ReleaseInfo`; `ReleaseClient.DownloadAndExtract(ReleaseInfo, string stage, CancellationToken, Action<string>)` produces stage/Mini-Monitor. A transport interface supports test fixtures without production command-line feed overrides.

- [ ] Write executable tests for signed valid manifests, tampering, invalid URLs/paths, duplicate entries, size limits, truncation and cancellation; two fixture versions use the same compiled verifier.
- [ ] Run tests, observe missing behavior; use reflection for initial missing API assertion rather than compile error.
- [ ] Pin a vetted .NET-compatible Ed25519 package and its hash/license. Implement bounded HTTPS transport, strict manifest validation and signed inventory extraction.
- [ ] Run `scripts/Test-OnlineInstaller.ps1`; expected all fixture cases pass; real public manifest verifies.
- [ ] Commit verified release client and tests.

### Task 2: Install transaction and small bootstrap

**Files:** installer/Setup.cs; installer/InstallerTests.cs; scripts/Build-Installer.ps1; scripts/Test-Installer.ps1; installer/README.md.

**Interfaces:** consumes Task 1 ReleaseInfo and acquisition functions; preserves Engine.Install's existing overload and adds cancellation. Registry DisplayVersion derives from downloaded release; installed source version controls downgrade prevention.

- [ ] Add failing tests for latest resolution, current-version no-op, downgrade refusal, cancellation preservation and post-placement health failure rollback.
- [ ] Replace embedded payload with acquisition; keep small embedded uninstaller, metadata ownership and shared lock. Allow cancellation before commit only. Check installed CLI health before registration completes.
- [ ] Build with `scripts/Build-Installer.ps1`; expected EXE <= 1 MB and no embedded app ZIP.
- [ ] Run fixture QA and `scripts/Test-Installer.ps1` against isolated roots; expected install/update/rollback/uninstall pass, real user app unchanged.
- [ ] Run `.venv/Scripts/python.exe -m pytest -q`; expected green suite. Commit.

### Task 3: Independent publication

**Files:** .github/workflows/publish-installer.yml; installer/release-request.json; scripts/Publish-AppRelease.ps1; README.md; installer/README.md.

**Interfaces:** bootstrap workflow explicitly creates non-Latest installer-v1; app publishing accepts pre-signed 3-file bundle, verifies it and excludes Setup assets. Existing app updater consumes unchanged Latest.

- [ ] Test app publisher dry-run rejects Setup/unsigned/incomplete bundles and emits exactly three assets.
- [ ] Implement separated publication and document local signing prerequisite (no private key uploaded).
- [ ] Perform one fresh-context security/whole-change review, fix important findings with regression tests.
- [ ] Publish bootstrap via GitHub plugin-triggered Actions, preserving historical app assets.
- [ ] Verify fixed asset size/hash, latest app unchanged, old updater detects current app; update primary README link only after installer exists.
- [ ] Commit/reconcile remote changes normally; report exact tested and untested boundaries.

Self-review: all spec sections map to Tasks 1–3. Client interface is consumed unchanged by engine. No app signing-key migration or app-version increment is needed to publish the bootstrap.
