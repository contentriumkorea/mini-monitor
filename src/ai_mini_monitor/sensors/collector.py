from __future__ import annotations

import math
import os
import platform
import re
from datetime import datetime
from typing import Any, Callable

import psutil

from ..config import SensorConfig
from ..models import Metric, SensorReading, SensorSnapshot
from .libre_hardware import LibreHardwareCollector, LibreHardwareUnavailable, choose_sensor


GIB = 1024.0**3
MODEL_NAME_MAX_LENGTH = 64


def sanitize_hardware_model(value: object, *, max_length: int = MODEL_NAME_MAX_LENGTH) -> str | None:
    """Return a single-line, bounded hardware identity suitable for the display."""

    if value is None or max_length <= 0:
        return None
    try:
        text = str(value)
    except Exception:
        return None
    printable = "".join(character if character.isprintable() else " " for character in text)
    normalized = re.sub(r"\s+", " ", printable).strip()
    if not normalized:
        return None
    return normalized[:max_length].rstrip() or None


def read_cpu_model(
    *,
    _registry: Any = None,
    _os_name: str | None = None,
    _processor_reader: Callable[[], object] = platform.processor,
) -> str | None:
    """Read the stable Windows CPU identity without enabling another poller."""

    if (_os_name or os.name) == "nt":
        try:
            if _registry is None:
                import winreg

                _registry = winreg

            key_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            with _registry.OpenKey(
                _registry.HKEY_LOCAL_MACHINE,
                key_path,
                0,
                _registry.KEY_READ,
            ) as key:
                value, _value_type = _registry.QueryValueEx(key, "ProcessorNameString")
            model = sanitize_hardware_model(value)
            if model:
                return model
        except (ImportError, OSError, AttributeError):
            pass
    try:
        return sanitize_hardware_model(_processor_reader())
    except Exception:
        return None


class SystemSensorCollector:
    def __init__(
        self,
        config: SensorConfig,
        *,
        lhm: LibreHardwareCollector | None = None,
        cpu_model_reader: Callable[[], object] = read_cpu_model,
    ) -> None:
        self.config = config
        self.lhm = lhm or LibreHardwareCollector()
        self._cpu_model_reader = cpu_model_reader
        self._cpu_model: str | None = None
        self._cpu_model_resolved = False
        self._lhm_reason: str | None = None
        self._opened = False

    def open(self) -> None:
        psutil.cpu_percent(interval=None)
        if not self._cpu_model_resolved:
            try:
                self._cpu_model = sanitize_hardware_model(self._cpu_model_reader())
            except Exception:
                self._cpu_model = None
            self._cpu_model_resolved = True
        try:
            self.lhm.open()
            self._lhm_reason = None
        except LibreHardwareUnavailable as error:
            self._lhm_reason = str(error)
        self._opened = True

    def close(self) -> None:
        try:
            self.lhm.close()
        finally:
            self._opened = False

    def sample(self) -> SensorSnapshot:
        if not self._opened:
            self.open()
        captured = datetime.now().astimezone()
        try:
            cpu_percent = Metric(_finite(psutil.cpu_percent(interval=None)), "%")
        except Exception as error:
            cpu_percent = Metric(None, "%", f"psutil CPU error: {type(error).__name__}")
        try:
            memory = psutil.virtual_memory()
            memory_percent = Metric(_finite(memory.percent), "%")
            memory_used = Metric(_finite(memory.used) / GIB, "GiB")
            memory_total = Metric(_finite(memory.total) / GIB, "GiB")
            memory_available = Metric(_finite(memory.available) / GIB, "GiB")
        except Exception as error:
            reason = f"psutil memory error: {type(error).__name__}"
            memory_percent = Metric(None, "%", reason)
            memory_used = Metric(None, "GiB", reason)
            memory_total = Metric(None, "GiB", reason)
            memory_available = Metric(None, "GiB", reason)
        readings: tuple[SensorReading, ...] = ()
        try:
            if self.lhm.available:
                readings = self.lhm.sample()
        except Exception as error:
            self._lhm_reason = f"LibreHardwareMonitor sample failed: {type(error).__name__}: {error}"
        cpu_temperature, cpu_sensor = self._metric_and_sensor(
            readings,
            "Cpu",
            "Temperature",
            self.config.cpu_temperature_sensor,
            "CPU temperature",
        )
        gpu_percent, gpu_load_sensor = self._metric_and_sensor(
            readings,
            "Gpu",
            "Load",
            self.config.gpu_load_sensor,
            "GPU load",
        )
        gpu_temperature, gpu_temperature_sensor = self._metric_and_sensor(
            readings,
            "Gpu",
            "Temperature",
            self.config.gpu_temperature_sensor,
            "GPU temperature",
        )
        return SensorSnapshot(
            captured_at=captured,
            cpu_percent=cpu_percent,
            cpu_temperature=cpu_temperature,
            gpu_percent=gpu_percent,
            gpu_temperature=gpu_temperature,
            memory_percent=memory_percent,
            memory_used_gib=memory_used,
            memory_total_gib=memory_total,
            memory_available_gib=memory_available,
            cpu_model=self._cpu_model
            or sanitize_hardware_model(cpu_sensor.hardware if cpu_sensor else None),
            gpu_model=sanitize_hardware_model(
                gpu_load_sensor.hardware
                if gpu_load_sensor is not None
                else (
                    gpu_temperature_sensor.hardware
                    if gpu_temperature_sensor is not None
                    else None
                )
            ),
            discovered=readings,
        )

    def _metric_and_sensor(
        self,
        readings: tuple[SensorReading, ...],
        hardware_family: str,
        sensor_type: str,
        configured_identifier: str | None,
        label: str,
    ) -> tuple[Metric, SensorReading | None]:
        sensor = choose_sensor(
            readings,
            hardware_family=hardware_family,
            sensor_type=sensor_type,
            configured_identifier=configured_identifier,
        )
        unit = "°C" if sensor_type == "Temperature" else "%"
        if sensor is None:
            if configured_identifier:
                reason = f"configured sensor not found: {configured_identifier}"
            else:
                family = hardware_family.casefold()
                kind = sensor_type.casefold()
                reported_without_value = any(
                    family in reading.kind.partition(":")[0].casefold()
                    and reading.kind.partition(":")[2].casefold() == kind
                    for reading in readings
                )
                if reported_without_value:
                    reason = f"{label} sensors were reported without values; low-level access is unavailable"
                else:
                    reason = self._lhm_reason or f"no {label} sensor was reported"
            return Metric(None, unit, reason), None
        return Metric(sensor.value, sensor.unit or unit, sensor.reason), sensor

    def __enter__(self) -> "SystemSensorCollector":
        self.open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def _finite(value: object) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("sensor returned a non-finite value")
    return number
