from __future__ import annotations

import ctypes
import os
import platform
import sys
from dataclasses import asdict
from datetime import datetime
from typing import Any

import PIL
import psutil
import serial

from .config import AppConfig
from .security.dpapi import DPAPISecretStore
from .sensors.collector import SystemSensorCollector
from .transport.device import DeviceDetector


def collect_diagnostics(config: AppConfig) -> dict[str, Any]:
    """Read-only diagnostic snapshot. It never opens a serial port."""

    detector = DeviceDetector()
    ports = detector.list_ports()
    sensor_collector = SystemSensorCollector(config.sensors)
    sensor = None
    sensor_error = None
    try:
        sensor_collector.open()
        sensor = sensor_collector.sample()
    except Exception as error:
        sensor_error = f"{type(error).__name__}: {error}"
    finally:
        sensor_collector.close()

    candidates = []
    if sensor is not None:
        for reading in sensor.discovered:
            hardware_type, _, sensor_type = reading.kind.partition(":")
            if ("cpu" in hardware_type.casefold() or "gpu" in hardware_type.casefold()) and sensor_type in {
                "Load",
                "Temperature",
            }:
                candidates.append(asdict(reading))

    return {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "read_only": True,
        "serial_port_opened": False,
        "runtime": {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "is_admin": bool(ctypes.windll.shell32.IsUserAnAdmin()) if os.name == "nt" else False,
            "pillow": PIL.__version__,
            "psutil": psutil.__version__,
            "pyserial": serial.VERSION,
        },
        "device": {
            "ports": [
                {
                    **asdict(port),
                    "exact_target": port.is_exact_target,
                }
                for port in ports
            ],
            "exact_targets": [port.device for port in ports if port.is_exact_target],
            "configured_manual_port": config.device.manual_port,
        },
        "sensors": {
            "error": sensor_error,
            "cpu_model": sensor.cpu_model if sensor else None,
            "gpu_model": sensor.gpu_model if sensor else None,
            "cpu_percent": _metric(sensor.cpu_percent if sensor else None),
            "cpu_temperature": _metric(sensor.cpu_temperature if sensor else None),
            "gpu_percent": _metric(sensor.gpu_percent if sensor else None),
            "gpu_temperature": _metric(sensor.gpu_temperature if sensor else None),
            "memory_percent": _metric(sensor.memory_percent if sensor else None),
            "memory_used_gib": _metric(sensor.memory_used_gib if sensor else None),
            "memory_total_gib": _metric(sensor.memory_total_gib if sensor else None),
            "candidate_sensors": candidates,
        },
        "ai": {
            "provider": config.ai.provider,
            "codex_local_consent": config.ai.codex_local_consent,
            "codex_sessions_scanned": False,
            "admin_key_configured": DPAPISecretStore().configured(),
            "secret_value_included": False,
        },
    }


def _metric(metric: Any) -> dict[str, Any] | None:
    if metric is None:
        return None
    return {"value": metric.value, "unit": metric.unit, "reason": metric.reason}
