"""Contract tests for the isolated Codex app-server account reader."""

from __future__ import annotations

import json
import hashlib
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_mini_monitor.ai.codex_account import CodexAccountService


@pytest.fixture(autouse=True)
def _no_bundled_runtime_except_in_explicit_tests(monkeypatch, request):
    """Existing RPC tests exercise their own fake external CLI, not the release binary."""
    import ai_mini_monitor.ai.codex_account as account_module

    if not request.node.name.startswith("test_bundled_"):
        monkeypatch.setattr(account_module, "_resolve_bundled_runtime", lambda: None, raising=False)


def test_bundled_app_server_starts_account_login_with_no_external_cli(monkeypatch, tmp_path: Path) -> None:
    import ai_mini_monitor.ai.codex_account as account_module

    runtime = tmp_path / "codex-app-server.exe"
    runtime.write_bytes(b"official runtime fixture")
    process = _FakeProcess()
    launch_args: dict = {}
    monkeypatch.setattr(account_module, "_resolve_bundled_runtime", lambda: runtime)
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: None)
    monkeypatch.setenv("PATH", "")

    def launch(argv, **kwargs):
        launch_args.update(argv=argv, **kwargs)
        return process

    monkeypatch.setattr(account_module.subprocess, "Popen", launch)
    service = CodexAccountService(cli_path=None, home=tmp_path / "isolated-home")
    try:
        assert service.begin_login()
        _eventually(lambda: any(event.kind == "auth_url" for event in service.drain_events()))
        assert launch_args["argv"][:3] == [str(runtime), "--listen", "stdio://"]
        assert launch_args["env"]["CODEX_HOME"] == str(tmp_path / "isolated-home")
    finally:
        service.close()


def test_bundled_runtime_rejects_hash_mismatch_and_external_cli_is_fallback(monkeypatch, tmp_path: Path) -> None:
    import ai_mini_monitor.ai.codex_account as account_module

    bundled = tmp_path / "codex-app-server.exe"
    bundled.write_bytes(b"tampered")
    monkeypatch.setattr(account_module, "resource_path", lambda _relative: bundled, raising=False)
    assert account_module._resolve_bundled_runtime() is None
    bundled.write_bytes(b"official runtime fixture")
    monkeypatch.setattr(account_module, "_BUNDLED_SHA256", hashlib.sha256(b"official runtime fixture").hexdigest(), raising=False)
    assert account_module._resolve_bundled_runtime() == bundled


def test_structured_http_status_code_auth_failure_is_classified_without_message() -> None:
    from ai_mini_monitor.ai.codex_account import _is_auth_error

    assert _is_auth_error({"data": {"httpStatusCode": 401, "message": "private token text"}})
    assert not _is_auth_error({"data": {"httpStatusCode": 429}})


@pytest.mark.parametrize("credits, expected, unlimited", [
    ({"hasCredits": True, "unlimited": False, "balance": "1250.50"}, "1250.50", False),
    ({"hasCredits": True, "unlimited": False, "balance": "60519.0000000000"}, "60519.0000000000", False),
    ({"hasCredits": False, "unlimited": False, "balance": "0"}, "0", False),
    ({"hasCredits": True, "unlimited": True, "balance": None}, None, True),
    ({"hasCredits": False, "unlimited": False, "balance": None}, None, False),
    (None, None, False),
    ({"hasCredits": True, "unlimited": False, "balance": "NaN"}, None, False),
    ({"hasCredits": True, "unlimited": False, "balance": "-1"}, None, False),
    ({"hasCredits": True, "unlimited": False, "balance": "private text"}, None, False),
])
def test_account_credits_are_separate_from_quota(monkeypatch, tmp_path, credits, expected, unlimited):
    import ai_mini_monitor.ai.codex_account as account_module

    process = _FakeProcess()
    process.limits["rateLimitsByLimitId"]["codex"]["credits"] = credits
    process.limits["rateLimitsByLimitId"]["codex_other"]["credits"] = {
        "hasCredits": True, "unlimited": False, "balance": "99999"
    }
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *a, **k: process)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        service.refresh()
        _eventually(lambda: service.snapshot().state == "ready")
        snapshot = service.snapshot()
        assert snapshot.credit_balance == expected
        assert snapshot.credits_unlimited is unlimited
        assert snapshot.ai.primary_value == "79%"
        assert ("CREDITS", "UNLIMITED" if unlimited else expected or "--") in snapshot.ai.fields
        service.logout()
        assert service.snapshot().credit_balance is None
        assert service.snapshot().credits_unlimited is False
    finally:
        service.close()


