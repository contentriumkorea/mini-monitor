# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ai_mini_monitor.benchmark import (
    BENCHMARK_DEMO_TIME,
    BENCHMARK_WORKLOADS,
    PARTIAL_WORKLOADS,
    PATCH_SIZES,
    BenchmarkOptions,
    DeviceOutputConfirmationRequired,
    main,
    run_device_benchmark,
)
from ai_mini_monitor.demo import demo_snapshot
from ai_mini_monitor.rendering.renderer import DashboardRenderer
from ai_mini_monitor.transport.device import DeviceDetector
from ai_mini_monitor.transport.serial_writer import SerialWriterEvent, SerialWriterState


def target_port(device: str = "COM_FAKE", serial_number: str = "USB35INCHIPSV2") -> SimpleNamespace:
    return SimpleNamespace(
        device=device,
        vid=0x1A86,
        pid=0x5722,
        serial_number=serial_number,
        description="fake exact display",
        hwid="USB VID:PID=1A86:5722",
        location="fake",
    )


class StepClock:
    def __init__(self, step: float = 0.01) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


class FakeProcess:
    def __init__(self) -> None:
        self.calls = 0

    def cpu_percent(self, interval: object = None) -> float:
        assert interval is None
        self.calls += 1
        return float(self.calls % 7)

    def memory_info(self) -> SimpleNamespace:
        return SimpleNamespace(rss=100_000 + self.calls * 128)


class FakeWriter:
    def __init__(
        self,
        *,
        status_callback: Any,
        emit_error: bool = False,
        **kwargs: Any,
    ) -> None:
        self.kwargs = kwargs
        self.status_callback = status_callback
        self.emit_error = emit_error
        self.updates = []
        self.pending_count = 0
        self.replaced_count = 0
        self.last_sent_sequence = -1
        self.connection_count = 0
        self.fatal_error = None
        self.started = False
        self.stopped = False

    def submit(self, update: object) -> None:
        self.updates.append(update)
        self.pending_count = 1

    def start(self) -> None:
        self.started = True
        self.connection_count = 1
        self.status_callback(SerialWriterEvent(SerialWriterState.ONLINE, "COM_FAKE", None))
        if self.emit_error:
            self.status_callback(
                SerialWriterEvent(
                    SerialWriterState.RECONNECTING,
                    None,
                    "synthetic serial failure",
                )
            )
            self.connection_count = 2
            self.status_callback(SerialWriterEvent(SerialWriterState.ONLINE, "COM_FAKE", None))

    def wait_for_sequence(self, sequence: int, timeout: float = 1.0) -> bool:
        assert timeout > 0
        self.last_sent_sequence = sequence
        self.pending_count = 0
        return True

    def stop(self, timeout: float = 1.0) -> bool:
        assert timeout > 0
        self.stopped = True
        return True


class FakeWriterFactory:
    def __init__(self, *, emit_error: bool = False) -> None:
        self.emit_error = emit_error
        self.calls: list[dict[str, Any]] = []
        self.writer: FakeWriter | None = None

    def __call__(self, **kwargs: Any) -> FakeWriter:
        self.calls.append(kwargs)
        self.writer = FakeWriter(emit_error=self.emit_error, **kwargs)
        return self.writer


def options(path: Path, *, confirmed: bool = True) -> BenchmarkOptions:
    return BenchmarkOptions(
        duration_seconds=0.001,
        output_path=path,
        confirm_device_output=confirmed,
        update_interval_seconds=0.0,
        completion_timeout_seconds=0.5,
        stop_timeout_seconds=0.5,
    )


def test_confirmation_is_required_before_discovery_or_output(tmp_path: Path) -> None:
    provider_called = False

    def provider() -> list[SimpleNamespace]:
        nonlocal provider_called
        provider_called = True
        return [target_port()]

    output = tmp_path / "should-not-exist.json"
    with pytest.raises(DeviceOutputConfirmationRequired):
        run_device_benchmark(
            options(output, confirmed=False),
            detector=DeviceDetector(provider),
            writer_factory=FakeWriterFactory(),
            process=FakeProcess(),
        )
    assert not provider_called
    assert not output.exists()


def test_exact_identity_is_revalidated_before_writer_creation(tmp_path: Path) -> None:
    enumerations = 0

    def provider() -> list[SimpleNamespace]:
        nonlocal enumerations
        enumerations += 1
        if enumerations == 1:
            return [target_port("COM9")]
        return [target_port("COM9", serial_number="IMPOSTOR")]

    factory = FakeWriterFactory()
    output = tmp_path / "identity-failed.json"
    result = run_device_benchmark(
        options(output),
        detector=DeviceDetector(provider),
        writer_factory=factory,
        process=FakeProcess(),
        monotonic=StepClock(),
    )

    assert enumerations == 2
    assert factory.calls == []
    assert result["identity_verified"] is False
    assert result["success"] is False
    assert result["exceptions"][0]["stage"] == "device_identity_preflight"
    assert json.loads(output.read_text(encoding="utf-8"))["success"] is False


