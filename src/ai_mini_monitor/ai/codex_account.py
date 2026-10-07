# SPDX-License-Identifier: GPL-3.0-or-later
"""Isolated, account-backed Codex limits through the official app-server.

The Codex CLI owns authentication.  This module never opens credentials or
session logs, creates a Codex thread, or asks a model to do work.
"""

from __future__ import annotations

import json
import math
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..models import AIData, AIProviderKind, SyncStatus
from ..security.windows_system import pin_powershell_modules, windows_powershell_paths


_MAX_LINE = 1024 * 1024
_RPC_TIMEOUT = 8.0
_MAX_RESET = 253_402_300_799
_STALE_SECONDS = 120
_AUTH_HOSTS = {"chatgpt.com", "auth.openai.com"}
_SENSITIVE_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "CHATGPT_ACCESS_TOKEN")


class _RpcError(RuntimeError):
    """Classified server failure without carrying response text or credentials."""

    def __init__(self, *, authentication: bool) -> None:
        super().__init__("codex_auth_error" if authentication else "codex_rpc_error")
        self.authentication = authentication


def _is_auth_error(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    candidates = (value, value.get("data"))
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for name in ("code", "status", "statusCode", "httpStatus", "httpStatusCode"):
            code = candidate.get(name)
            if type(code) is int and code in (401, 403):
                return True
    return False


@dataclass(frozen=True, slots=True)
class CodexLimitWindow:
    duration_mins: int
    used_percent: float
    resets_at: datetime | None

    @property
    def remaining_percent(self) -> float:
        return 100.0 - self.used_percent


@dataclass(frozen=True, slots=True)
class CodexAccountSnapshot:
    generation: int
    state: str
    email: str | None
    plan_type: str | None
    login_pending: bool
    windows: tuple[CodexLimitWindow, ...]
    updated_at: datetime | None
    error_detail: str | None
    ai: AIData
    credit_balance: str | None = None
    credits_unlimited: bool = False


@dataclass(frozen=True, slots=True)
class CodexAccountEvent:
    kind: str
    generation: int
    auth_url: str | None = None


def _display(
    state: str,
    windows: tuple[CodexLimitWindow, ...] = (),
    updated_at: datetime | None = None,
    error_detail: str | None = None,
    credit_balance: str | None = None,
    credits_unlimited: bool = False,
) -> AIData:
    credit_field = ("CREDITS", "UNLIMITED" if credits_unlimited else credit_balance or "--")
    if windows:
        main = windows[0]
        fields: tuple[tuple[str, str], ...] = tuple(
            (f"{_duration_label(window.duration_mins)} LEFT", f"{round(window.remaining_percent)}%")
            for window in windows[1:2]
        )
        return AIData(
            provider=AIProviderKind.CODEX_ACCOUNT,
            title="CODEX",
            status=SyncStatus.DELAYED if state == "delayed" else SyncStatus.OK,
            primary_value=f"{round(main.remaining_percent)}%",
            primary_label=f"{_duration_label(main.duration_mins)} LEFT",
            fields=fields + (credit_field,),
            last_sync=updated_at,
            budget_ratio=main.used_percent / 100.0,
            budget_label=f"{_duration_label(main.duration_mins)} USED {round(main.used_percent)}%",
            error_detail=error_detail,
        )
    labels = {
        "setup_required": ("SETUP", "CODEX CLI", SyncStatus.SETUP_REQUIRED),
        "signed_out": ("LOGIN", "CHATGPT", SyncStatus.AUTH_ERROR),
        "login_pending": ("LOGIN", "PENDING", SyncStatus.DELAYED),
        "auth_error": ("LOGIN", "ERROR", SyncStatus.AUTH_ERROR),
        "no_data": ("--", "NO LIMIT", SyncStatus.DELAYED),
        "unavailable": ("--", "UNAVAILABLE", SyncStatus.DELAYED),
        "delayed": ("--", "DELAYED", SyncStatus.DELAYED),
    }
    value, label, sync = labels.get(state, labels["unavailable"])
    return AIData(
        provider=AIProviderKind.CODEX_ACCOUNT,
        title="CODEX",
        status=sync,
        primary_value=value,
        primary_label=label,
        fields=(credit_field,),
        error_detail=error_detail,
    )


def _duration_label(minutes: int) -> str:
    if minutes % 1440 == 0:
        return f"{minutes // 1440}D"
    if minutes % 60 == 0:
        return f"{minutes // 60}H"
    return f"{minutes}M"


def _parse_window(value: Any) -> CodexLimitWindow | None:
    if not isinstance(value, dict):
        return None
    duration = value.get("windowDurationMins")
    used = value.get("usedPercent")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
        return None
    if isinstance(used, bool) or not isinstance(used, (int, float)):
        return None
    try:
        used = float(used)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(used) or not 0 <= used <= 100:
        return None
    reset = value.get("resetsAt")
    resets_at = None
    if isinstance(reset, (int, float)) and not isinstance(reset, bool) and 0 <= reset <= _MAX_RESET:
        try:
            resets_at = datetime.fromtimestamp(reset, timezone.utc)
        except (ValueError, OverflowError, OSError):
            pass
    return CodexLimitWindow(duration, used, resets_at)


def _codex_bucket(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    buckets = result.get("rateLimitsByLimitId")
    bucket = buckets.get("codex") if isinstance(buckets, dict) else None
    if not isinstance(bucket, dict):
        legacy = result.get("rateLimits")
        bucket = legacy if isinstance(legacy, dict) and legacy.get("limitId") == "codex" else None
    if not isinstance(bucket, dict) or bucket.get("limitId") != "codex":
        return {}
    return bucket


def _parse_credits(result: Any) -> tuple[str | None, bool]:
    credits = _codex_bucket(result).get("credits")
    if not isinstance(credits, dict):
        return None, False
    if type(credits.get("unlimited")) is not bool or type(credits.get("hasCredits")) is not bool:
        return None, False
    if credits["unlimited"]:
        return None, True
    balance = credits.get("balance")
    # Preserve the server's decimal precision, without displaying arbitrary text
    # or turning absent credit information into a zero balance.
    if not isinstance(balance, str) or not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,32})?", balance):
        return None, False
    return balance, False