def test_credits_available_even_without_quota_windows(monkeypatch, tmp_path):
    import ai_mini_monitor.ai.codex_account as account_module

    process = _FakeProcess(limits={"rateLimits": {
        "limitId": "codex", "credits": {"hasCredits": True, "unlimited": False, "balance": "42"}
    }})
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *a, **k: process)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        service.refresh()
        _eventually(lambda: service.snapshot().state == "no_data")
        assert service.snapshot().credit_balance == "42"
        assert ("CREDITS", "42") in service.snapshot().ai.fields
        assert service.snapshot().ai.primary_value == "--"
    finally:
        service.close()


class _FakeOutput:
    def __init__(self) -> None:
        self.lines: queue.Queue[str | None] = queue.Queue()

    def readline(self, _limit: int = -1) -> str:
        line = self.lines.get()
        return "" if line is None else line


class _FakeInput:
    def __init__(self, process: "_FakeProcess") -> None:
        self.process = process

    def write(self, data: str) -> int:
        message = json.loads(data)
        self.process.sent.append(message)
        self.process.respond(message)
        return len(data)

    def flush(self) -> None:
        pass


class _FakeProcess:
    def __init__(self, *, limits: object | None = None, account: object | None = None) -> None:
        self.stdout = _FakeOutput()
        self.stdin = _FakeInput(self)
        self.stderr = None
        self.sent: list[dict] = []
        self.limits = limits or {
            "rateLimitsByLimitId": {
                "codex_other": {
                    "limitId": "codex_other",
                    "primary": {"usedPercent": 0, "windowDurationMins": 10080, "resetsAt": 1791000000},
                },
                "codex": {
                    "limitId": "codex",
                    "primary": {"usedPercent": 38, "windowDurationMins": 300, "resetsAt": 1791000000},
                    "secondary": {"usedPercent": 21, "windowDurationMins": 10080, "resetsAt": 1791000000},
                },
            }
        }
        self.account = account if account is not None else {
            "type": "chatgpt", "email": "test@example.com", "planType": "pro"
        }
        self.alive = True

    def respond(self, message: dict) -> None:
        method = message["method"]
        if method == "initialized":
            return
        if method == "initialize":
            result = {"userAgent": "fake", "platformFamily": "windows", "platformOs": "windows"}
        elif method == "account/read":
            result = {"account": self.account, "requiresOpenaiAuth": True}
        elif method == "account/rateLimits/read":
            result = self.limits
        elif method == "account/login/start":
            result = {"type": "chatgpt", "loginId": "login-1", "authUrl": "https://chatgpt.com/auth?secret=never-log"}
        elif method in {"account/login/cancel", "account/logout"}:
            result = {}
        else:
            raise AssertionError(f"unexpected request {method}")
        self.emit({"id": message["id"], "result": result})

    def emit(self, message: dict) -> None:
        self.stdout.lines.put(json.dumps(message) + "\n")

    def poll(self) -> int | None:
        return None if self.alive else 0

    def terminate(self) -> None:
        self.alive = False
        self.stdout.lines.put(None)

    def kill(self) -> None:
        self.terminate()

    def wait(self, timeout: float | None = None) -> int:
        self.terminate()
        return 0