def test_benchmark_uses_only_rendered_full_frame_and_exact_safe_patches(
    tmp_path: Path,
) -> None:
    factory = FakeWriterFactory()
    output = tmp_path / "benchmark.json"
    result = run_device_benchmark(
        options(output),
        detector=DeviceDetector(lambda: [target_port()]),
        writer_factory=factory,
        process=FakeProcess(),
        monotonic=StepClock(),
        wall_now=lambda: datetime(2026, 8, 10, tzinfo=timezone.utc),
    )

    assert factory.writer is not None
    writer = factory.writer
    assert writer.started and writer.stopped
    assert factory.calls[0]["detector"] is not None
    assert factory.calls[0]["manual_port"] is None

    calibration_updates = writer.updates[: len(BENCHMARK_WORKLOADS)]
    endurance_updates = writer.updates[len(BENCHMARK_WORKLOADS) : -1]
    final_restore = writer.updates[-1]

    assert len(calibration_updates) == 5
    assert calibration_updates[0].full_refresh
    assert all(not update.full_refresh for update in calibration_updates[1:])
    assert endurance_updates
    assert all(not update.full_refresh for update in endurance_updates)
    assert final_restore.full_refresh
    final_rgb = (
        DashboardRenderer()
        .render(demo_snapshot("normal", now=BENCHMARK_DEMO_TIME))
        .convert("RGB")
        .tobytes()
    )
    assert final_restore.regions[0].rgb == final_rgb

    calibration_sizes = [
        (update.regions[0].width, update.regions[0].height)
        for update in calibration_updates
    ]
    assert calibration_sizes == [
        (spec.width, spec.height) for spec in BENCHMARK_WORKLOADS
    ]
    assert {
        (update.regions[0].width, update.regions[0].height)
        for update in endurance_updates
    }.issubset(set(PATCH_SIZES))
    assert {
        (spec.width, spec.height) for spec in BENCHMARK_WORKLOADS if not spec.full_refresh
    } == set(PATCH_SIZES)
    assert all(not spec.full_refresh for spec in PARTIAL_WORKLOADS)
    assert all(update.width == 480 and update.height == 320 for update in writer.updates)

    rendered = DashboardRenderer()
    states = ("zero", "hundred", "temperature_warning", "memory_99", "ai_delayed", "normal")
    rendered_frames = [
        rendered.render(demo_snapshot(state, now=BENCHMARK_DEMO_TIME)).convert("RGB")
        for state in states
    ]
    for update in writer.updates[:-1]:
        region = update.regions[0]
        box = (region.x, region.y, region.x + region.width, region.y + region.height)
        assert region.rgb in {frame.crop(box).tobytes() for frame in rendered_frames}

    assert result["success"] is True
    assert result["required_workloads_completed"] is True
    assert result["calibration"]["complete"] is True
    assert result["calibration"]["submitted_count"] == 5
    assert result["calibration"]["full_frame_submissions"] == 1
    assert len(result["calibration"]["samples"]) == 5
    assert result["endurance"]["partial_updates_only"] is True
    assert result["endurance"]["full_frame_submissions"] == 0
    assert result["endurance_full_frame_submissions"] == 0
    assert all(
        sample["full_refresh"] is False
        for sample in result["endurance"]["samples"]
    )
    assert result["measurement_full_frame_submissions"] == 1
    assert result["cleanup_full_frame_submissions"] == 1
    assert result["full_frame_submissions"] == 2
    assert result["partial_update_rate_hz"] > 0
    assert result["final_dashboard_restored"] is True
    assert result["writer_closed"] is True
    assert result["max_pending"] <= 1
    assert result["serial_error_count"] == 0
    assert result["reconnect_count"] == 0
    assert result["queue_latency_samples_ms"]
    assert result["update_rate_hz"] > 0
    assert result["process_samples"]
    assert result["rss_peak_bytes"] >= result["rss_start_bytes"]
    assert result["memory_growth_bytes"] == result["rss_end_bytes"] - result["rss_start_bytes"]
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["calibration"] == result["calibration"]
    assert saved["endurance"] == result["endurance"]


def test_serial_errors_and_reconnects_are_counted(tmp_path: Path) -> None:
    result = run_device_benchmark(
        options(tmp_path / "unstable.json"),
        detector=DeviceDetector(lambda: [target_port()]),
        writer_factory=FakeWriterFactory(emit_error=True),
        process=FakeProcess(),
        monotonic=StepClock(),
    )
    assert result["serial_error_count"] == 1
    assert result["reconnect_count"] == 1
    assert result["connection_count"] == 2
    assert result["serial_error_messages"] == ["synthetic serial failure"]
    assert result["success"] is False


def test_python_cli_requires_explicit_confirmation(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--duration", "1", "--output", str(tmp_path / "never.json")])
    assert raised.value.code == 2


def test_powershell_wrapper_has_a_confirmation_gate() -> None:
    script = (
        Path(__file__).parents[1] / "scripts" / "Benchmark-Device.ps1"
    ).read_text(encoding="utf-8")
    assert "[switch]$ConfirmDeviceOutput" in script
    assert "if (-not $ConfirmDeviceOutput)" in script
    assert "--confirm-device-output" in script
    assert "--duration" in script
    assert "--output" in script
