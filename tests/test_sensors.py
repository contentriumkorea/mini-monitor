# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_mini_monitor.config import SensorConfig
from ai_mini_monitor.models import AIData, ConnectionData, SensorReading
from ai_mini_monitor.sensors.collector import (
    GIB,
    MODEL_NAME_MAX_LENGTH,
    SystemSensorCollector,
    read_cpu_model,
    sanitize_hardware_model,
)
from ai_mini_monitor.sensors.libre_hardware import (
    UNIT_BY_TYPE,
    LibreHardwareCollector,
    LibreHardwareUnavailable,
    choose_sensor,
)
from ai_mini_monitor.state import DisplayComposer, RuntimeValues


class MissingLhm:
    available = False

    def __init__(self) -> None:
        self.closed = False

    def open(self) -> None:
        raise LibreHardwareUnavailable("test LHM unavailable")

    def sample(self):
        raise AssertionError("unavailable LHM must not be sampled")

    def close(self) -> None:
        self.closed = True


def patch_psutil(monkeypatch) -> None:
    monkeypatch.setattr("ai_mini_monitor.sensors.collector.psutil.cpu_percent", lambda interval=None: 42.5)
    memory = SimpleNamespace(percent=25.0, used=32 * GIB, total=128 * GIB, available=96 * GIB)
    monkeypatch.setattr("ai_mini_monitor.sensors.collector.psutil.virtual_memory", lambda: memory)


def test_missing_sensors_remain_unavailable_instead_of_becoming_zero(monkeypatch) -> None:
    patch_psutil(monkeypatch)
    lhm = MissingLhm()
    collector = SystemSensorCollector(
        SensorConfig(),
        lhm=lhm,
        cpu_model_reader=lambda: None,
    )
    result = collector.sample()
    assert result.cpu_percent.value == 42.5
    assert result.memory_percent.value == 25.0
    assert result.cpu_temperature.value is None
    assert result.gpu_percent.value is None
    assert result.gpu_temperature.value is None
    assert result.cpu_model is None
    assert result.gpu_model is None
    assert "test LHM unavailable" in (result.cpu_temperature.reason or "")
    collector.close()
    assert lhm.closed


class SnapshotLhm:
    def __init__(self, readings: tuple[SensorReading, ...]) -> None:
        self.readings = readings
        self.available = False
        self.sample_calls = 0

    def open(self) -> None:
        self.available = True

    def sample(self) -> tuple[SensorReading, ...]:
        self.sample_calls += 1
        return self.readings

    def close(self) -> None:
        self.available = False


def test_system_snapshot_shares_one_lhm_sample_across_cpu_and_gpu(monkeypatch) -> None:
    patch_psutil(monkeypatch)
    readings = (
        SensorReading("/cpu/temp", "CPU", "CPU Package", "Cpu:Temperature", 71.0, UNIT_BY_TYPE["Temperature"]),
        SensorReading("/gpu/load", "GPU", "GPU Core", "GpuNvidia:Load", 63.0, "%"),
        SensorReading("/gpu/temp", "GPU", "GPU Core", "GpuNvidia:Temperature", 68.0, UNIT_BY_TYPE["Temperature"]),
    )
    lhm = SnapshotLhm(readings)
    result = SystemSensorCollector(
        SensorConfig(),
        lhm=lhm,
        cpu_model_reader=lambda: None,
    ).sample()
    assert lhm.sample_calls == 1
    assert result.cpu_temperature.value == 71.0
    assert result.gpu_percent.value == 63.0
    assert result.gpu_temperature.value == 68.0
    assert result.cpu_model == "CPU"
    assert result.gpu_model == "GPU"
    assert result.discovered == readings


def test_hardware_model_sanitization_is_single_line_and_bounded() -> None:
    model = sanitize_hardware_model("  AMD\tRyzen\n9\x00 " + "X" * 100)

    assert model is not None
    assert model.startswith("AMD Ryzen 9 ")
    assert len(model) == MODEL_NAME_MAX_LENGTH
    assert "\n" not in model and "\t" not in model and "\x00" not in model
    assert sanitize_hardware_model("\r\n\t") is None


