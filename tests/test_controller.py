from __future__ import annotations

import time
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from ai_mini_monitor.config import AppConfig
from ai_mini_monitor.ai.codex_account import CodexAccountSnapshot
from ai_mini_monitor.controller import MonitorController, MonitorStartError


def test_account_provider_reads_injected_snapshot_without_local_scan_or_rpc() -> None:
    config = AppConfig()
    config.ai.provider = AIProviderKind.CODEX_ACCOUNT.value
    expected = CodexAccountSnapshot(1, "signed_out", None, None, False, (), None, None,
                                    AIData(AIProviderKind.CODEX_ACCOUNT, "CODEX", SyncStatus.AUTH_ERROR, "LOGIN", "CHATGPT"))
    calls = []
    controller = MonitorController(
        config,
        enable_serial=False,
        codex_account_snapshot=lambda: (calls.append("snapshot") or expected),
    )
    original_update = controller.store.update_ai

    def capture(value):
        original_update(value)
        controller._stop.set()

    controller.store.update_ai = capture
    controller._ai_loop()
    assert calls == ["snapshot"]
    assert controller.store.read().ai == expected.ai


def test_account_without_snapshot_requires_login_and_never_uses_legacy_provider() -> None:
    config = AppConfig()
    controller = MonitorController(
        config,
        enable_serial=False,
    )
    original_update = controller.store.update_ai

    def capture(value):
        original_update(value)
        controller._stop.set()

    controller.store.update_ai = capture
    controller._ai_loop()

    ai = controller.store.read().ai
    assert ai.provider is AIProviderKind.CODEX_ACCOUNT
    assert ai.status is SyncStatus.SETUP_REQUIRED
from ai_mini_monitor.models import (
    AIData,
    AIProviderKind,
    DisplaySnapshot,
    Metric,
    SensorSnapshot,
    SyncStatus,
)
from ai_mini_monitor.rendering.layout import CPU, MEMORY
from ai_mini_monitor.transport.serial_writer import (
    InitialRestoreResult,
    InitialRestoreStatus,
    SerialWriterEvent,
    SerialWriterState,
)
from ai_mini_monitor.transport.protocol_rev_a import Orientation


class FakeCollector:
    def __init__(self) -> None:
        self.count = 0
        self.closed = False

    def open(self) -> None:
        pass

    def sample(self) -> SensorSnapshot:
        self.count += 1
        cpu = float((self.count * 17) % 101)
        gpu = float((self.count * 11) % 101)
        memory = 40.0 + self.count % 10
        return SensorSnapshot(
            captured_at=datetime.now().astimezone(),
            cpu_percent=Metric(cpu, "%"),
            cpu_temperature=Metric(61.0, "°C"),
            gpu_percent=Metric(gpu, "%"),
            gpu_temperature=Metric(65.0, "°C"),
            memory_percent=Metric(memory, "%"),
            memory_used_gib=Metric(16.0, "GiB"),
            memory_total_gib=Metric(32.0, "GiB"),
            memory_available_gib=Metric(16.0, "GiB"),
        )

    def close(self) -> None:
        self.closed = True


class ImmediateSerial:
    def __init__(
        self,
        restore_result: InitialRestoreResult | None = None,
    ) -> None:
        self.updates = []
        self.pending_count = 0
        self.last_sent_sequence = -1
        self.reconnects = 0
        self.brightness_requests: list[int] = []
        self.stopped = False
        self.suspend_timeouts: list[float] = []
        self.resumes = 0
        self.start_calls = 0
        self.start_cancelled = False
        self.restore_result = restore_result or InitialRestoreResult(
            InitialRestoreStatus.READY,
            "COM_FAKE",
        )
        self.restore_waits: list[tuple[int, float]] = []

    def submit(self, update) -> None:
        self.updates.append(update)
        self.last_sent_sequence = update.sequence

    def start(self) -> None:
        self.start_calls += 1

    def wait_for_initial_restore(self, sequence: int, timeout: float):
        self.restore_waits.append((sequence, timeout))
        return self.restore_result

    def signal_stop(self) -> None:
        self.start_cancelled = True

    def cancel_pending_start(self) -> None:
        self.start_cancelled = True

    def request_reconnect(self) -> None:
        self.reconnects += 1

    def request_brightness(self, brightness: int) -> bool:
        self.brightness_requests.append(brightness)
        return False

    def suspend(self, timeout: float = 2.0) -> bool:
        self.suspend_timeouts.append(timeout)
        return True

    def resume(self) -> bool:
        self.resumes += 1
        return True

    def stop(self, timeout: float = 1.0) -> bool:
        self.stopped = True
        return True


