# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from ai_mini_monitor.config import AppConfig
from ai_mini_monitor.controller import MonitorStartError
from ai_mini_monitor.desktop_session import DesktopSession, SerialTaskWorker, SessionState


class FakeController:
    instances: list["FakeController"] = []

    def __init__(self, config, *, enable_serial: bool) -> None:
        self.config = config
        self.enable_serial = enable_serial
        self.started = False
        self.stopped = False
        self.start_cancelled = False
        self.reconnects = 0
        self.ai_refreshes = 0
        self.brightness_requests: list[int] = []
        self.events: list[object] = []
        self.store = SimpleNamespace(read=lambda: "runtime")
        self.__class__.instances.append(self)

    def start(self) -> None:
        if self.start_cancelled:
            self.events.append("start-blocked")
            raise RuntimeError("synthetic cancelled start")
        self.events.append("start")
        self.started = True

    def cancel_start(self) -> None:
        self.start_cancelled = True
        self.events.append("cancel-start")

    def stop(self, timeout: float = 20.0) -> bool:
        self.events.append(("stop", timeout))
        self.stopped = True
        return True

    def request_reconnect(self) -> None:
        self.reconnects += 1

    def request_ai_refresh(self) -> None:
        self.ai_refreshes += 1

    def request_brightness(self, brightness: int) -> bool:
        self.brightness_requests.append(brightness)
        return True

    def suspend_for_power_event(self, timeout: float = 2.0) -> bool:
        self.events.append(("suspend", timeout))
        return True

    def resume_from_power_event(self) -> bool:
        self.events.append("resume")
        return True

    def latest_image(self):
        return Image.new("RGB", (480, 320), "black")


def test_every_restart_gets_a_fresh_one_shot_controller() -> None:
    FakeController.instances.clear()
    session = DesktopSession(enable_serial=True, controller_factory=FakeController)

    assert session.start(AppConfig()).ok
    first = FakeController.instances[-1]
    assert session.stop().ok
    assert first.stopped
    assert session.start(AppConfig()).ok
    second = FakeController.instances[-1]

    assert second is not first
    assert len(FakeController.instances) == 2
    assert second.enable_serial is True
    assert session.request_reconnect()
    assert second.reconnects == 1
    assert session.stop().ok


def test_no_serial_is_immutable_for_the_whole_session() -> None:
    FakeController.instances.clear()
    session = DesktopSession(enable_serial=False, controller_factory=FakeController)
    assert session.start(AppConfig()).ok
    assert FakeController.instances[-1].enable_serial is False
    assert session.request_reconnect() is False
    assert session.request_brightness(32) is True
    assert FakeController.instances[-1].brightness_requests == [32]
    assert session.stop().state is SessionState.STOPPED


@pytest.mark.parametrize(
    "brightness",
    [True, False, "25", 25.0, float("nan"), 0, 51],
)
def test_live_brightness_route_rejects_noncanonical_values(
    brightness: object,
) -> None:
    session = DesktopSession(enable_serial=True, controller_factory=FakeController)
    assert session.start(AppConfig()).ok
    controller = FakeController.instances[-1]

    with pytest.raises(ValueError, match="brightness"):
        session.request_brightness(brightness)  # type: ignore[arg-type]

    assert controller.brightness_requests == []
    assert session.stop().ok


def test_constructor_failure_returns_to_stopped_and_allows_retry() -> None:
    calls = 0

    def factory(config, *, enable_serial: bool):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic constructor failure")
        return FakeController(config, enable_serial=enable_serial)

    session = DesktopSession(enable_serial=False, controller_factory=factory)
    first = session.start(AppConfig())
    assert not first.ok and first.state is SessionState.STOPPED
    assert session.state is SessionState.STOPPED
    second = session.start(AppConfig())
    assert second.ok and second.state is SessionState.RUNNING
    assert session.stop().ok