def test_cpu_model_prefers_fake_windows_registry_identity() -> None:
    calls: list[tuple[object, str, int, int]] = []

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            pass

    class FakeRegistry:
        HKEY_LOCAL_MACHINE = object()
        KEY_READ = 0x20019

        @classmethod
        def OpenKey(cls, root: object, path: str, reserved: int, access: int):
            calls.append((root, path, reserved, access))
            return FakeKey()

        @staticmethod
        def QueryValueEx(key: FakeKey, name: str):
            assert name == "ProcessorNameString"
            return "  Intel(R)\tCore(TM) Ultra 9  ", 1

    result = read_cpu_model(
        _registry=FakeRegistry,
        _os_name="nt",
        _processor_reader=lambda: "FORBIDDEN FALLBACK",
    )

    assert result == "Intel(R) Core(TM) Ultra 9"
    assert calls == [
        (
            FakeRegistry.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            0,
            FakeRegistry.KEY_READ,
        )
    ]


def test_models_use_cached_cpu_identity_and_selected_physical_gpu(monkeypatch) -> None:
    patch_psutil(monkeypatch)
    readings = (
        SensorReading(
            "/gpu/00-virtual/load",
            "Parsec Virtual Display Adapter",
            "GPU Core",
            "GpuGeneric:Load",
            99.0,
            "%",
        ),
        SensorReading(
            "/gpu/10-physical/load",
            "  NVIDIA   GeForce RTX 4090  ",
            "GPU Core",
            "GpuNvidia:Load",
            61.0,
            "%",
        ),
        SensorReading(
            "/gpu/10-physical/temp",
            "NVIDIA GeForce RTX 4090",
            "GPU Core",
            "GpuNvidia:Temperature",
            67.0,
            UNIT_BY_TYPE["Temperature"],
        ),
    )
    cpu_calls = 0

    def fake_cpu_model() -> str:
        nonlocal cpu_calls
        cpu_calls += 1
        return "  AMD\tRyzen 9 9950X  "

    collector = SystemSensorCollector(
        SensorConfig(),
        lhm=SnapshotLhm(readings),
        cpu_model_reader=fake_cpu_model,
    )

    first = collector.sample()
    second = collector.sample()

    assert cpu_calls == 1
    assert first.cpu_model == second.cpu_model == "AMD Ryzen 9 9950X"
    assert first.gpu_model == second.gpu_model == "NVIDIA GeForce RTX 4090"
    assert first.gpu_percent.value == second.gpu_percent.value == 61.0


def test_display_composer_propagates_models_without_modification(monkeypatch) -> None:
    patch_psutil(monkeypatch)
    readings = (
        SensorReading(
            "/gpu/load",
            "AMD Radeon RX 7900 XTX",
            "GPU Core",
            "GpuAmd:Load",
            48.0,
            "%",
        ),
    )
    sensor = SystemSensorCollector(
        SensorConfig(),
        lhm=SnapshotLhm(readings),
        cpu_model_reader=lambda: "Intel Core i9-14900K",
    ).sample()

    display = DisplayComposer().compose(
        RuntimeValues(sensor, AIData(), ConnectionData(), sensor_revision=1),
        monotonic_now=1.0,
    )

    assert display.cpu_model == "Intel Core i9-14900K"
    assert display.gpu_model == "AMD Radeon RX 7900 XTX"


class FakeSensor:
    def __init__(self, identifier: str, name: str, kind: str, value: float | None) -> None:
        self.Identifier = identifier
        self.Name = name
        self.SensorType = kind
        self.Value = value


class FakeHardware:
    def __init__(
        self,
        name: str,
        hardware_type: str,
        sensors: list[FakeSensor],
        sub_hardware: list["FakeHardware"] | None = None,
    ) -> None:
        self.Name = name
        self.HardwareType = hardware_type
        self.Sensors = sensors
        self.SubHardware = sub_hardware or []
        self.update_calls = 0

    def Update(self) -> None:
        self.update_calls += 1


def test_fake_lhm_updates_each_hardware_object_once_per_snapshot() -> None:
    cpu_sub = FakeHardware("CPU cores", "Cpu", [FakeSensor("/cpu/core", "Core 0", "Temperature", 69.0)])
    cpu = FakeHardware(
        "CPU",
        "Cpu",
        [FakeSensor("/cpu/package", "CPU Package", "Temperature", 72.0)],
        [cpu_sub],
    )
    gpu = FakeHardware("GPU", "GpuNvidia", [FakeSensor("/gpu/load", "GPU Core", "Load", 55.0)])
    collector = LibreHardwareCollector(Path("unused-in-test.dll"))
    collector._computer = SimpleNamespace(Hardware=[cpu, gpu])
    collector._owner_thread = threading.get_ident()

    readings = collector.sample()
    assert (cpu.update_calls, cpu_sub.update_calls, gpu.update_calls) == (1, 1, 1)
    assert {reading.identifier for reading in readings} == {"/cpu/package", "/cpu/core", "/gpu/load"}


