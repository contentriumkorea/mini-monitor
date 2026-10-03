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
from .libre_hardware import (
    LibreHardwareCollector,
    LibreHardwareUnavailable,
    _is_virtual_gpu_hardware,
    choose_sensor,
    gpu_device_key,
)


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
        selected_gpu = _select_gpu_readings(readings, self.config)
        gpu_percent, gpu_load_sensor = self._gpu_metric_and_sensor(
            readings,
            selected_gpu,
            "Load",
            self.config.gpu_load_sensor,
            "GPU load",
        )
        gpu_temperature, gpu_temperature_sensor = self._gpu_metric_and_sensor(
            readings,
            selected_gpu,
            "Temperature",
            self.config.gpu_temperature_sensor,
            "GPU temperature",
        )
        gpu_vram_used, gpu_vram_total = _gpu_memory_metrics(selected_gpu)
        gpu_power = _gpu_power_metric(selected_gpu)
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
            gpu_model=sanitize_hardware_model(selected_gpu[0].hardware if selected_gpu else None),
            discovered=readings,
            gpu_vram_used_gib=gpu_vram_used,
            gpu_vram_total_gib=gpu_vram_total,
            gpu_power_w=gpu_power,
        )

    def _gpu_metric_and_sensor(
        self,
        all_readings: tuple[SensorReading, ...],
        selected_gpu: tuple[SensorReading, ...],
        sensor_type: str,
        configured_identifier: str | None,
        label: str,
    ) -> tuple[Metric, SensorReading | None]:
        if configured_identifier and any(
            reading.identifier == configured_identifier
            and reading.kind.partition(":")[2] == sensor_type
            and reading not in selected_gpu
            for reading in all_readings
        ):
            unit = "°C" if sensor_type == "Temperature" else "%"
            return Metric(None, unit, "configured sensor belongs to a different GPU"), None
        return self._metric_and_sensor(
            selected_gpu, "Gpu", sensor_type, configured_identifier, label
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


def _select_gpu_readings(
    readings: tuple[SensorReading, ...], config: SensorConfig
) -> tuple[SensorReading, ...]:
    groups: dict[str, list[SensorReading]] = {}
    for reading in readings:
        if "gpu" not in reading.kind.partition(":")[0].casefold():
            continue
        if _is_virtual_gpu_hardware(reading.hardware):
            continue
        groups.setdefault(gpu_device_key(reading), []).append(reading)
    if not groups:
        return ()
    for identifier, sensor_type in (
        (config.gpu_load_sensor, "Load"),
        (config.gpu_temperature_sensor, "Temperature"),
    ):
        if identifier:
            for group in groups.values():
                if any(
                    reading.identifier == identifier
                    and reading.kind.partition(":")[2] == sensor_type
                    for reading in group
                ):
                    return tuple(group)

    def rank(item: tuple[str, list[SensorReading]]) -> tuple[int, int, int, int, str]:
        key, group = item
        has_total = any(
            reading.name.casefold() == "gpu memory total"
            and reading.kind.partition(":")[2] == "SmallData"
            and reading.value is not None
            and math.isfinite(reading.value)
            and reading.value > 0
            for reading in group
        )
        has_load = any(
            reading.name.casefold() == "gpu core"
            and reading.kind.partition(":")[2] == "Load"
            and reading.value is not None
            for reading in group
        )
        has_temperature = any(
            reading.name.casefold() == "gpu core"
            and reading.kind.partition(":")[2] == "Temperature"
            and reading.value is not None
            for reading in group
        )
        is_discrete_family = any(
            reading.kind.partition(":")[0].casefold() in ("gpunvidia", "gpuamd")
            for reading in group
        )
        return (-int(has_total), -int(has_load), -int(is_discrete_family), -int(has_temperature), key)

    return tuple(min(groups.items(), key=rank)[1])


def _gpu_memory_metrics(readings: tuple[SensorReading, ...]) -> tuple[Metric, Metric]:
    for used_name, total_name in (
        ("GPU Memory Used", "GPU Memory Total"),
        ("D3D Dedicated Memory Used", "D3D Dedicated Memory Total"),
    ):
        matching = {
            reading.name.casefold(): reading
            for reading in readings
            if reading.kind.partition(":")[2] == "SmallData"
            and reading.unit == "MiB"
            and reading.name.casefold() in (used_name.casefold(), total_name.casefold())
        }
        used = matching.get(used_name.casefold())
        total = matching.get(total_name.casefold())
        if used is None or total is None or used.value is None or total.value is None:
            continue
        if (
            not math.isfinite(used.value)
            or not math.isfinite(total.value)
            or used.value < 0
            or total.value <= 0
            or used.value > total.value
        ):
            continue
        return Metric(used.value / 1024.0, "GiB"), Metric(total.value / 1024.0, "GiB")
    reason = "dedicated GPU memory used/total pair unavailable"
    return Metric(None, "GiB", reason), Metric(None, "GiB", reason)


def _gpu_power_metric(readings: tuple[SensorReading, ...]) -> Metric:
    for reading in readings:
        if (
            reading.kind.partition(":")[2] == "Power"
            and reading.name.casefold() == "gpu package"
            and reading.unit == "W"
            and reading.value is not None
            and math.isfinite(reading.value)
            and reading.value >= 0
        ):
            return Metric(reading.value, "W")
    return Metric(None, "W", "GPU package power unavailable")
