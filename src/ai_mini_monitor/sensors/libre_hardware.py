from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..models import SensorReading
from ..resources import resource_path


UNIT_BY_TYPE = {
    "Temperature": "°C",
    "Load": "%",
    "Clock": "MHz",
    "Power": "W",
    "Data": "GiB",
    "SmallData": "MiB",
    "Throughput": "B/s",
    "Fan": "RPM",
    "Voltage": "V",
}


class LibreHardwareUnavailable(RuntimeError):
    pass


class LibreHardwareCollector:
    """One Computer instance, sampled by exactly one collection thread."""

    def __init__(self, dll_path: Path | None = None) -> None:
        self.dll_path = dll_path or resource_path("third_party/librehardwaremonitor/LibreHardwareMonitorLib.dll")
        self._computer: Any = None
        self._owner_thread: int | None = None
        self._last_error: str | None = None

    @property
    def available(self) -> bool:
        return self._computer is not None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def open(self) -> None:
        if os.name != "nt":
            raise LibreHardwareUnavailable("LibreHardwareMonitor requires Windows")
        if not self.dll_path.is_file():
            raise LibreHardwareUnavailable(f"LibreHardwareMonitor library not found: {self.dll_path}")
        try:
            from pythonnet import load

            try:
                load("netfx")
            except RuntimeError as error:
                if "already been loaded" not in str(error).lower():
                    raise
            import clr

            clr.AddReference(str(self.dll_path))
            from LibreHardwareMonitor.Hardware import Computer

            computer = Computer()
            computer.IsCpuEnabled = True
            computer.IsGpuEnabled = True
            # RAM comes from psutil. Explicitly keep every unrelated hardware poller off.
            for property_name in (
                "IsMemoryEnabled",
                "IsMotherboardEnabled",
                "IsControllerEnabled",
                "IsStorageEnabled",
                "IsNetworkEnabled",
                "IsPsuEnabled",
                "IsBatteryEnabled",
            ):
                if hasattr(computer, property_name):
                    setattr(computer, property_name, False)
            computer.Open()
            self._computer = computer
            self._owner_thread = threading.get_ident()
            self._last_error = None
        except Exception as error:
            self._last_error = f"{type(error).__name__}: {error}"
            self._computer = None
            raise LibreHardwareUnavailable(self._last_error) from error

    def close(self) -> None:
        if self._computer is not None:
            try:
                self._computer.Close()
            finally:
                self._computer = None
                self._owner_thread = None

    def sample(self) -> tuple[SensorReading, ...]:
        if self._computer is None:
            raise LibreHardwareUnavailable(self._last_error or "LibreHardwareMonitor is not open")
        if self._owner_thread != threading.get_ident():
            raise RuntimeError("LibreHardwareMonitor must be sampled and closed on its owner thread")
        readings: list[SensorReading] = []
        for hardware in list(self._computer.Hardware):
            self._update_hardware_tree(hardware, readings)
        return tuple(readings)

    def _update_hardware_tree(self, hardware: Any, output: list[SensorReading]) -> None:
        # Exactly one Update() per hardware object per snapshot; all cards share this output.
        hardware.Update()
        hardware_name = str(hardware.Name)
        hardware_type = str(hardware.HardwareType)
        for sensor in list(hardware.Sensors):
            sensor_type = str(sensor.SensorType)
            sensor_name = str(sensor.Name)
            if not _is_allowed_sensor(hardware_type, sensor_type, sensor_name):
                continue
            value = None if sensor.Value is None else float(sensor.Value)
            if value is not None and not math.isfinite(value):
                value = None
            output.append(
                SensorReading(
                    identifier=str(sensor.Identifier),
                    hardware=hardware_name,
                    name=sensor_name,
                    kind=f"{hardware_type}:{sensor_type}",
                    value=value,
                    unit=UNIT_BY_TYPE.get(sensor_type, ""),
                    reason=(
                        None
                        if value is not None
                        else "sensor returned no finite value"
                    ),
                )
            )
        for sub_hardware in list(hardware.SubHardware):
            self._update_hardware_tree(sub_hardware, output)

    def __enter__(self) -> "LibreHardwareCollector":
        self.open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def choose_sensor(
    readings: Iterable[SensorReading],
    *,
    hardware_family: str,
    sensor_type: str,
    configured_identifier: str | None = None,
) -> SensorReading | None:
    readings_list = list(readings)
    family = hardware_family.casefold()
    kind = sensor_type.casefold()

    def matches_family(reading: SensorReading) -> bool:
        return (
            family in reading.kind.partition(":")[0].casefold()
            and not ("gpu" in family and _is_virtual_gpu_hardware(reading.hardware))
        )

    if configured_identifier:
        return next(
            (
                reading
                for reading in readings_list
                if reading.identifier == configured_identifier
                and matches_family(reading)
                and reading.kind.partition(":")[2].casefold() == kind
            ),
            None,
        )
    candidates = [
        reading
        for reading in readings_list
        if matches_family(reading)
        and reading.kind.partition(":")[2].casefold() == kind
        and reading.value is not None
    ]
    if not candidates:
        return None
    generic_priority = ("total", "core", "package", "average")

    def score(reading: SensorReading) -> tuple[int, str]:
        name = reading.name.casefold()
        rank = next((index for index, word in enumerate(generic_priority) if word in name), len(generic_priority))
        return rank, reading.identifier

    return min(candidates, key=score)


def _is_virtual_gpu_hardware(hardware_name: str) -> bool:
    normalized = " ".join(str(hardware_name).casefold().split())
    return any(
        token in normalized
        for token in (
            "parsec",
            "virtual",
            "remote display",
            "remote desktop",
            "indirect display",
            "microsoft basic render",
        )
    )


def _is_allowed_sensor(
    hardware_type: str,
    sensor_type: str,
    sensor_name: str,
) -> bool:
    """Keep only data explicitly permitted by the product scope."""

    hardware = hardware_type.casefold()
    kind = sensor_type.casefold()
    name = sensor_name.casefold()
    if "cpu" in hardware:
        return kind == "temperature"
    if "gpu" not in hardware:
        return False
    if any(token in name for token in ("memory", "vram")):
        return False
    if kind == "temperature":
        return True
    if kind != "load":
        return False
    forbidden_load_names = (
        "bus",
        "video",
        "decode",
        "encode",
        "copy",
        "power",
        "security",
        "jpeg",
        "optical",
    )
    return not any(token in name for token in forbidden_load_names)