def test_non_finite_lhm_values_are_reported_unavailable() -> None:
    gpu = FakeHardware(
        "GPU",
        "GpuNvidia",
        [FakeSensor("/gpu/temp", "GPU Core", "Temperature", float("nan"))],
    )
    collector = LibreHardwareCollector(Path("unused-in-test.dll"))
    collector._computer = SimpleNamespace(Hardware=[gpu])
    collector._owner_thread = threading.get_ident()

    reading = collector.sample()[0]
    assert reading.value is None
    assert "finite" in (reading.reason or "")


def test_lhm_snapshot_collects_only_explicit_cpu_gpu_allowlist() -> None:
    cpu = FakeHardware(
        "CPU",
        "Cpu",
        [
            FakeSensor("/cpu/temp", "CPU Package", "Temperature", 70.0),
            FakeSensor("/cpu/load", "CPU Total", "Load", 25.0),
            FakeSensor("/cpu/fan", "CPU Fan", "Fan", 900.0),
            FakeSensor("/cpu/voltage", "CPU Core", "Voltage", 1.2),
        ],
    )
    gpu = FakeHardware(
        "GPU",
        "GpuNvidia",
        [
            FakeSensor("/gpu/core-load", "GPU Core", "Load", 55.0),
            FakeSensor("/gpu/core-temp", "GPU Core", "Temperature", 65.0),
            FakeSensor("/gpu/vram", "GPU Memory", "Load", 40.0),
            FakeSensor("/gpu/vram-temp", "GPU Memory Junction", "Temperature", 75.0),
            FakeSensor("/gpu/fan", "GPU Fan", "Fan", 1200.0),
            FakeSensor("/gpu/data", "GPU Memory Used", "SmallData", 8.0),
            FakeSensor("/gpu/voltage", "GPU Core", "Voltage", 0.9),
        ],
    )
    collector = LibreHardwareCollector(Path("unused-in-test.dll"))
    collector._computer = SimpleNamespace(Hardware=[cpu, gpu])
    collector._owner_thread = threading.get_ident()

    readings = collector.sample()

    assert {reading.identifier for reading in readings} == {
        "/cpu/temp",
        "/gpu/core-load",
        "/gpu/core-temp",
    }
    assert {reading.kind for reading in readings} == {
        "Cpu:Temperature",
        "GpuNvidia:Load",
        "GpuNvidia:Temperature",
    }


def test_sensor_selection_honors_explicit_identifier_and_skips_none() -> None:
    readings = (
        SensorReading("/cpu/missing", "CPU", "CPU Package", "Cpu:Temperature", None, UNIT_BY_TYPE["Temperature"]),
        SensorReading("/cpu/core", "CPU", "Core Average", "Cpu:Temperature", 65.0, UNIT_BY_TYPE["Temperature"]),
    )
    assert choose_sensor(readings, hardware_family="Cpu", sensor_type="Temperature") == readings[1]
    assert (
        choose_sensor(
            readings,
            hardware_family="Cpu",
            sensor_type="Temperature",
            configured_identifier="/cpu/missing",
        )
        == readings[0]
    )
    assert choose_sensor(readings, hardware_family="Gpu", sensor_type="Load") is None


def test_configured_identifier_cannot_cross_sensor_family_or_type() -> None:
    readings = (
        SensorReading(
            "/gpu/load",
            "GPU",
            "GPU Core",
            "GpuNvidia:Load",
            72.0,
            "%",
        ),
    )
    assert (
        choose_sensor(
            readings,
            hardware_family="Gpu",
            sensor_type="Temperature",
            configured_identifier="/gpu/load",
        )
        is None
    )


def test_gpu_selection_rejects_virtual_display_hardware() -> None:
    virtual = SensorReading(
        "/gpu/virtual/load",
        "Parsec Virtual Display Adapter",
        "GPU Core",
        "GpuGeneric:Load",
        100.0,
        "%",
    )
    physical = SensorReading(
        "/gpu/physical/load",
        "NVIDIA GeForce RTX 4090",
        "GPU Core",
        "GpuNvidia:Load",
        52.0,
        "%",
    )

    assert choose_sensor(
        (virtual, physical),
        hardware_family="Gpu",
        sensor_type="Load",
    ) == physical
    assert (
        choose_sensor(
            (virtual, physical),
            hardware_family="Gpu",
            sensor_type="Load",
            configured_identifier=virtual.identifier,
        )
        is None
    )
