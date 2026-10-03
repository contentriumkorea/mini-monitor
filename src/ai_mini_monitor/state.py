from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from .models import (
    AIData,
    AIProviderKind,
    ConnectionData,
    DisplaySnapshot,
    Metric,
    SensorSnapshot,
    SyncStatus,
)
from .smoothing import TimeEma, clamp_percent


def not_configured_ai() -> AIData:
    return AIData(
        provider=AIProviderKind.NOT_CONFIGURED,
        title="AI PROVIDER",
        status=SyncStatus.SETUP_REQUIRED,
        primary_value="SETUP",
        primary_label="REQUIRED",
        fields=(("STATUS", "NOT LINKED"),),
    )


@dataclass(frozen=True, slots=True)
class RuntimeValues:
    sensor: SensorSnapshot | None
    ai: AIData
    connection: ConnectionData
    sensor_revision: int


class RuntimeStateStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sensor: SensorSnapshot | None = None
        self._ai = not_configured_ai()
        self._connection = ConnectionData()
        self._sensor_revision = 0

    def update_sensor(self, value: SensorSnapshot) -> None:
        with self._lock:
            self._sensor = value
            self._sensor_revision += 1

    def update_ai(self, value: AIData) -> None:
        with self._lock:
            self._ai = value

    def update_connection(self, value: ConnectionData) -> None:
        with self._lock:
            self._connection = value

    def read(self) -> RuntimeValues:
        with self._lock:
            return RuntimeValues(self._sensor, self._ai, self._connection, self._sensor_revision)


class DisplayComposer:
    """Separates exact raw readings from only the time-smoothed displayed values."""

    def __init__(self, *, sensor_period_seconds: float = 0.5) -> None:
        self.cpu = TimeEma(0.26)
        self.gpu = TimeEma(0.26)
        self.memory = TimeEma(0.30)
        self.cpu_temperature = TimeEma(0.28, alert_threshold=85.0, alert_time_constant_seconds=0.05)
        self.gpu_temperature = TimeEma(0.28, alert_threshold=85.0, alert_time_constant_seconds=0.05)
        self.cpu_history: deque[float | None] = deque(maxlen=max(2, round(30.0 / sensor_period_seconds)))
        self.gpu_history: deque[float | None] = deque(maxlen=max(2, round(30.0 / sensor_period_seconds)))
        self.memory_history: deque[float | None] = deque(maxlen=max(2, round(60.0 / sensor_period_seconds)))
        self._last_sensor_revision = -1

    def compose(self, values: RuntimeValues, *, monotonic_now: float) -> DisplaySnapshot:
        sensor = values.sensor
        if sensor is None:
            return DisplaySnapshot(ai=values.ai, connection=values.connection)
        if values.sensor_revision != self._last_sensor_revision:
            self._last_sensor_revision = values.sensor_revision
            self.cpu_history.append(clamp_percent(sensor.cpu_percent.value))
            self.gpu_history.append(clamp_percent(sensor.gpu_percent.value))
            self.memory_history.append(clamp_percent(sensor.memory_percent.value))
        return DisplaySnapshot(
            timestamp=datetime.now().astimezone(),
            cpu_model=sensor.cpu_model,
            gpu_model=sensor.gpu_model,
            cpu_percent=self._smooth_metric(self.cpu, sensor.cpu_percent, monotonic_now, percent=True),
            cpu_temperature=self._smooth_metric(self.cpu_temperature, sensor.cpu_temperature, monotonic_now),
            gpu_percent=self._smooth_metric(self.gpu, sensor.gpu_percent, monotonic_now, percent=True),
            gpu_temperature=self._smooth_metric(self.gpu_temperature, sensor.gpu_temperature, monotonic_now),
            memory_percent=self._smooth_metric(self.memory, sensor.memory_percent, monotonic_now, percent=True),
            memory_used_gib=sensor.memory_used_gib,
            memory_total_gib=sensor.memory_total_gib,
            memory_available_gib=sensor.memory_available_gib,
            cpu_history=tuple(self.cpu_history),
            gpu_history=tuple(self.gpu_history),
            memory_history=tuple(self.memory_history),
            ai=values.ai,
            connection=values.connection,
        )

    @staticmethod
    def _smooth_metric(smoother: TimeEma, raw: Metric, now: float, *, percent: bool = False) -> Metric:
        if raw.value is None or not math.isfinite(float(raw.value)):
            smoother.reset(None, now)
            return Metric(
                None,
                raw.unit,
                raw.reason or "sensor returned a non-finite value",
            )
        sample = clamp_percent(raw.value) if percent else raw.value
        return Metric(smoother.update(sample, now), raw.unit, raw.reason)
