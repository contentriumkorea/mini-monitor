# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared desktop collection, optional USB transmission, and serialized work."""

from __future__ import annotations

import copy
import math
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from PIL import Image

from .ai.codex_account import CodexAccountSnapshot
from .config import AppConfig, validate_brightness
from .controller import MonitorController, MonitorStartError
from .state import RuntimeValues


class SessionState(str, Enum):
    STOPPED = "stopped"
    DESKTOP = "desktop"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SessionResult:
    ok: bool
    state: SessionState
    reason: str | None = None


ControllerFactory = Callable[..., MonitorController]


class DesktopSession:
    """Keep one desktop collector alive while USB Start/Stop only owns COM."""

    def __init__(
        self,
        *,
        enable_serial: bool,
        controller_factory: ControllerFactory = MonitorController,
        codex_account_snapshot: Callable[[], CodexAccountSnapshot] | None = None,
    ) -> None:
        self.enable_serial = bool(enable_serial)
        self._controller_factory = controller_factory
        self._codex_account_snapshot = codex_account_snapshot
        self._lock = threading.RLock()
        self._operation_lock = threading.Lock()
        self._shutdown_gate_lock = threading.Lock()
        self._power_lock = threading.Lock()
        self._controller: MonitorController | None = None
        self._desktop_mode = False
        self._state = SessionState.STOPPED
        self._shutdown_event = threading.Event()
        self._power_suspended = False
        self._power_suspend_timeout = 2.0

    @property
    def state(self) -> SessionState:
        with self._lock:
            return self._state

    @property
    def running(self) -> bool:
        return self.state is SessionState.RUNNING

    @property
    def shutting_down(self) -> bool:
        return self._shutdown_event.is_set()

    def begin_shutdown(self) -> bool:
        """Atomically reject future Start and runtime side-effect requests."""

        # This gate is deliberately independent of the state lock and any
        # persistent preparation. Exit can therefore latch immediately even
        # while a slow DPAPI/config operation that was already admitted is
        # completing on the worker thread.
        with self._shutdown_gate_lock:
            first = not self._shutdown_event.is_set()
            self._shutdown_event.set()
        with self._lock:
            controller = self._controller
        if controller is not None:
            cancel_start = getattr(controller, "cancel_start", None)
            if callable(cancel_start):
                try:
                    cancel_start()
                except Exception:
                    pass
        return first

    def start(
        self,
        config: AppConfig,
        *,
        prepare: Callable[[], None] | None = None,
    ) -> SessionResult:
        with self._operation_lock:
            with self._lock:
                if self._shutdown_event.is_set():
                    return SessionResult(False, self._state, "shutting_down")
                desktop_controller = (
                    self._controller if self._desktop_mode and self._state is SessionState.DESKTOP
                    else None
                )
                if desktop_controller is None and (self._controller is not None or self._state is not SessionState.STOPPED):
                    return SessionResult(False, self._state, "already_active")
                if desktop_controller is None:
                    self._state = SessionState.STARTING
            if desktop_controller is not None:
                return self._attach_monitor(desktop_controller, config, prepare)
            try:
                draft = copy.deepcopy(config)
                draft.validate()
                account_kwargs = (
                    {"codex_account_snapshot": self._codex_account_snapshot}
                    if self._codex_account_snapshot is not None else {}
                )
                controller = self._controller_factory(
                    draft,
                    enable_serial=self.enable_serial,
                    **account_kwargs,
                )
            except Exception:
                with self._lock:
                    self._controller = None
                    self._state = SessionState.STOPPED
                return SessionResult(False, SessionState.STOPPED, "start_failed")

            with self._lock:
                self._controller = controller
                shutdown_before_start = self._shutdown_event.is_set()
            if shutdown_before_start:
                return self._finish_failed_start(controller, "shutting_down")

            if prepare is not None:
                shutdown_before_prepare = False
                try:
                    # Admission is serialized only for this short check. The
                    # potentially slow persistent I/O runs outside all session
                    # state locks so the GUI can latch Exit immediately.
                    with self._shutdown_gate_lock:
                        if self._shutdown_event.is_set():
                            shutdown_before_prepare = True
                    if not shutdown_before_prepare:
                        prepare()
                except Exception:
                    return self._finish_failed_start(controller, "preparation_failed")
                if shutdown_before_prepare:
                    return self._finish_failed_start(controller, "shutting_down")

            try:
                # A suspend broadcast can arrive while validation or the
                # factory is still running. Apply that latched state before
                # controller.start() gets any opportunity to open COM.
                with self._power_lock:
                    if self._power_suspended and not controller.suspend_for_power_event(
                        timeout=self._power_suspend_timeout
                    ):
                        raise RuntimeError("pre-start suspend was not acknowledged")
                with self._lock:
                    shutdown_during_start = self._shutdown_event.is_set()
                if shutdown_during_start:
                    return self._finish_failed_start(controller, "shutting_down")
                controller.start()
            except Exception as error:
                if self.shutting_down:
                    reason = "shutting_down"
                elif isinstance(error, MonitorStartError):
                    reason = error.reason
                else:
                    reason = "start_failed"
                return self._finish_failed_start(controller, reason)

            with self._lock:
                shutdown_after_start = self._shutdown_event.is_set()
                if not shutdown_after_start:
                    self._state = SessionState.RUNNING
            if shutdown_after_start:
                return self._finish_failed_start(controller, "shutting_down")
            return SessionResult(True, SessionState.RUNNING)

    def start_desktop(self, config: AppConfig) -> SessionResult:
        """Start one non-serial collector/renderer for desktop consumers."""
        with self._operation_lock:
            with self._lock:
                if self._shutdown_event.is_set():
                    return SessionResult(False, self._state, "shutting_down")
                if self._controller is not None or self._state is not SessionState.STOPPED:
                    return SessionResult(False, self._state, "already_active")
                self._state = SessionState.STARTING
            try:
                draft = copy.deepcopy(config)
                draft.validate()
                account_kwargs = (
                    {"codex_account_snapshot": self._codex_account_snapshot}
                    if self._codex_account_snapshot is not None else {}
                )
                controller = self._controller_factory(draft, enable_serial=False, **account_kwargs)
                with self._lock:
                    self._controller = controller
                if self._shutdown_event.is_set():
                    return self._finish_failed_start(controller, "shutting_down")
                with self._power_lock:
                    if self._power_suspended and not controller.suspend_for_power_event(
                        timeout=self._power_suspend_timeout
                    ):
                        raise RuntimeError("pre-start suspend was not acknowledged")
                controller.start()
            except Exception:
                if self._controller is not None:
                    return self._finish_failed_start(self._controller, "start_failed")
                with self._lock:
                    self._state = SessionState.STOPPED
                return SessionResult(False, SessionState.STOPPED, "start_failed")
            with self._lock:
                shutdown_after_start = self._shutdown_event.is_set()
                if not shutdown_after_start:
                    self._desktop_mode = True
                    self._state = SessionState.DESKTOP
            if shutdown_after_start:
                return self._finish_failed_start(controller, "shutting_down")
            return SessionResult(True, SessionState.DESKTOP)

    def _attach_monitor(
        self, controller: MonitorController, config: AppConfig,
        prepare: Callable[[], None] | None,
    ) -> SessionResult:
        self._state = SessionState.STARTING
        try:
            draft = copy.deepcopy(config)
            draft.validate()
            if prepare is not None:
                with self._shutdown_gate_lock:
                    if self._shutdown_event.is_set():
                        raise RuntimeError("shutting_down")
                prepare()
            if self._shutdown_event.is_set():
                raise RuntimeError("shutting_down")
            if self.enable_serial:
                controller.attach_serial(draft)
            elif draft.device.rotation != controller.config.device.rotation:
                controller.reconfigure_display(draft.device.rotation)
        except Exception as error:
            reason = (
                "shutting_down" if self._shutdown_event.is_set() else
                error.reason if isinstance(error, MonitorStartError) else
                "start_failed"
            )
            state = SessionState.ERROR if str(error) == "serial_cleanup_incomplete" else SessionState.DESKTOP
            with self._lock:
                self._state = state
            return SessionResult(False, state, "start_cleanup_incomplete" if state is SessionState.ERROR else reason)
        with self._lock:
            shutdown_after_attach = self._shutdown_event.is_set()
            if not shutdown_after_attach:
                self._state = SessionState.RUNNING
        if shutdown_after_attach:
            if self.enable_serial:
                controller.detach_serial(timeout=5.0)
            with self._lock:
                self._state = SessionState.DESKTOP
            return SessionResult(False, SessionState.DESKTOP, "shutting_down")
        return SessionResult(True, SessionState.RUNNING)

    def reconfigure_desktop(self, rotation: str) -> bool:
        """Apply idle desktop orientation off Tk without opening USB."""
        with self._operation_lock:
            with self._lock:
                controller = self._controller if self._desktop_mode and self._state is SessionState.DESKTOP else None
            if controller is None or self.shutting_down:
                return False
            controller.reconfigure_display(rotation)
            return True

    def stop_monitor(self, timeout: float = 20.0) -> SessionResult:
        """Stop USB transmission but preserve desktop collection when enabled."""
        with self._lock:
            desktop_mode = self._desktop_mode
        if not desktop_mode:
            return self.stop(timeout=timeout)
        if not self._operation_lock.acquire(timeout=timeout):
            with self._lock:
                return SessionResult(False, self._state, "operation_busy")
        try:
            with self._lock:
                controller = self._controller
                if controller is None or self._state is SessionState.DESKTOP:
                    return SessionResult(True, SessionState.DESKTOP)
                if self._state is not SessionState.RUNNING:
                    return SessionResult(False, self._state, "already_active")
                self._state = SessionState.STOPPING
            try:
                complete = True if not self.enable_serial else controller.detach_serial(timeout=timeout)
            except Exception:
                complete = False
            with self._lock:
                self._state = SessionState.DESKTOP if complete else SessionState.ERROR
                return SessionResult(complete, self._state, None if complete else "stop_incomplete")
        finally:
            self._operation_lock.release()

    def _finish_failed_start(self, controller: MonitorController, reason: str) -> SessionResult:
        cleanup_complete = False
        try:
            cleanup_complete = bool(controller.stop(timeout=5.0))
        except Exception:
            cleanup_complete = False
        with self._lock:
            if cleanup_complete:
                if self._controller is controller:
                    self._controller = None
                self._state = SessionState.STOPPED
                return SessionResult(False, SessionState.STOPPED, reason)
            # Never drop the last reference to a controller whose cleanup is
            # incomplete. ERROR deliberately blocks reuse until stop retries.
            self._controller = controller
            self._state = SessionState.ERROR
            return SessionResult(False, SessionState.ERROR, "start_cleanup_incomplete")

    def stop(self, timeout: float = 20.0) -> SessionResult:
        if isinstance(timeout, bool):
            raise ValueError("stop timeout must be a positive finite number")
        try:
            timeout_value = float(timeout)
        except (TypeError, ValueError) as error:
            raise ValueError("stop timeout must be a positive finite number") from error
        if not math.isfinite(timeout_value) or timeout_value <= 0:
            raise ValueError("stop timeout must be a positive finite number")

        deadline = time.monotonic() + timeout_value
        if not self._operation_lock.acquire(timeout=timeout_value):
            with self._lock:
                return SessionResult(False, self._state, "operation_busy")
        try:
            with self._lock:
                controller = self._controller
                if controller is None:
                    self._state = SessionState.STOPPED
                    self._desktop_mode = False
                    return SessionResult(True, SessionState.STOPPED)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with self._lock:
                    return SessionResult(False, self._state, "operation_timeout")
            with self._lock:
                self._state = SessionState.STOPPING
            try:
                complete = controller.stop(timeout=remaining)
            except Exception:
                complete = False
            with self._lock:
                if complete:
                    self._controller = None
                    self._desktop_mode = False
                    self._state = SessionState.STOPPED
                    return SessionResult(True, SessionState.STOPPED)
                self._state = SessionState.ERROR
                return SessionResult(False, SessionState.ERROR, "stop_incomplete")
        finally:
            self._operation_lock.release()

    def request_reconnect(self) -> bool:
        if self.shutting_down or not self.running:
            return False
        controller = self._active_controller()
        if controller is None or not self.enable_serial:
            return False
        controller.request_reconnect()
        return True

    def request_brightness(self, brightness: int) -> bool:
        """Route a validated live setting to the existing controller only."""

        value = validate_brightness(brightness)
        if self.shutting_down or not self.running:
            return False
        controller = self._active_controller()
        if controller is None:
            return False
        return bool(controller.request_brightness(value))

    def request_ai_refresh(self) -> bool:
        if self.shutting_down:
            return False
        controller = self._active_controller()
        if controller is None:
            return False
        controller.request_ai_refresh()
        return True

    def suspend_active(self, timeout: float = 2.0) -> bool:
        if isinstance(timeout, bool):
            raise ValueError("power suspend timeout must be a positive finite number")
        try:
            timeout_value = float(timeout)
        except (TypeError, ValueError) as error:
            raise ValueError("power suspend timeout must be a positive finite number") from error
        if not math.isfinite(timeout_value) or timeout_value <= 0:
            raise ValueError("power suspend timeout must be a positive finite number")
        with self._power_lock:
            self._power_suspended = True
            self._power_suspend_timeout = timeout_value
            controller = self._active_controller(include_starting=True)
            return True if controller is None else controller.suspend_for_power_event(timeout=timeout_value)

    def resume_active(self) -> bool:
        with self._power_lock:
            if not self._power_suspended:
                return False
            self._power_suspended = False
            if self.shutting_down:
                return False
            controller = self._active_controller(include_starting=True)
            # Clearing a latch before a controller exists is still a real
            # resume transition and prevents a later Start from pausing COM.
            return True if controller is None else controller.resume_from_power_event()

    def latest_image(self) -> Image.Image | None:
        with self._lock:
            controller = self._controller if self._desktop_mode else None
        controller = controller or self._active_controller(include_starting=True)
        return None if controller is None else controller.latest_image()

    def runtime_values(self) -> RuntimeValues | None:
        with self._lock:
            controller = self._controller if self._desktop_mode else None
        controller = controller or self._active_controller(include_starting=True)
        return None if controller is None else controller.store.read()

    def _active_controller(self, *, include_starting: bool = False) -> MonitorController | None:
        with self._lock:
            allowed = {SessionState.RUNNING, SessionState.DESKTOP}
            if include_starting:
                allowed.add(SessionState.STARTING)
            return self._controller if self._state in allowed else None