def _eventually(predicate, *, seconds: float = 3) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_longest_codex_window_is_primary(monkeypatch, tmp_path: Path) -> None:
    """Catches selecting the named model pool or short window as the headline."""
    import ai_mini_monitor.ai.codex_account as account_module

    process = _FakeProcess()
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "설치 경로" / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    service = CodexAccountService(cli_path=None, home=tmp_path / "앱 홈")
    try:
        assert service.refresh()
        _eventually(lambda: service.snapshot().state == "ready")
        snapshot = service.snapshot()
        assert snapshot.ai.primary_value == "79%"
        assert snapshot.ai.primary_label == "7D LEFT"
        assert snapshot.windows[0].duration_mins == 10080
        assert snapshot.windows[1].remaining_percent == 62
        assert [m["method"] for m in process.sent[:4]] == [
            "initialize", "initialized", "account/read", "account/rateLimits/read"
        ]
    finally:
        service.close()


def test_fifteen_percent_used_is_eighty_five_remaining_not_named_pool(monkeypatch, tmp_path: Path) -> None:
    """Catches the original false 100%-remaining bug."""
    import ai_mini_monitor.ai.codex_account as account_module

    process = _FakeProcess(limits={
        "rateLimitsByLimitId": {
            "codex_other": {"limitId": "codex_other", "primary": {"usedPercent": 0, "windowDurationMins": 10080}},
            "codex": {"limitId": "codex", "primary": {"usedPercent": 15, "windowDurationMins": 10080}},
        }
    })
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        service.refresh()
        _eventually(lambda: service.snapshot().state == "ready")
        assert service.snapshot().ai.primary_value == "85%"
    finally:
        service.close()


def test_cli_selection_reuses_service_and_revalidates_off_caller_thread(monkeypatch, tmp_path: Path) -> None:
    import ai_mini_monitor.ai.codex_account as account_module

    chosen = tmp_path / "chosen.exe"
    chosen.write_bytes(b"test executable stand-in")
    processes = [_FakeProcess(), _FakeProcess()]
    resolved = []

    def resolve(path, *_args):
        resolved.append((path, threading.current_thread()))
        return chosen if path == chosen else tmp_path / "initial.exe"

    monkeypatch.setattr(account_module, "_resolve_cli", resolve)
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: processes.pop(0))
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        assert service.refresh()
        _eventually(lambda: service.snapshot().state == "ready")
        previous_generation = service.snapshot().generation
        assert service.set_cli_path(chosen)
        assert service.snapshot().generation == previous_generation + 1
        assert service.snapshot().windows == ()
        _eventually(lambda: service.snapshot().state == "ready")
        assert resolved[-1] == (chosen, service._worker)
        assert not service.set_cli_path(tmp_path / "missing.exe")
    finally:
        service.close()