def test_controller_renders_actual_snapshot_without_serial() -> None:
    config = AppConfig()
    config.app.render_fps = 10.0
    config.sensors.sample_interval_ms = 250
    fake = FakeCollector()
    controller = MonitorController(
        config,
        enable_serial=False,
        sensor_collector_factory=lambda: fake,
    )
    controller.start()
    time.sleep(0.55)
    controller.stop()
    image = controller.latest_image()
    stats = controller.stats_snapshot()
    assert image is not None and image.size == (480, 320)
    assert stats["render_frames"] >= 4
    assert stats["unhandled_worker_errors"] == 0
    assert fake.closed


@pytest.mark.parametrize(
    ("rotation", "dimensions", "protocol"),
    [
        ("landscape", (480, 320), Orientation.LANDSCAPE),
        (
            "landscape_inverted",
            (480, 320),
            Orientation.REVERSE_LANDSCAPE,
        ),
        ("portrait", (320, 480), Orientation.PORTRAIT),
        (
            "portrait_inverted",
            (320, 480),
            Orientation.REVERSE_PORTRAIT,
        ),
    ],
)
def test_controller_uses_selected_logical_layout_and_protocol_orientation(
    rotation: str,
    dimensions: tuple[int, int],
    protocol: Orientation,
) -> None:
    config = AppConfig()
    config.device.rotation = rotation
    config.device.brightness = 41
    controller = MonitorController(config, enable_serial=True)

    image = controller.renderer.render(DisplaySnapshot())
    update = controller._frame_update(image, full_refresh=True)

    assert image.size == dimensions
    assert (update.width, update.height) == dimensions
    assert (update.regions[0].width, update.regions[0].height) == dimensions
    assert controller.serial is not None
    assert controller.serial._orientation is protocol
    assert controller.serial.brightness_percent == 41


def test_controller_routes_live_brightness_through_existing_writer_only() -> None:
    serial = ImmediateSerial()
    controller = MonitorController(
        AppConfig(),
        enable_serial=True,
        serial_writer=serial,
    )

    # The fake reports duplicate/unchanged as False, but the controller-level
    # desired-setting request was still accepted and must not create a writer.
    assert controller.request_brightness(38) is True
    assert controller.serial is serial
    assert serial.brightness_requests == [38]
    assert controller.config.device.brightness == 38

    for invalid in (True, False, "25", 25.0, float("nan"), 0, 51):
        with pytest.raises(ValueError, match="brightness"):
            controller.request_brightness(invalid)  # type: ignore[arg-type]
    assert serial.brightness_requests == [38]


@pytest.mark.parametrize(
    ("normal", "inverted"),
    [
        ("landscape", "landscape_inverted"),
        ("portrait", "portrait_inverted"),
    ],
)
def test_reverse_modes_keep_the_same_host_raster(
    normal: str,
    inverted: str,
) -> None:
    normal_config = AppConfig()
    normal_config.device.rotation = normal
    inverted_config = AppConfig()
    inverted_config.device.rotation = inverted
    normal_controller = MonitorController(normal_config, enable_serial=False)
    inverted_controller = MonitorController(inverted_config, enable_serial=False)
    snapshot = DisplaySnapshot()

    assert normal_controller.renderer.render(snapshot).tobytes() == (
        inverted_controller.renderer.render(snapshot).tobytes()
    )


def test_controller_cancel_before_start_never_admits_serial_writer() -> None:
    serial = ImmediateSerial()
    controller = MonitorController(
        AppConfig(),
        enable_serial=True,
        serial_writer=serial,
        sensor_collector_factory=FakeCollector,
    )

    controller.cancel_start()

    with pytest.raises(RuntimeError, match="cancelled"):
        controller.start()
    assert serial.start_cancelled
    assert serial.start_calls == 0
    assert serial.updates == []


