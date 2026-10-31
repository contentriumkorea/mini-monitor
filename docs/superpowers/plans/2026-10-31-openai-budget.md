# OpenAI budget implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Show official API spending against a user-configured USD total without browser automation.
**Architecture:** Separate optional configuration and single-worker budget service; typed immutable snapshots feed setup, runtime renderer and taskbar alongside Codex.
**Tech Stack:** Existing Python/Tk/Pillow, urllib, Decimal, Windows DPAPI; no new dependency.
**Spec:** docs/superpowers/specs/2026-10-31-openai-budget-design.md

## Global Constraints
- No browser, secret discovery, live key fixtures, account mutation, publish or installed-app replacement.
- Poll 600 seconds, manual admission minimum 60 seconds; UTC start midnight; USD <= 999999.99.
- Never label configured-total accounting as actual credit balance.

## Review Focus
- Failed or malformed response must not become measured zero (Task 1).
- Changed/disabled settings must discard old in-flight response (Task 1).
- Config saves from other existing controls must not overwrite API settings (Task 3).
- API must update with USB stopped and keep Codex independent (Task 3).
- Six-card labels/amounts and >100% values must not overlap gauges (Task 2).

### Task 1: Budget data, API and worker
Files: config.py, ai/openai_budget.py, tests/test_openai_budget.py.
Interfaces: OpenAIBudgetConfig; BudgetSnapshot.ai; OpenAIBudgetService.configure(config), refresh(), snapshot(), close().
- [x] Write and run failing tests for UTC query, decimal totals, pagination validation, interval/race/error boundaries, config roundtrip.
- [x] Implement bounded official client and separate background worker using existing redirect guard/DPAPI store.
- [x] Run `.venv/Scripts/python.exe -m pytest tests/test_openai_budget.py tests/test_openai_usage.py tests/test_config.py -q`; expect pass.
- Backend commit consolidated with Task 3 to keep the cross-module feature atomic.

### Task 2: Independent API display
Files: models.py, state.py, rendering/layout.py, rendering/renderer.py, ui/taskbar.py and renderer/taskbar tests.
Interfaces: DisplaySnapshot.api: AIData | None; RuntimeStateStore.update_api(); render_taskbar_frame(..., api=None).
- [x] Write and run failing tests for six-card landscape/portrait, disabled five-card compatibility and API-only taskbar selection.
- [x] Implement optional layouts and USD fields without changing Codex semantics.
- [x] Run renderer/state/taskbar/config tests; expect pass.
- Display commit consolidated with Task 3.

### Task 3: Settings and application lifecycle
Files: ui/openai_budget.py, ui/setup.py, app.py, desktop_session.py, controller.py and focused GUI/integration tests.
Interfaces: budget dialog emits validated config + optional newly typed key; app saves config under existing lock and configures service; controller mirrors budget snapshot.
- [x] Write and run failing tests for key-safe settings, runtime start/stop independence, config merge preservation.
- [x] Implement dialog, callbacks, shutdown and snapshot updates.
- [x] Run whole pytest suite and isolated desktop smoke; inspect rendered previews. Report unrelated baseline failures and live-key verification boundary explicitly.
- [x] Fresh whole-change review; fix important findings with red/green tests.

## Verification and delivery boundary

- Final whole suite: 1218 passed, 1 skipped in 170.05 seconds. The skipped packaging test requires a newly built artifact; existing artifacts predate this source change.
- Final feature tests: 47 passed. Independent review findings fixed with failing regression tests first.
- API-enabled desktop smoke with isolated LOCALAPPDATA and no actual key: exit 0. Synthetic landscape and portrait previews inspected.
- Live official API authentication/account totals are not verified: user must enter an organization Admin API key through the app, never in chat.
- No installer build, public release, installed-app replacement or account-setting change performed. Changes retained as one local feature commit.