def test_invalid_config_returns_stopped_without_calling_factory() -> None:
    calls = 0

    def factory(config, *, enable_serial: bool):
        nonlocal calls
        calls += 1
        return FakeController(config, enable_serial=enable_serial)

    invalid = AppConfig()
    invalid.device.usb_fps = 0
    session = DesktopSession(enable_serial=False, controller_factory=factory)

    failed = session.start(invalid)

    assert failed.state is SessionState.STOPPED
    assert failed.reason == "start_failed"
    assert session.state is SessionState.STOPPED
    assert calls == 0
    assert session.start(AppConfig()).ok
    assert session.stop().ok


def test_start_failure_returns_stopped_only_after_cleanup_completes() -> None:
    class FailingStart(FakeController):
        def start(self) -> None:
            self.events.append("start-failed")
            raise RuntimeError("synthetic start failure")

    calls = 0

    def factory(config, *, enable_serial: bool):
        nonlocal calls
        calls += 1
        controller_type = FailingStart if calls == 1 else FakeController
        return controller_type(config, enable_serial=enable_serial)

    session = DesktopSession(enable_serial=False, controller_factory=factory)
    failed = session.start(AppConfig())

    assert failed.state is SessionState.STOPPED
    assert session.state is SessionState.STOPPED
    assert session.start(AppConfig()).ok
    assert calls == 2
    assert session.stop().ok


def test_sanitized_monitor_start_reason_survives_cleanup_for_setup_ui() -> None:
    class PortInUseStart(FakeController):
        def start(self) -> None:
            self.events.append("port-in-use")
            raise MonitorStartError("port_in_use")

    session = DesktopSession(enable_serial=True, controller_factory=PortInUseStart)

    failed = session.start(AppConfig())

    assert not failed.ok
    assert failed.state is SessionState.STOPPED
    assert failed.reason == "port_in_use"
    assert PortInUseStart.instances[-1].stopped


@pytest.mark.parametrize("cleanup_mode", ["false", "raise"])
def test_start_failure_retains_controller_when_cleanup_is_incomplete(cleanup_mode: str) -> None:
    factory_calls = 0

    class UnsafeCleanup(FakeController):
        def start(self) -> None:
            raise RuntimeError("synthetic start failure")

        def stop(self, timeout: float = 20.0) -> bool:
            self.events.append(("stop", timeout))
            if cleanup_mode == "raise":
                raise RuntimeError("synthetic cleanup failure")
            return False

    def factory(config, *, enable_serial: bool):
        nonlocal factory_calls
        factory_calls += 1
        return UnsafeCleanup(config, enable_serial=enable_serial)

    session = DesktopSession(enable_serial=False, controller_factory=factory)
    failed = session.start(AppConfig())

    assert not failed.ok
    assert failed.state is SessionState.ERROR
    assert failed.reason == "start_cleanup_incomplete"
    assert session.state is SessionState.ERROR
    retry = session.start(AppConfig())
    assert not retry.ok
    assert retry.state is SessionState.ERROR
    assert retry.reason == "already_active"
    assert factory_calls == 1


def test_incomplete_stop_blocks_reuse_of_the_controller_slot() -> None:
    class BlockingStop(FakeController):
        def stop(self, timeout: float = 20.0) -> bool:
            return False

    session = DesktopSession(enable_serial=True, controller_factory=BlockingStop)
    assert session.start(AppConfig()).ok
    stopped = session.stop(timeout=0.01)
    assert not stopped.ok and stopped.state is SessionState.ERROR
    assert not session.start(AppConfig()).ok


def test_stop_operation_lock_wait_is_bounded_while_start_is_busy() -> None:
    entered = threading.Event()
    release = threading.Event()
    start_results = []

    class SlowStart(FakeController):
        def start(self) -> None:
            entered.set()
            assert release.wait(2.0)
            super().start()

    session = DesktopSession(enable_serial=False, controller_factory=SlowStart)
    thread = threading.Thread(target=lambda: start_results.append(session.start(AppConfig())))
    thread.start()
    assert entered.wait(1.0)

    began = time.monotonic()
    stopped = session.stop(timeout=0.05)
    elapsed = time.monotonic() - began

    assert not stopped.ok
    assert stopped.state is SessionState.STARTING
    assert stopped.reason == "operation_busy"
    assert elapsed < 0.25
    release.set()
    thread.join(1.0)
    assert not thread.is_alive()
    assert start_results[0].state is SessionState.RUNNING
    assert session.stop().ok