@dataclass(frozen=True, slots=True)
class WorkResult:
    kind: str
    value: Any = None
    error_kind: str | None = None


class SerialTaskWorker:
    """Run desktop lifecycle and local scans one-at-a-time off the Tk thread."""

    def __init__(self) -> None:
        self._tasks: queue.Queue[tuple[str, Callable[[], Any]] | None] = queue.Queue()
        self._results: queue.SimpleQueue[WorkResult] = queue.SimpleQueue()
        self._closed = threading.Event()
        self._gate_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run,
            name="ai-mini-monitor-desktop-worker",
            daemon=True,
        )
        self._thread.start()

    def submit(self, kind: str, action: Callable[[], Any]) -> bool:
        if not isinstance(kind, str) or not kind:
            raise ValueError("work kind must be a non-empty string")
        with self._gate_lock:
            if self._closed.is_set():
                return False
            self._tasks.put_nowait((kind, action))
            return True

    def poll(self) -> list[WorkResult]:
        with self._gate_lock:
            if self._closed.is_set():
                self._drain_results()
                return []
            output: list[WorkResult] = []
            while True:
                try:
                    output.append(self._results.get_nowait())
                except queue.Empty:
                    return output

    def begin_shutdown(self) -> bool:
        """Reject new work and discard every task that has not begun."""

        with self._gate_lock:
            if self._closed.is_set():
                return False
            self._closed.set()
            while True:
                try:
                    self._tasks.get_nowait()
                except queue.Empty:
                    break
            self._drain_results()
            self._tasks.put_nowait(None)
            return True

    def close(self, timeout: float = 5.0) -> bool:
        self.begin_shutdown()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout)
        return not self._thread.is_alive()

    def _drain_results(self) -> None:
        while True:
            try:
                self._results.get_nowait()
            except queue.Empty:
                return

    def _run(self) -> None:
        while True:
            item = self._tasks.get()
            if item is None:
                return
            kind, action = item
            with self._gate_lock:
                if self._closed.is_set():
                    continue
            try:
                result = WorkResult(kind, value=action())
            except Exception as error:
                # Preserve only the exception class. Arbitrary messages can
                # contain paths, serial internals, or secret-adjacent input.
                result = WorkResult(kind, error_kind=type(error).__name__)
            with self._gate_lock:
                if not self._closed.is_set():
                    self._results.put(result)