@pytest.mark.parametrize("limits", [
    {"rateLimitsByLimitId": {"codex": {"limitId": "codex", "primary": None}}},
    {"rateLimitsByLimitId": {"codex_other": {"limitId": "codex_other", "primary": {"usedPercent": 0, "windowDurationMins": 10080}}}},
    {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": None, "windowDurationMins": 10080}}},
])
def test_missing_or_null_window_never_becomes_hundred(monkeypatch, tmp_path: Path, limits: dict) -> None:
    """Catches defaulting missing percentages or buckets to a full allowance."""
    import ai_mini_monitor.ai.codex_account as account_module

    process = _FakeProcess(limits=limits)
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        service.refresh()
        _eventually(lambda: service.snapshot().state == "no_data")
        assert service.snapshot().ai.primary_value != "100%"
        assert service.snapshot().windows == ()
    finally:
        service.close()


def test_oversized_numeric_usage_is_unknown_not_worker_crash(monkeypatch, tmp_path: Path) -> None:
    """Catches numeric conversion overflow from an external rate-limit payload."""
    import ai_mini_monitor.ai.codex_account as account_module

    process = _FakeProcess(limits={"rateLimits": {"limitId": "codex", "primary": {
        "usedPercent": 10**1000, "windowDurationMins": 10080,
    }}})
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        service.refresh()
        _eventually(lambda: service.snapshot().state == "no_data")
        assert service.snapshot().windows == ()
    finally:
        service.close()


def test_invalid_reset_time_does_not_hide_valid_percent(monkeypatch, tmp_path: Path) -> None:
    """Catches a huge optional reset timestamp crashing the reader."""
    import ai_mini_monitor.ai.codex_account as account_module

    process = _FakeProcess(limits={"rateLimits": {"limitId": "codex", "primary": {
        "usedPercent": 15, "windowDurationMins": 10080, "resetsAt": 10**1000,
    }}})
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        service.refresh()
        _eventually(lambda: service.snapshot().state == "ready")
        assert service.snapshot().ai.primary_value == "85%"
        assert service.snapshot().windows[0].resets_at is None
    finally:
        service.close()


def test_isolated_cli_environment_and_one_time_url(monkeypatch, tmp_path: Path) -> None:
    """Catches inherited credentials, cwd leakage, and repeated auth URL events."""
    import ai_mini_monitor.ai.codex_account as account_module

    process = _FakeProcess()
    captured: dict = {}
    executable = tmp_path / "한글 설치 경로" / "codex.exe"
    home = tmp_path / "내 앱 홈"
    monkeypatch.setenv("OPENAI_API_KEY", "private-key")
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "private-token")
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: executable)

    def launch(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return process

    monkeypatch.setattr(account_module.subprocess, "Popen", launch)
    service = CodexAccountService(cli_path=executable, home=home)
    try:
        assert service.begin_login()
        _eventually(lambda: any(event.kind == "auth_url" for event in service.drain_events()))
        assert captured["argv"][0] == str(executable)
        assert captured["cwd"] == home
        assert captured["env"]["CODEX_HOME"] == str(home)
        assert "OPENAI_API_KEY" not in captured["env"]
        assert "CODEX_ACCESS_TOKEN" not in captured["env"]
        assert service.drain_events() == ()
    finally:
        service.close()


def test_cancelled_login_cannot_publish_stale_auth_url(monkeypatch, tmp_path: Path) -> None:
    """Catches opening an OAuth URL after the user cancelled that login generation."""
    import ai_mini_monitor.ai.codex_account as account_module

    process = _FakeProcess()
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        assert service.begin_login()
        _eventually(lambda: any(message["method"] == "account/login/start" for message in process.sent))
        assert service.cancel_login()
        assert service.snapshot().state == "signed_out"
        assert not any(event.kind == "auth_url" for event in service.drain_events())
    finally:
        service.close()


def test_missing_cli_is_setup_required(monkeypatch, tmp_path: Path) -> None:
    """Catches treating an installation prerequisite as a transient network outage."""
    import ai_mini_monitor.ai.codex_account as account_module

    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: None)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        service.refresh()
        _eventually(lambda: service.snapshot().error_detail is not None)
        assert service.snapshot().state == "setup_required"
    finally:
        service.close()


@pytest.mark.skipif(os.name != "nt" or not shutil.which("codex.exe"), reason="installed Windows Codex CLI required")
def test_installed_official_cli_has_valid_openai_signature() -> None:
    """Catches a signer check that rejects a legitimately installed official CLI."""
    from ai_mini_monitor.ai.codex_account import _signed_by_openai

    assert _signed_by_openai(Path(shutil.which("codex.exe")))


