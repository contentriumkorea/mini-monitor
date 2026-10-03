# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_mini_monitor.endurance import (
    DeviceOutputConfirmationRequired,
    EnduranceOptions,
    run_endurance,
)
from ai_mini_monitor.transport.device import DeviceDetector
from ai_mini_monitor.transport.serial_writer import SerialWriterState


def target_port() -> SimpleNamespace:
    return SimpleNamespace(
        device="COM_FAKE",
        vid=0x1A86,
        pid=0x5722,
        serial_number="USB35INCHIPSV2",
        description="fake target",
        hwid="USB VID:PID=1A86:5722",
        location="fake",
    )


class StepClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.01
        return self.value


class FakeProcess:
    def __init__(self) -> None:
        self.calls = 0

    def cpu_percent(self, interval=None) -> float:
        assert interval is None
        self.calls += 1
        return 2.0

    def memory_info(self) -> SimpleNamespace:
        return SimpleNamespace(rss=50_000_000 + self.calls * 100)


class FakeController:
    def __init__(self, _config) -> None:
        self.serial = SimpleNamespace(
            state=SerialWriterState.DISCONNECTED,
            fatal_error=None,
            connection_count=0,
            replaced_count=2,
        )
        self.started = False

    def start(self) -> None:
        self.started = True
        self.serial.state = SerialWriterState.ONLINE
        self.serial.connection_count = 1

    def stop(self, timeout: float) -> bool:
        assert timeout > 0
        self.serial.state = SerialWriterState.STOPPED
        return True

    def save_latest(self, _path: Path) -> bool:
        return True

    def stats_snapshot(self) -> dict[str, float | int | None]:
        return {
            "sent_updates": 10,
            "sent_regions": 14,
            "sent_full_refreshes": 1,
            "serial_error_events": 0,
            "unhandled_worker_errors": 0,
            "full_refresh_submissions": 1,
            "max_pending": 1,
            "queue_latency_p95_ms": 120.0,
            "sensor_to_serial_p95_ms": 180.0,
        }


def test_confirmation_gate_runs_before_device_discovery(tmp_path: Path) -> None:
    enumerated = False

    def provider():
        nonlocal enumerated
        enumerated = True
        return [target_port()]

    with pytest.raises(DeviceOutputConfirmationRequired):
        run_endurance(
            EnduranceOptions(
                duration_seconds=1,
                output_path=tmp_path / "never.json",
                confirm_device_output=False,
            ),
            detector=DeviceDetector(provider),
            controller_factory=FakeController,
            process=FakeProcess(),
        )
    assert not enumerated


def test_fake_endurance_records_live_pipeline_metrics(tmp_path: Path) -> None:
    output = tmp_path / "endurance.json"
    result = run_endurance(
        EnduranceOptions(
            duration_seconds=0.03,
            output_path=output,
            confirm_device_output=True,
            sample_interval_seconds=0.01,
        ),
        detector=DeviceDetector(lambda: [target_port()]),
        controller_factory=FakeController,
        process=FakeProcess(),
        monotonic=StepClock(),
        sleep=lambda _seconds: None,
        wall_now=lambda: datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    assert result["success"] is True
    assert result["identity_verified"] is True
    assert result["writer_closed"] is True
    assert result["controller_stop_complete"] is True
    assert result["completed_partial_update_rate_hz"] > 0
    assert result["completed_partial_region_rate_hz"] > 0
    assert result["controller_stats"]["sensor_to_serial_p95_ms"] == 180.0
    assert "memory_warmup_excluded_seconds" in result
    assert output.is_file()


def test_powershell_wrapper_requires_explicit_confirmation() -> None:
    script = (
        Path(__file__).parents[1] / "scripts" / "Endurance-Test.ps1"
    ).read_text(encoding="utf-8")
    assert "[switch]$ConfirmDeviceOutput" in script
    assert "if (-not $ConfirmDeviceOutput)" in script
    assert "--confirm-device-output" in script