def test_begin_shutdown_rejects_new_runtime_side_effects_and_restart() -> None:
    session = DesktopSession(enable_serial=True, controller_factory=FakeController)
    assert session.start(AppConfig()).ok
    controller = FakeController.instances[-1]

    assert session.begin_shutdown() is True
    assert session.begin_shutdown() is False
    assert controller.start_cancelled
    assert session.request_reconnect() is False
    assert session.request_ai_refresh() is False
    assert session.request_brightness(25) is False
    assert controller.reconnects == 0
    assert controller.ai_refreshes == 0
    assert controller.brightness_requests == []
    assert session.stop().ok

    rejected = session.start(AppConfig())
    assert rejected.state is SessionState.STOPPED
    assert rejected.reason == "shutting_down"


def test_shutdown_during_start_cleans_up_instead_of_publishing_running() -> None:
    entered = threading.Event()
    release = threading.Event()
    results = []

    class SlowStart(FakeController):
        def start(self) -> None:
            entered.set()
            assert release.wait(2.0)
            super().start()

    session = DesktopSession(enable_serial=False, controller_factory=SlowStart)
    thread = threading.Thread(target=lambda: results.append(session.start(AppConfig())))
    thread.start()
    assert entered.wait(1.0)
    controller = SlowStart.instances[-1]
    assert session.begin_shutdown()
    release.set()
    thread.join(1.0)

    assert not thread.is_alive()
    assert results[0].state is SessionState.STOPPED
    assert results[0].reason == "shutting_down"
    assert not controller.started
    assert "cancel-start" in controller.events
    assert "start-blocked" in controller.events
    assert controller.stopped
    assert session.state is SessionState.STOPPED


def test_shutdown_before_preparation_skips_persistent_side_effects() -> None:
    factory_entered = threading.Event()
    factory_release = threading.Event()
    prepared = threading.Event()
    results = []

    def factory(config, *, enable_serial: bool):
        factory_entered.set()
        assert factory_release.wait(2.0)
        return FakeController(config, enable_serial=enable_serial)

    session = DesktopSession(enable_serial=False, controller_factory=factory)
    thread = threading.Thread(
        target=lambda: results.append(
            session.start(AppConfig(), prepare=prepared.set)
        )
    )
    thread.start()
    assert factory_entered.wait(1.0)
    assert session.begin_shutdown()
    factory_release.set()
    thread.join(1.0)

    assert not thread.is_alive()
    assert not prepared.is_set()
    assert results[0].reason == "shutting_down"


def test_shutdown_latches_promptly_while_admitted_preparation_is_slow() -> None:
    prepare_entered = threading.Event()
    prepare_release = threading.Event()
    results = []

    def slow_prepare() -> None:
        prepare_entered.set()
        assert prepare_release.wait(2.0)

    session = DesktopSession(enable_serial=False, controller_factory=FakeController)
    start_thread = threading.Thread(
        target=lambda: results.append(session.start(AppConfig(), prepare=slow_prepare))
    )
    start_thread.start()
    assert prepare_entered.wait(1.0)

    began = time.monotonic()
    assert session.begin_shutdown()
    elapsed = time.monotonic() - began

    assert elapsed < 0.25
    assert session.shutting_down
    prepare_release.set()
    start_thread.join(1.0)
    assert not start_thread.is_alive()
    assert results[0].reason == "shutting_down"


def test_suspend_during_factory_is_latched_and_applied_before_controller_start() -> None:
    factory_entered = threading.Event()
    factory_release = threading.Event()
    results = []
    controller_holder: list[FakeController] = []

    def factory(config, *, enable_serial: bool):
        controller = FakeController(config, enable_serial=enable_serial)
        controller_holder.append(controller)
        factory_entered.set()
        assert factory_release.wait(2.0)
        return controller

    session = DesktopSession(enable_serial=True, controller_factory=factory)
    thread = threading.Thread(target=lambda: results.append(session.start(AppConfig())))
    thread.start()
    assert factory_entered.wait(1.0)
    assert session.state is SessionState.STARTING
    assert session.suspend_active(timeout=0.25)
    factory_release.set()
    thread.join(1.0)

    assert not thread.is_alive()
    controller = controller_holder[0]
    assert controller.events[:2] == [("suspend", 0.25), "start"]
    assert results[0].state is SessionState.RUNNING
    assert session.resume_active() is True
    assert session.resume_active() is False
    assert controller.events[-1] == "resume"
    assert session.stop().ok