@pytest.mark.skipif(os.name != "nt", reason="Windows system-directory contract")
def test_signature_verification_uses_os_system_powershell_not_environment(monkeypatch, tmp_path) -> None:
    """Catches PATH/CWD/SystemRoot selecting a fake signature verifier."""
    import ctypes
    import ai_mini_monitor.ai.codex_account as account_module

    buffer = ctypes.create_unicode_buffer(32768)
    assert ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer)) > 0
    system_dir = Path(buffer.value)
    fake_root = tmp_path / "fake-windows"
    fake_shell = fake_root / "System32/WindowsPowerShell/v1.0/powershell.exe"
    fake_shell.parent.mkdir(parents=True)
    fake_shell.write_bytes(b"untrusted")
    (tmp_path / "powershell.exe").write_bytes(b"untrusted")
    monkeypatch.setenv("SystemRoot", str(fake_root))
    monkeypatch.setenv("WINDIR", str(fake_root))
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PSMODULEPATH", str(fake_root))
    monkeypatch.chdir(tmp_path)
    captured = {}

    def run(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(account_module.subprocess, "run", run)
    assert account_module._signed_by_openai(tmp_path / "codex.exe")
    assert captured["command"][0] == str(system_dir / "WindowsPowerShell/v1.0/powershell.exe")
    assert captured["kwargs"]["env"]["PSModulePath"] == str(system_dir / "WindowsPowerShell/v1.0/Modules")
    assert sum(key.casefold() == "psmodulepath" for key in captured["kwargs"]["env"]) == 1
    assert captured["kwargs"]["creationflags"] == subprocess.CREATE_NO_WINDOW


@pytest.mark.skipif(os.name != "nt", reason="Windows system-directory contract")
@pytest.mark.parametrize("reported, value", [(0, ""), (19, "relative\\System32")])
def test_signature_verification_fails_closed_without_absolute_os_system_directory(
    monkeypatch, tmp_path, reported, value,
) -> None:
    """Catches fallback to PATH or environment after a failed Win32 resolution."""
    import ctypes
    import ai_mini_monitor.ai.codex_account as account_module

    def fake_directory(buffer, _capacity):
        buffer.value = value
        return reported

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(
        kernel32=SimpleNamespace(GetSystemDirectoryW=fake_directory),
    ))
    launched = []
    def run(command, **kwargs):
        launched.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(account_module.subprocess, "run", run)
    assert not account_module._signed_by_openai(tmp_path / "codex.exe")
    assert launched == []