def _parse_windows(result: Any) -> tuple[CodexLimitWindow, ...]:
    bucket = _codex_bucket(result)
    found = [window for key in ("primary", "secondary") if (window := _parse_window(bucket.get(key)))]
    return tuple(sorted(found, key=lambda item: item.duration_mins, reverse=True))


def _valid_auth_url(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 8192:
        return False
    try:
        url = urlsplit(value)
    except ValueError:
        return False
    return (
        url.scheme == "https"
        and url.hostname in _AUTH_HOSTS
        and url.username is None
        and url.password is None
        and url.port in (None, 443)
        and not url.fragment
    )


def _safe_environment(home: Path) -> dict[str, str]:
    environment = dict(os.environ)
    for key in tuple(environment):
        upper = key.upper()
        if upper in _SENSITIVE_ENV or (
            upper.startswith(("OPENAI_", "CODEX_", "CHATGPT_"))
            and any(part in upper for part in ("KEY", "TOKEN", "SECRET", "AUTH"))
        ):
            environment.pop(key, None)
    environment["CODEX_HOME"] = str(home)
    return environment


def _signed_by_openai(path: Path) -> bool:
    powershell_paths = windows_powershell_paths()
    if powershell_paths is None:
        return False
    powershell, modules = powershell_paths
    environment = _safe_environment(path.parent)
    environment["MINI_MONITOR_CODEX_CLI"] = str(path)
    # A parent PowerShell 7 PSModulePath can make Windows PowerShell import an
    # incompatible Security module and falsely reject a valid signature.
    pin_powershell_modules(environment, modules)
    script = (
        "$s=Get-AuthenticodeSignature -LiteralPath $env:MINI_MONITOR_CODEX_CLI; "
        "if($s.Status -eq 'Valid' -and $s.SignerCertificate -and "
        "$s.SignerCertificate.GetNameInfo("
        "[System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName,$false) "
        "-ceq 'OpenAI OpCo, LLC'){exit 0}else{exit 1}"
    )
    try:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=6,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _resolve_cli(chosen: Path | None, home: Path, environment: dict[str, str]) -> Path | None:
    candidates: list[Path] = []
    if chosen is not None:
        candidates.append(Path(chosen))
    else:
        local = environment.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe")
        found = shutil.which("codex.exe")
        if found:
            candidates.append(Path(found))
    for candidate in candidates:
        try:
            actual = candidate.resolve(strict=True)
            if not actual.is_file() or actual.suffix.lower() != ".exe" or not _signed_by_openai(actual):
                continue
            probe = subprocess.run(
                [str(actual), "app-server", "--help"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=home,
                env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=6,
                check=False,
            )
            if probe.returncode == 0 and "app-server" in probe.stdout:
                return actual
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            continue
    return None


class CodexAccountService:
    """One non-blocking account service per Mini Monitor application process."""

    def __init__(self, *, cli_path: Path | None, home: Path, refresh_seconds: int = 60) -> None:
        if not isinstance(refresh_seconds, int) or refresh_seconds < 60:
            raise ValueError("refresh_seconds must be at least 60")
        self._cli_path = Path(cli_path) if cli_path is not None else None
        self._home = Path(home).absolute()
        self._refresh_seconds = refresh_seconds
        self._lock = threading.RLock()
        self._commands: queue.Queue[tuple[str, int, str | None]] = queue.Queue(maxsize=32)
        self._incoming: queue.Queue[tuple[Any, dict[str, Any] | None]] = queue.Queue()
        self._events: queue.Queue[CodexAccountEvent] = queue.Queue()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._reader: threading.Thread | None = None
        self._process: Any = None
        self._next_id = 1
        self._login_id: str | None = None
        self._early_login: dict[str, bool] = {}
        self._expected_logout_notice: int | None = None
        self._polling = False
        self._next_poll = float("inf")
        self._backoff = refresh_seconds
        self._refresh_queued = False
        self._snapshot = CodexAccountSnapshot(
            0, "setup_required", None, None, False, (), None, None,
            _display("setup_required"),
        )

    def snapshot(self) -> CodexAccountSnapshot:
        with self._lock:
            value = self._snapshot
        if value.state == "ready" and value.updated_at is not None:
            age = (datetime.now(timezone.utc) - value.updated_at).total_seconds()
            if age > _STALE_SECONDS:
                return replace(value, state="delayed", ai=_display("delayed", value.windows, value.updated_at, "stale", value.credit_balance, value.credits_unlimited))
        return value

    def drain_events(self) -> tuple[CodexAccountEvent, ...]:
        events: list[CodexAccountEvent] = []
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return tuple(events)
            if event.kind == "auth_url":
                with self._lock:
                    if event.generation != self._snapshot.generation or not self._snapshot.login_pending:
                        continue
            events.append(event)

    def _ensure_worker(self) -> bool:
        with self._lock:
            if self._stop.is_set():
                return False
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, name="mini-monitor-codex-account", daemon=True)
                self._worker.start()
            return True

    def _enqueue(self, operation: str, generation: int, argument: str | None = None) -> bool:
        if not self._ensure_worker():
            return False
        try:
            self._commands.put_nowait((operation, generation, argument))
        except queue.Full:
            return False
        return True

    def begin_login(self) -> bool:
        with self._lock:
            if self._stop.is_set() or self._snapshot.login_pending:
                return False
            generation = self._snapshot.generation + 1
            self._snapshot = CodexAccountSnapshot(
                generation, "login_pending", None, None, True, (), None, None,
                _display("login_pending"),
            )
        if self._enqueue("login", generation):
            return True
        self._publish(generation, "unavailable", error="queue_full")
        return False

    def cancel_login(self) -> bool:
        with self._lock:
            if self._stop.is_set() or not self._snapshot.login_pending:
                return False
            old_id = self._login_id
            generation = self._snapshot.generation + 1
            self._snapshot = CodexAccountSnapshot(
                generation, "signed_out", None, None, False, (), None, None,
                _display("signed_out"),
            )
        self._enqueue("cancel", generation, old_id)
        return True

    def refresh(self) -> bool:
        with self._lock:
            if self._stop.is_set():
                return False
            if self._refresh_queued:
                return True
            self._refresh_queued = True
            generation = self._snapshot.generation
        if self._enqueue("refresh", generation):
            return True
        with self._lock:
            self._refresh_queued = False
        return False

    def set_cli_path(self, path: Path | None) -> bool:
        """Queue a CLI switch; signature and app-server validation stay off Tk."""

        if path is not None:
            try:
                selected = Path(path).resolve(strict=True)
            except (OSError, ValueError, TypeError):
                return False
            if not selected.is_file() or selected.suffix.lower() != ".exe":
                return False
        else:
            selected = None
        with self._lock:
            if self._stop.is_set():
                return False
            generation = self._snapshot.generation + 1
            self._cli_path = selected
            self._login_id = None
            self._early_login.clear()
            self._expected_logout_notice = None
            self._refresh_queued = False
            self._snapshot = CodexAccountSnapshot(
                generation, "setup_required", None, None, False, (), None, None,
                _display("setup_required"),
            )
        if self._enqueue("cli", generation):
            return True
        self._publish(generation, "unavailable", error="queue_full")
        return False

    def logout(self) -> bool:
        with self._lock:
            if self._stop.is_set():
                return False
            login_id = self._login_id if self._snapshot.login_pending else None
            generation = self._snapshot.generation + 1
            self._snapshot = CodexAccountSnapshot(
                generation, "signed_out", None, None, False, (), None, None,
                _display("signed_out"),
            )
            self._expected_logout_notice = generation
        return self._enqueue("logout", generation, login_id)

    def set_polling(self, enabled: bool) -> None:
        with self._lock:
            self._polling = bool(enabled)
            self._next_poll = time.monotonic() if enabled else float("inf")
        if enabled:
            self._ensure_worker()

    def close(self) -> None:
        self._stop.set()
        self._dispose_process()
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=3)

    def _publish(
        self,
        generation: int,
        state: str,
        *,
        email: str | None = None,
        plan: str | None = None,
        windows: tuple[CodexLimitWindow, ...] = (),
        updated: datetime | None = None,
        error: str | None = None,
        pending: bool = False,
        credit_balance: str | None = None,
        credits_unlimited: bool = False,
    ) -> None:
        with self._lock:
            if generation != self._snapshot.generation or self._stop.is_set():
                return
            self._snapshot = CodexAccountSnapshot(
                generation, state, email, plan, pending, windows, updated, error,
                _display(state, windows, updated, error, credit_balance, credits_unlimited),
                credit_balance, credits_unlimited,
            )
        self._events.put(CodexAccountEvent("updated", generation))

    def _run(self) -> None:
        while not self._stop.is_set():
            self._drain_incoming()
            with self._lock:
                poll_due = self._polling and time.monotonic() >= self._next_poll
            if poll_due:
                self.refresh()
                with self._lock:
                    self._next_poll = time.monotonic() + self._backoff
            try:
                operation, generation, argument = self._commands.get(timeout=0.1)
            except queue.Empty:
                continue
            if self._stop.is_set():
                break
            try:
                if operation in ("refresh", "cli"):
                    with self._lock:
                        self._refresh_queued = False
                    if operation == "cli":
                        self._dispose_process()
                    self._work_refresh(generation)
                elif operation == "login":
                    self._work_login(generation)
                elif operation == "cancel":
                    self._work_cancel(argument)
                elif operation == "logout":
                    self._work_logout(generation, argument)
            except (OSError, RuntimeError, ValueError, TimeoutError) as error:
                self._dispose_process()
                if operation == "logout":
                    with self._lock:
                        self._expected_logout_notice = None
                if operation in ("refresh", "cli"):
                    if isinstance(error, FileNotFoundError):
                        self._publish(generation, "setup_required", error="codex_cli_unavailable")
                    elif isinstance(error, _RpcError) and error.authentication:
                        self._publish(generation, "auth_error", error="authentication_required")
                    else:
                        self._failed_refresh(generation, type(error).__name__)
                elif operation == "login":
                    if isinstance(error, FileNotFoundError):
                        self._publish(generation, "setup_required", error="codex_cli_unavailable")
                    else:
                        self._publish(generation, "auth_error", error=type(error).__name__)
                with self._lock:
                    self._backoff = min(300, max(self._refresh_seconds, self._backoff * 2))
                    self._next_poll = time.monotonic() + self._backoff

    def _failed_refresh(self, generation: int, reason: str) -> None:
        with self._lock:
            previous = self._snapshot
        if previous.generation != generation:
            return
        if previous.email and (previous.windows or previous.credit_balance is not None or previous.credits_unlimited):
            self._publish(
                generation, "delayed", email=previous.email, plan=previous.plan_type,
                windows=previous.windows, updated=previous.updated_at, error=reason,
                credit_balance=previous.credit_balance, credits_unlimited=previous.credits_unlimited,
            )
        else:
            self._publish(generation, "unavailable", error=reason)

    def _ensure_process(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._dispose_process()
        if self._home.exists() and self._home.is_symlink():
            raise RuntimeError("unsafe_codex_home")
        self._home.mkdir(parents=True, exist_ok=True)
        environment = _safe_environment(self._home)
        cli = _resolve_cli(self._cli_path, self._home, environment)
        if cli is None:
            raise FileNotFoundError("codex_cli_unavailable")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = subprocess.Popen(
            [str(cli), "app-server", "--stdio", "-c", 'cli_auth_credentials_store="file"'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=self._home,
            env=environment,
            creationflags=creationflags,
        )
        self._reader = threading.Thread(target=self._read_loop, args=(self._process,), name="mini-monitor-codex-rpc", daemon=True)
        self._reader.start()
        self._rpc("initialize", {"clientInfo": {"name": "mini_monitor", "title": "Mini Monitor", "version": "1.0.0"}})
        self._send({"method": "initialized", "params": {}})

    def _read_loop(self, process: Any) -> None:
        try:
            while not self._stop.is_set():
                line = process.stdout.readline(_MAX_LINE + 1)
                if not line or len(line) > _MAX_LINE:
                    break
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(message, dict):
                    self._incoming.put((process, message))
        except (OSError, ValueError):
            pass
        self._incoming.put((process, None))

    def _send(self, message: dict[str, Any]) -> None:
        if self._process is None or self._process.poll() is not None:
            raise RuntimeError("codex_process_exited")
        self._process.stdin.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._process.stdin.flush()

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        process = self._process
        request_id = self._next_id
        self._next_id += 1
        message: dict[str, Any] = {"method": method, "id": request_id}
        if params is not None:
            message["params"] = params
        self._send(message)
        deadline = time.monotonic() + _RPC_TIMEOUT
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("codex_rpc_timeout")
            try:
                source, incoming = self._incoming.get(timeout=min(0.1, remaining))
            except queue.Empty:
                continue
            if source is not process:
                continue
            if incoming is None:
                raise RuntimeError("codex_process_exited")
            if incoming.get("id") == request_id:
                if "error" in incoming:
                    raise _RpcError(authentication=_is_auth_error(incoming["error"]))
                return incoming.get("result")
            if "method" in incoming:
                self._handle_notification(incoming)
        raise RuntimeError("service_closed")

    def _drain_incoming(self) -> None:
        while True:
            try:
                source, incoming = self._incoming.get_nowait()
            except queue.Empty:
                return
            if source is not self._process:
                continue
            if incoming is None:
                self._dispose_process()
                return
            if "method" in incoming:
                self._handle_notification(incoming)

    def _handle_notification(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params")
        params = params if isinstance(params, dict) else {}
        if method == "account/login/completed":
            with self._lock:
                generation = self._snapshot.generation
                valid = self._snapshot.login_pending and params.get("loginId") == self._login_id
                if self._snapshot.login_pending and self._login_id is None and isinstance(params.get("loginId"), str):
                    self._early_login[params["loginId"]] = params.get("success") is True
            if valid:
                self._login_id = None
                if params.get("success") is True:
                    self._publish(generation, "unavailable")
                    self.refresh()
                else:
                    self._publish(generation, "auth_error", error="login_failed")
        elif method == "account/updated":
            auth_mode = params.get("authMode")
            with self._lock:
                old = self._snapshot
                expected = self._expected_logout_notice
                if auth_mode is None and expected is not None:
                    self._expected_logout_notice = None
                    if old.generation >= expected:
                        return
                if auth_mode != "chatgpt" or not old.login_pending:
                    generation = old.generation + 1
                    state = "unavailable" if auth_mode == "chatgpt" else "signed_out"
                    self._snapshot = CodexAccountSnapshot(
                        generation, state, None, None, False, (), None, None,
                        _display(state),
                    )
                    self._login_id = None
                    self._early_login.clear()
                    self._refresh_queued = False
                else:
                    generation = old.generation
            if auth_mode == "chatgpt":
                self.refresh()
            else:
                self._events.put(CodexAccountEvent("updated", generation))
        elif method == "account/rateLimits/updated":
            self.refresh()

    def _work_refresh(self, generation: int) -> None:
        with self._lock:
            if generation != self._snapshot.generation:
                return
        self._ensure_process()
        account_result = self._rpc("account/read", {"refreshToken": False})
        account = account_result.get("account") if isinstance(account_result, dict) else None
        with self._lock:
            if generation != self._snapshot.generation:
                return
            previous = self._snapshot
        if not isinstance(account, dict) or account.get("type") != "chatgpt":
            with self._lock:
                if generation == self._snapshot.generation:
                    self._snapshot = replace(self._snapshot, generation=generation + 1)
            self._publish(generation + 1, "signed_out" if account is None else "auth_error")
            return
        email = account.get("email") if isinstance(account.get("email"), str) else None
        plan = account.get("planType") if isinstance(account.get("planType"), str) else None
        if previous.email and email != previous.email:
            with self._lock:
                if generation == self._snapshot.generation:
                    generation += 1
                    self._snapshot = replace(self._snapshot, generation=generation)
            self._publish(generation, "unavailable", email=email, plan=plan)
        limits_result = self._rpc("account/rateLimits/read")
        with self._lock:
            if generation != self._snapshot.generation:
                return
        windows = _parse_windows(limits_result)
        credit_balance, credits_unlimited = _parse_credits(limits_result)
        if not windows:
            self._publish(generation, "no_data", email=email, plan=plan,
                          updated=datetime.now(timezone.utc), credit_balance=credit_balance,
                          credits_unlimited=credits_unlimited)
        else:
            self._publish(
                generation, "ready", email=email, plan=plan, windows=windows,
                updated=datetime.now(timezone.utc),
                credit_balance=credit_balance, credits_unlimited=credits_unlimited,
            )
            with self._lock:
                self._backoff = self._refresh_seconds
                self._next_poll = time.monotonic() + self._refresh_seconds

    def _work_login(self, generation: int) -> None:
        with self._lock:
            if generation != self._snapshot.generation:
                return
        self._ensure_process()
        result = self._rpc("account/login/start", {"type": "chatgpt"})
        if not isinstance(result, dict) or not isinstance(result.get("loginId"), str):
            raise RuntimeError("invalid_login_response")
        auth_url = result.get("authUrl")
        if not _valid_auth_url(auth_url):
            try:
                self._rpc("account/login/cancel", {"loginId": result["loginId"]})
            except (RuntimeError, TimeoutError):
                pass
            raise ValueError("untrusted_auth_url")
        with self._lock:
            current = generation == self._snapshot.generation
            if current:
                self._login_id = result["loginId"]
            early_completion = self._early_login.pop(result["loginId"], None)
        if not current:
            try:
                self._rpc("account/login/cancel", {"loginId": result["loginId"]})
            except (RuntimeError, TimeoutError):
                pass
            return
        if early_completion is not None:
            self._login_id = None
            if early_completion:
                self._publish(generation, "unavailable")
                self.refresh()
            else:
                self._publish(generation, "auth_error", error="login_failed")
            return
        self._events.put(CodexAccountEvent("auth_url", generation, auth_url))

    def _work_cancel(self, login_id: str | None) -> None:
        if login_id and self._process is not None:
            self._rpc("account/login/cancel", {"loginId": login_id})

    def _work_logout(self, generation: int, login_id: str | None) -> None:
        if login_id and self._process is not None:
            try:
                self._rpc("account/login/cancel", {"loginId": login_id})
            except (RuntimeError, TimeoutError):
                # Killing only our isolated child also tears down its OAuth
                # callback before a fresh process performs account/logout.
                self._dispose_process()
        self._ensure_process()
        self._rpc("account/logout")
        self._dispose_process()
        with self._lock:
            self._expected_logout_notice = None
        self._publish(generation, "signed_out")

    def _dispose_process(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
