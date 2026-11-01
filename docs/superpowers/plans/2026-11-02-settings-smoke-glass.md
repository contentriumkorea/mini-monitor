# Smoke Glass Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace flat settings/forms with a coherent compact neutral smoke-glass UI and publish the verified update.

**Architecture:** Shared native theme/geometry helpers used by the existing SettingsWindow and OptionsWindow. Keep command IDs, native input semantics, DPI ownership and ApplicationCore unchanged. Use supported DWM chrome and a solid fallback, without an added rendering runtime.

**Tech Stack:** Rust, Win32 GDI/DWM, existing native controls and package/signing tools.

**Spec:** `docs/superpowers/specs/2026-11-02-settings-smoke-glass-design.md`

## Global Constraints

- Neutral charcoal; opaque text; no new WebView or continuous decoration.
- Preserve all command IDs, native keyboard behavior, secrets and installed app.
- Keep 9-point semibold controls and single OS DPI scaling.
- Release0.3.1 app assets only; fixed installer-v2 unchanged.

## Review Focus

- Rounded owner-drawn controls still emit native click/keyboard commands.
- Small work areas/high DPI never overlap footer, fields or preview.
- Repeated repaints/resizes do not leak GDI objects or poll wallpaper.
- Failure/disabled/focus states remain readable and password fields masked.
- Unsupported system material has a readable fallback rather than startup failure.

### Task 1: Shared theme and main settings

Files: create `rust/src/settings_theme.rs`, modify `rust/src/settings_window.rs`, `rust/src/lib.rs`; tests `rust/tests/settings_window.rs`, `rust/tests/settings_theme.rs`.

Interfaces: theme exposes neutral palette, rounded surface/button drawing and supported chrome; SettingsWindow retains public command/status API.

- [ ] Add failing geometry/paint checks for compact output cards, grouped settings, footer separation, neutral colors and rounded surfaces.
- [ ] Run targeted tests; observe missing layout/theme behavior.
- [ ] Implement shared static theme, main hierarchy, compact output switches and native title bar.
- [ ] Run targeted tests, native paint/resource probe and full cargo suite; commit.

### Task 2: Matching preference forms

Files: `rust/src/options_window.rs`, `rust/tests/options_window.rs`.

Interfaces: consumes Task1 theme; keeps Field and take_apply/translate_message semantics.

- [ ] Add failing owner-drawn apply/close and control-fit/paint tests, including password style.
- [ ] Implement matching form panels/buttons/inputs while retaining standard input behavior.
- [ ] Verify commands, Tab/Escape, apply values, DPI and no GDI growth; full suite; commit.

### Task 3: Native visual verification and release

Files: `rust/Cargo.toml`, lock/manifest, README version badge and `docs/releases/0.3.1.md`.

Interfaces: unchanged native packager -> signed ZIP/v2 manifest/signature -> fixed online installer.

- [ ] Agent-desktop inspect synthetic main/forms and interactions; record short CPU/memory and actual display limits. Fix confirmed failures by RED/GREEN tests.
- [ ] Run full cargo suite, clippy, format, relevant Python package/release tests.
- [ ] One fresh-context whole-change review under requesting-code-review; one tested fix pass for important issues.
- [ ] Build version0.3.1, audit package/source for private data, sign and verify against fixed installer contract in isolation.
- [ ] Commit/tag/publish authorized assets; verify public Latest, signatures/links and 0.3.0 update detection. Leave unrelated old releases/installer channels intact when fixed-link dependencies are unknown.
