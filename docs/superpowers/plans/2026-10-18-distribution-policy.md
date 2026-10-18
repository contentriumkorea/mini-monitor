# Distribution policy refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Republish the lightweight installer and user documentation under the user's replacement distribution rules.

**Architecture:** Preserve the signed latest-stable application acquisition and independent app-update channel. Publish installer-v2 without changing or replacing the existing 0.2.11 runtime assets.

**Tech Stack:** Existing Windows installer and release workflow.

**Spec:** User-provided AGENTS.md instructions in this conversation, 2026-10-18.

## Global Constraints

- Preserve settings, sign-in and existing installations during tests.
- Default installation creates desktop and Start menu shortcuts and removal registration.
- Installer remains below 1 MiB, downloads verified latest stable payload, and is never Latest.
- Public README/release prose describes user benefits, installation and operation rather than implementation.
- GitHub connector first; targeted CLI fallback follows installed plugin metadata.

## Review Focus

- Default shortcut checkbox accidentally remains disabled: real Wizard default assertion.
- Silent installation omits desktop shortcut: real installation and target assertion in clean CI account.
- Uninstall leaves generated desktop shortcut: real CI removal assertion.
- Existing installation gets damaged by QA: local lifecycle tests refuse existing registration; run on clean CI.
- Installer displaces latest app: compare latest release tag before and after publication.

### Task 1: Installer defaults and lifecycle

**Files:** installer/Setup.cs, installer/InstallerTests.cs, scripts/Test-Installer.ps1.
**Interfaces:** Existing Engine.Install and Wizard; existing CI lifecycle test.

- [ ] Add and run InstallerTests --defaults; expect failure for unchecked desktop option.
- [ ] Default GUI and quiet installation to desktop shortcut; retain explicit GUI opt-out.
- [ ] Assert desktop target and removal in the clean-account real installation test; safely read optional startup values.
- [ ] Build installer, run default test, full Python suite and clean CI signed acquisition/lifecycle tests.
- [ ] Commit scoped changes.

### Task 2: Public distribution and verification

**Files:** README.md, README_KO.md, installer/RELEASE-NOTES.md, installer/release-request.json, .github/workflows/publish-installer.yml.
**Interfaces:** installer-v2 fixed link; latest app remains v0.2.11.

- [ ] Rewrite public instructions; preserve license links, account privacy and legacy-update warning.
- [ ] Make installer revision 1.1.0 / installer-v2, preserving existing installer-v1.
- [ ] Scan changed text and installer inputs for secrets/private data.
- [ ] Fresh review, commit, push, wait for real Windows CI install/removal and release.
- [ ] Verify public asset, README link, size and latest app; report exact scope and remaining manual checks.