def test_controller_submits_one_full_then_partial_updates() -> None:
    config = AppConfig()
    config.app.render_fps = 10.0
    config.device.usb_fps = 5.0
    config.sensors.sample_interval_ms = 250
    serial = ImmediateSerial()
    controller = MonitorController(
        config,
        enable_serial=True,
        serial_writer=serial,
        sensor_collector_factory=FakeCollector,
    )
    controller.start()
    assert serial.restore_waits == [(serial.updates[0].sequence, 15.0)]
    time.sleep(0.8)
    controller.request_reconnect()
    controller.stop()
    assert serial.updates[0].full_refresh
    assert sum(update.full_refresh for update in serial.updates) == 1
    assert any(not update.full_refresh for update in serial.updates[1:])
    assert all(len(update.regions) >= 1 for update in serial.updates)
    assert serial.reconnects == 1
    assert serial.stopped
    stats = controller.stats_snapshot()
    assert stats["max_pending"] <= 1
    assert stats["sensor_to_serial_p95_ms"] is not None


def test_controller_rejects_port_in_use_before_publishing_started() -> None:
    serial = ImmediateSerial(
        InitialRestoreResult(InitialRestoreStatus.PORT_IN_USE, "COM3")
    )
    controller = MonitorController(
        AppConfig(),
        enable_serial=True,
        serial_writer=serial,
        sensor_collector_factory=FakeCollector,
        initial_restore_timeout=0.25,
    )

    with pytest.raises(MonitorStartError) as captured:
        controller.start()

    assert captured.value.reason == "port_in_use"
    assert controller.started is False
    assert serial.start_calls == 1
    assert serial.restore_waits == [(serial.updates[0].sequence, 0.25)]
    assert serial.start_cancelled
    assert controller.stop()


def test_access_denied_runtime_event_is_classified_without_raw_ui_text() -> None:
    controller = MonitorController(AppConfig(), enable_serial=False)
    controller._on_serial_event(
        SerialWriterEvent(
            SerialWriterState.RECONNECTING,
            "COM3",
            "could not open port 'COM3': PermissionError(13, '액세스가 거부되었습니다.')",
        )
    )

    connection = controller.store.read().connection
    assert connection.status.value == "DISCONNECTED"
    assert connection.port == "COM3"
    assert connection.detail == "PORT IN USE"


def test_controller_advances_the_latest_desired_frame_after_submission() -> None:
    controller = MonitorController(AppConfig(), enable_serial=False)
    baseline = Image.new("RGB", (480, 320), "black")
    memory_changed = baseline.copy()
    ImageDraw.Draw(memory_changed).rectangle(
        (MEMORY.x + 10, MEMORY.y + 10, MEMORY.x + 20, MEMORY.y + 20),
        fill="white",
    )
    cpu_and_memory_changed = memory_changed.copy()
    ImageDraw.Draw(cpu_and_memory_changed).rectangle(
        (CPU.x + 10, CPU.y + 10, CPU.x + 20, CPU.y + 20),
        fill="white",
    )

    with controller._completion_lock:
        controller._desired_frame = baseline
    first_dirty = controller._dirty_from_desired(memory_changed)
    first = controller._regions_update(memory_changed, first_dirty)
    controller._register_submission(first, sensor_age_ms=5.0)
    with controller._completion_lock:
        controller._desired_frame = memory_changed

    # The writer preserves a full target framebuffer for latest-wins, so the
    # controller only needs to submit changes since its latest desired state.
    newest = controller._dirty_from_desired(cpu_and_memory_changed)
    assert not any(rect.intersects(MEMORY) for rect in newest)
    assert any(rect.intersects(CPU) for rect in newest)


def test_incomplete_worker_shutdown_is_reported_and_can_be_retried() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingCollector(FakeCollector):
        def sample(self) -> SensorSnapshot:
            entered.set()
            release.wait(2.0)
            return super().sample()

    controller = MonitorController(
        AppConfig(),
        enable_serial=False,
        sensor_collector_factory=BlockingCollector,
    )
    controller.start()
    assert entered.wait(1.0)
    assert controller.stop(timeout=0.05) is False
    assert controller.started is True
    release.set()
    assert controller.stop(timeout=2.0) is True
    assert controller.started is False


def test_controller_power_transition_is_bounded_and_duplicate_resume_is_ignored() -> None:
    serial = ImmediateSerial()
    controller = MonitorController(
        AppConfig(),
        enable_serial=True,
        serial_writer=serial,
        sensor_collector_factory=FakeCollector,
    )

    assert controller.suspend_for_power_event(timeout=0.25) is True
    assert serial.suspend_timeouts == [0.25]
    assert controller.resume_from_power_event() is True
    assert controller.resume_from_power_event() is False
    assert serial.resumes == 1
