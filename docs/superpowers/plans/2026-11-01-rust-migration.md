# Mini Monitor Rust Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task. Steps use checkbox syntax.

**Goal:** Replace the installed Python implementation with a functionally equivalent low-resource Rust Windows app, without changing the user's current installation during development.

**Architecture:** An isolated Rust workspace owns Windows UI, sensors, display rendering, USB protocol, accounts, secure configuration and update verification; share immutable snapshots between bounded workers. Preserve the existing Python source and use its behavior and fixtures as reference.

**Tech Stack:** Rust stable and Win32; pinned dependencies chosen after validating platform availability; Codex app-server remains an external required component where needed.

**Spec:** `docs/superpowers/specs/2026-11-01-rust-migration-design.md`

## Global Constraints

- No public release, installed-program replacement, credential inspection, or account changes in this task.
- Preserve existing user-data formats, DPAPI entropy, authentication home, approved USB device identification, 1–50 brightness safety and signed update contract.
- A native UI without monitoring, login, USB, update and compatibility is a prototype, not completion.
- Compare CPU, private memory, working set and resource growth against the original under matching conditions; never infer improvement from language alone.

## Review Focus

- Reject wrong USB identity and invalid brightness before serial I/O (Task 2).
- No stale data becomes 0 on failed network or monthly rollover (Task 3).
- Existing settings and test DPAPI credentials survive load, migration and rollback (Task 1).
- Cursor, DPI and fullscreen changes never hide or trap taskbar interaction (Task 4).
- Invalid update signatures, unsafe archives and failed activation preserve the current installed app (Task 5).

### Task 1: Baseline, workspace, settings and secret compatibility

Files: `rust/Cargo.toml`, `rust/src/config.rs`, `rust/src/secrets.rs`, `rust/tests/`, `docs/superpowers/plans/2026-11-01-rust-migration.md`.

- [ ] Record a timestamped baseline for existing process CPU, working set, private bytes, handles and duration without printing account data.
- [ ] Add tests using synthetic existing 0.2.13 config and temporary DPAPI secret: load, preserve fields, round-trip, reject invalid values; do not touch actual credentials.
- [ ] Implement configuration and DPAPI compatibility, atomic writes, then run `cargo test`.

### Task 2: USB protocol, sensors and shared snapshots

Files: `rust/src/device.rs`, `rust/src/sensors.rs`, `rust/src/model.rs`, `rust/tests/`.

- [ ] Port tested Rev-A bytes and reject incorrect VID/PID/serial, invalid brightness and malformed framebuffer before writes; compare against existing fixtures.
- [ ] Implement bounded CPU/RAM/GPU/VRAM sampling without fabricated fallback values; verify real adapter pairing and unsupported sensor behavior.
- [ ] Verify worker cancellation and independent USB/desktop operation with synthetic transports and compare snapshot results.

### Task 3: Account, billing and secure background workers

Files: `rust/src/codex.rs`, `rust/src/openai.rs`, `rust/src/worker.rs`, `rust/tests/`.

- [ ] Port app-server login lifecycle and usage/credits parsing with mocked RPC and retained per-user auth location.
- [ ] Port official Costs API, UTC month-anniversary logic, decimal totals, pagination limits, cancellation and error states; test Feb 28/29, day 31, rollover in flight, unauthorized and empty data.
- [ ] Test bounded polling, no secrets in logs, safe errors and cleanup on exit.

### Task 4: Native displays, taskbar and settings UI

Files: `rust/src/main.rs`, `rust/src/ui/`, `rust/src/render.rs`, `rust/tests/`.

- [ ] Port independent physical display, overlay and configurable taskbar presentation, scale and hit-testing; instantiate windows without busy cursor.
- [ ] Port settings UI, taskbar item choices, transparency, drag, screen rotation and brightness, startup, tray and close behavior.
- [ ] Verify accessibility, layout, nonblocking interactions and taskbar cursor on Windows; test 100% and high DPI plus fullscreen/monitor changes where available.

### Task 5: Update, integration, endurance and handoff

Files: `rust/src/update.rs`, `rust/src/startup.rs`, `rust/tests/`, packaging scripts scoped to development builds.

- [ ] Verify signature, archive inventory and safe rollback using valid and invalid synthetic releases; test upgrade compatibility from existing app in isolation.
- [ ] Run unit/integration suite, built executable smoke and existing app parity checklist; test supported real USB only when available.
- [ ] Run matched-duration performance and endurance measurements and compare with baseline including memory slope; inspect UI and report limitations.
- [ ] Complete independent review if available within current agent policy. Provide development build and concrete verification; no release or installed replacement unless asked.