@pytest.mark.skipif(os.name != "nt", reason="Windows CLI probe contract")
def test_codex_cli_help_probe_does_not_open_console(monkeypatch, tmp_path) -> None:
    import ai_mini_monitor.ai.codex_account as account_module

    candidate = tmp_path / "codex.exe"
    candidate.write_bytes(b"test executable")
    monkeypatch.setattr(account_module, "_signed_by_openai", lambda _path: True)
    seen = {}

    def run(command, **kwargs):
        seen.update(command=command, kwargs=kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="app-server")

    monkeypatch.setattr(account_module.subprocess, "run", run)
    assert account_module._resolve_cli(candidate, tmp_path, {}) == candidate
    assert seen["kwargs"]["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_legacy_session_provider_is_visibly_labeled_local() -> None:
    """Catches presenting log-derived limits as the new official account source."""
    from ai_mini_monitor.ai.codex_usage import CodexUsageSnapshot, CodexUsageStatus, to_ai_data

    assert to_ai_data(CodexUsageSnapshot(CodexUsageStatus.CONSENT_REQUIRED)).title == "CODEX LOCAL"


def test_login_completion_before_start_reply_still_refreshes(monkeypatch, tmp_path: Path) -> None:
    """Catches losing a fast OAuth callback before loginId is stored."""
    import ai_mini_monitor.ai.codex_account as account_module

    class FastLogin(_FakeProcess):
        def respond(self, message: dict) -> None:
            if message["method"] == "account/login/start":
                self.emit({"method": "account/login/completed", "params": {
                    "loginId": "login-1", "success": True, "error": None,
                }})
            super().respond(message)

    process = FastLogin()
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        assert service.begin_login()
        _eventually(lambda: service.snapshot().state == "ready")
        assert service.snapshot().login_pending is False
    finally:
        service.close()


def test_partial_limit_notification_causes_full_read(monkeypatch, tmp_path: Path) -> None:
    """Catches publishing a partial notification as if it were a complete quota."""
    import ai_mini_monitor.ai.codex_account as account_module

    process = _FakeProcess()
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        service.refresh()
        _eventually(lambda: service.snapshot().state == "ready")
        before = sum(m["method"] == "account/rateLimits/read" for m in process.sent)
        process.limits = {"rateLimits": {"limitId": "codex", "primary": {
            "usedPercent": 15, "windowDurationMins": 10080,
        }}}
        process.emit({"method": "account/rateLimits/updated", "params": {
            "rateLimits": {"limitId": "codex", "primary": {"usedPercent": 99}}
        }})
        _eventually(lambda: service.snapshot().ai.primary_value == "85%")
        assert sum(m["method"] == "account/rateLimits/read" for m in process.sent) > before
    finally:
        service.close()


def test_logout_discards_inflight_old_limit_response(monkeypatch, tmp_path: Path) -> None:
    """Catches a prior account response restoring quota after logout."""
    import ai_mini_monitor.ai.codex_account as account_module

    class DelayedLimit(_FakeProcess):
        defer = False
        held_id: int | None = None

        def respond(self, message: dict) -> None:
            if self.defer and message["method"] == "account/rateLimits/read":
                self.held_id = message["id"]
                return
            super().respond(message)

    process = DelayedLimit()
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        service.refresh()
        _eventually(lambda: service.snapshot().state == "ready")
        process.defer = True
        service.refresh()
        _eventually(lambda: process.held_id is not None)
        assert service.logout()
        process.emit({"id": process.held_id, "result": {
            "rateLimits": {"limitId": "codex", "primary": {"usedPercent": 0, "windowDurationMins": 10080}}
        }})
        _eventually(lambda: any(m["method"] == "account/logout" for m in process.sent))
        assert service.snapshot().state == "signed_out"
        assert service.snapshot().windows == ()
    finally:
        service.close()


def test_account_updated_clears_old_quota_before_new_account_read(monkeypatch, tmp_path: Path) -> None:
    """Catches showing the previous account's percentage during an account switch."""
    import ai_mini_monitor.ai.codex_account as account_module

    class DelayedAccount(_FakeProcess):
        hold_account = False
        held_id: int | None = None

        def respond(self, message: dict) -> None:
            if self.hold_account and message["method"] == "account/read":
                self.held_id = message["id"]
                return
            super().respond(message)

    process = DelayedAccount()
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        service.refresh()
        _eventually(lambda: service.snapshot().state == "ready")
        previous_generation = service.snapshot().generation
        process.hold_account = True
        process.emit({"method": "account/updated", "params": {"authMode": "chatgpt", "planType": "plus"}})
        _eventually(lambda: process.held_id is not None)
        assert service.snapshot().generation > previous_generation
        assert service.snapshot().windows == ()
        assert service.snapshot().email is None
    finally:
        service.close()


def test_close_during_cli_launch_does_not_leave_child_running(monkeypatch, tmp_path: Path) -> None:
    """Catches a subprocess created after close checked for one to terminate."""
    import ai_mini_monitor.ai.codex_account as account_module

    entered = threading.Event()
    release = threading.Event()
    process = _FakeProcess()
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")

    def launch(*_args, **_kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return process

    monkeypatch.setattr(account_module.subprocess, "Popen", launch)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    assert service.refresh()
    assert entered.wait(timeout=3)
    closer = threading.Thread(target=service.close)
    closer.start()
    time.sleep(0.05)
    release.set()
    closer.join(timeout=4)
    assert not closer.is_alive()
    _eventually(lambda: not process.alive)


def test_process_exit_is_recoverable_on_manual_refresh(monkeypatch, tmp_path: Path) -> None:
    """Catches a dead child poisoning the next JSON-RPC connection."""
    import ai_mini_monitor.ai.codex_account as account_module

    class ExitsOnAccount(_FakeProcess):
        def respond(self, message: dict) -> None:
            if message["method"] == "account/read":
                self.terminate()
                return
            super().respond(message)

    first = ExitsOnAccount()
    second = _FakeProcess()
    launches = iter((first, second))
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: next(launches))
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        service.refresh()
        _eventually(lambda: service.snapshot().state == "unavailable")
        service.refresh()
        _eventually(lambda: service.snapshot().state == "ready")
        assert service.snapshot().ai.primary_value == "79%"
    finally:
        service.close()


def test_api_key_account_is_not_shown_as_chatgpt_quota(monkeypatch, tmp_path: Path) -> None:
    """Catches using a Platform API-key account as a ChatGPT plan allowance."""
    import ai_mini_monitor.ai.codex_account as account_module

    process = _FakeProcess(account={"type": "apiKey"})
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        service.refresh()
        _eventually(lambda: service.snapshot().state == "auth_error")
        assert service.snapshot().windows == ()
        assert not any(m["method"] == "account/rateLimits/read" for m in process.sent)
    finally:
        service.close()


@pytest.mark.parametrize("notification_first", [False, True])
def test_old_logout_notification_does_not_cancel_new_login(monkeypatch, tmp_path: Path, notification_first: bool) -> None:
    """Catches an old logout's authMode:null invalidating a newly accepted login."""
    import ai_mini_monitor.ai.codex_account as account_module

    class LogoutNotifies(_FakeProcess):
        def respond(self, message: dict) -> None:
            if message["method"] == "account/logout":
                notice = {"method": "account/updated", "params": {"authMode": None, "planType": None}}
                reply = {"id": message["id"], "result": {}}
                for item in ((notice, reply) if notification_first else (reply, notice)):
                    self.emit(item)
                return
            super().respond(message)

    process = LogoutNotifies()
    next_process = _FakeProcess()
    launches = iter((process, next_process))
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: next(launches))
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        assert service.logout()
        assert service.begin_login()
        _eventually(lambda: any(e.kind == "auth_url" for e in service.drain_events()))
        assert any(m["method"] == "account/login/start" for m in next_process.sent)
        assert service.snapshot().login_pending
    finally:
        service.close()