def test_resume_before_factory_returns_clears_pending_suspend_latch() -> None:
    factory_entered = threading.Event()
    factory_release = threading.Event()
    controller_holder: list[FakeController] = []

    def factory(config, *, enable_serial: bool):
        controller = FakeController(config, enable_serial=enable_serial)
        controller_holder.append(controller)
        factory_entered.set()
        assert factory_release.wait(2.0)
        return controller

    session = DesktopSession(enable_serial=True, controller_factory=factory)
    thread = threading.Thread(target=lambda: session.start(AppConfig()))
    thread.start()
    assert factory_entered.wait(1.0)
    assert session.suspend_active(timeout=0.25)
    assert session.resume_active() is True
    assert session.resume_active() is False
    factory_release.set()
    thread.join(1.0)

    assert not thread.is_alive()
    assert controller_holder[0].events == ["start"]
    assert session.state is SessionState.RUNNING
    assert session.stop().ok


def test_worker_serializes_tasks_and_redacts_exception_messages() -> None:
    worker = SerialTaskWorker()
    order: list[str] = []
    entered = threading.Event()
    release = threading.Event()

    def first() -> int:
        order.append("first-enter")
        entered.set()
        release.wait(1.0)
        order.append("first-exit")
        return 1

    def second() -> int:
        order.append("second")
        return 2

    assert worker.submit("first", first)
    assert worker.submit("second", second)
    assert entered.wait(1.0)
    time.sleep(0.02)
    assert order == ["first-enter"]
    release.set()
    deadline = time.monotonic() + 1.0
    results = []
    while len(results) < 2 and time.monotonic() < deadline:
        results.extend(worker.poll())
        time.sleep(0.01)
    assert order == ["first-enter", "first-exit", "second"]
    assert [result.value for result in results] == [1, 2]

    assert worker.submit("secret", lambda: (_ for _ in ()).throw(RuntimeError("C:/private/secret")))
    deadline = time.monotonic() + 1.0
    failure = []
    while not failure and time.monotonic() < deadline:
        failure = worker.poll()
        time.sleep(0.01)
    assert failure[0].error_kind == "RuntimeError"
    assert "private" not in repr(failure[0])
    assert worker.close()
    assert not worker.submit("late", lambda: None)


def test_worker_begin_shutdown_discards_queued_work_and_late_results() -> None:
    worker = SerialTaskWorker()
    entered = threading.Event()
    release = threading.Event()
    queued_side_effect = threading.Event()

    def active() -> str:
        entered.set()
        assert release.wait(2.0)
        return "completed after shutdown"

    assert worker.submit("active", active)
    assert worker.submit("queued", queued_side_effect.set)
    assert entered.wait(1.0)
    assert worker.begin_shutdown() is True
    assert worker.begin_shutdown() is False
    assert worker.submit("late", queued_side_effect.set) is False
    release.set()

    assert worker.close(timeout=1.0)
    assert not queued_side_effect.is_set()
    assert worker.poll() == []


def test_worker_close_is_bounded_while_active_and_still_drains_pending_work() -> None:
    worker = SerialTaskWorker()
    entered = threading.Event()
    release = threading.Event()
    queued_side_effect = threading.Event()

    def active() -> None:
        entered.set()
        assert release.wait(2.0)

    assert worker.submit("active", active)
    assert worker.submit("queued", queued_side_effect.set)
    assert entered.wait(1.0)

    began = time.monotonic()
    assert worker.close(timeout=0.05) is False
    assert time.monotonic() - began < 0.25
    release.set()
    assert worker.close(timeout=1.0)
    assert not queued_side_effect.is_set()