def test_delayed_old_logout_notification_cannot_cancel_new_login(monkeypatch, tmp_path: Path) -> None:
    """Catches relying on a short timer rather than the logout operation's identity."""
    import ai_mini_monitor.ai.codex_account as account_module

    class DelayedLogoutNotice(_FakeProcess):
        def respond(self, message: dict) -> None:
            if message["method"] == "account/logout":
                self.emit({"id": message["id"], "result": {}})
                return
            super().respond(message)

    process = DelayedLogoutNotice()
    next_process = _FakeProcess()
    launches = iter((process, next_process))
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: next(launches))
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        assert service.logout()
        assert service.begin_login()
        _eventually(lambda: any(e.kind == "auth_url" for e in service.drain_events()))
        assert any(m["method"] == "account/login/start" for m in next_process.sent)
        service._incoming.put((process, {"method": "account/updated", "params": {"authMode": None}}))
        time.sleep(0.1)
        assert service.snapshot().login_pending
    finally:
        service.close()


def test_logout_cancels_pending_oauth_before_account_logout(monkeypatch, tmp_path: Path) -> None:
    """Catches a browser callback completing after local state alone was cleared."""
    import ai_mini_monitor.ai.codex_account as account_module

    process = _FakeProcess()
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        assert service.begin_login()
        _eventually(lambda: any(e.kind == "auth_url" for e in service.drain_events()))
        assert service.logout()
        _eventually(lambda: any(m["method"] == "account/logout" for m in process.sent))
        methods = [m["method"] for m in process.sent]
        assert methods.index("account/login/cancel") < methods.index("account/logout")
        process.emit({"method": "account/login/completed", "params": {
            "loginId": "login-1", "success": True, "error": None,
        }})
        assert service.snapshot().state == "signed_out"
    finally:
        service.close()


def test_logout_during_login_start_cancels_eventual_login_id(monkeypatch, tmp_path: Path) -> None:
    """Catches an in-flight login reply escaping cancellation on logout."""
    import ai_mini_monitor.ai.codex_account as account_module

    class DelayedStart(_FakeProcess):
        held_id: int | None = None

        def respond(self, message: dict) -> None:
            if message["method"] == "account/login/start":
                self.held_id = message["id"]
                return
            super().respond(message)

    process = DelayedStart()
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        assert service.begin_login()
        _eventually(lambda: process.held_id is not None)
        assert service.logout()
        process.emit({"id": process.held_id, "result": {
            "type": "chatgpt", "loginId": "login-1", "authUrl": "https://chatgpt.com/auth",
        }})
        _eventually(lambda: any(m["method"] == "account/logout" for m in process.sent))
        methods = [m["method"] for m in process.sent]
        assert methods.index("account/login/cancel") < methods.index("account/logout")
        assert not any(event.kind == "auth_url" for event in service.drain_events())
    finally:
        service.close()


@pytest.mark.parametrize("failed_method,error", [
    ("account/read", {"code": 401, "message": "private-token=do-not-show"}),
    ("account/rateLimits/read", {"code": -32603, "data": {"status": 403}, "message": "private-token=do-not-show"}),
])
def test_structured_auth_failure_clears_old_account(monkeypatch, tmp_path: Path, failed_method: str, error: dict) -> None:
    """Catches retaining old account quota after an authentication rejection."""
    import ai_mini_monitor.ai.codex_account as account_module

    class AuthRejects(_FakeProcess):
        reject = False

        def respond(self, message: dict) -> None:
            if self.reject and message["method"] == failed_method:
                self.emit({"id": message["id"], "error": error})
                return
            super().respond(message)

    process = AuthRejects()
    process.limits["rateLimitsByLimitId"]["codex"]["credits"] = {
        "hasCredits": True, "unlimited": False, "balance": "1250.50"
    }
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        service.refresh()
        _eventually(lambda: service.snapshot().state == "ready")
        process.reject = True
        service.refresh()
        _eventually(lambda: service.snapshot().state == "auth_error")
        snapshot = service.snapshot()
        assert snapshot.email is None
        assert snapshot.windows == ()
        assert snapshot.credit_balance is None
        assert ("CREDITS", "1250.50") not in snapshot.ai.fields
        assert "private-token" not in (snapshot.error_detail or "")
    finally:
        service.close()


def test_temporary_rpc_failure_retains_same_account_as_delayed(monkeypatch, tmp_path: Path) -> None:
    """Catches treating a transient server error as account revocation."""
    import ai_mini_monitor.ai.codex_account as account_module

    class TemporaryFailure(_FakeProcess):
        fail = False

        def respond(self, message: dict) -> None:
            if self.fail and message["method"] == "account/rateLimits/read":
                self.emit({"id": message["id"], "error": {"code": 503, "message": "transient"}})
                return
            super().respond(message)

    first = TemporaryFailure()
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: first)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        service.refresh()
        _eventually(lambda: service.snapshot().state == "ready")
        first.fail = True
        service.refresh()
        _eventually(lambda: service.snapshot().state == "delayed")
        snapshot = service.snapshot()
        assert snapshot.email == "test@example.com"
        assert snapshot.ai.primary_value == "79%"
    finally:
        service.close()


def test_ready_snapshot_is_stale_after_120_seconds_even_with_long_poll(tmp_path: Path) -> None:
    """Catches treating a 130-second-old quota as live with a 300-second poll setting."""
    from ai_mini_monitor.ai.codex_account import CodexLimitWindow

    service = CodexAccountService(cli_path=None, home=tmp_path / "home", refresh_seconds=300)
    old_time = datetime.now(timezone.utc) - timedelta(seconds=130)
    with service._lock:
        service._snapshot = replace(
            service._snapshot,
            state="ready", email="test@example.com", updated_at=old_time,
            windows=(CodexLimitWindow(10080, 15, None),),
        )
    try:
        assert service.snapshot().state == "delayed"
    finally:
        service.close()


@pytest.mark.parametrize("url", [
    "http://chatgpt.com/auth", "https://evil.example/auth",
    "https://chatgpt.com.evil.example/auth", "https://user@chatgpt.com/auth",
])
def test_untrusted_auth_url_never_reaches_ui(monkeypatch, tmp_path: Path, url: str) -> None:
    """Catches opening a spoofed or downgraded login URL."""
    import ai_mini_monitor.ai.codex_account as account_module

    class BadLogin(_FakeProcess):
        def respond(self, message: dict) -> None:
            if message["method"] == "account/login/start":
                self.emit({"id": message["id"], "result": {
                    "type": "chatgpt", "loginId": "login-1", "authUrl": url,
                }})
                return
            super().respond(message)

    process = BadLogin()
    monkeypatch.setattr(account_module, "_resolve_cli", lambda *_args: tmp_path / "codex.exe")
    monkeypatch.setattr(account_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    service = CodexAccountService(cli_path=None, home=tmp_path / "home")
    try:
        service.begin_login()
        _eventually(lambda: service.snapshot().state == "auth_error")
        assert not any(event.kind == "auth_url" for event in service.drain_events())
    finally:
        service.close()
