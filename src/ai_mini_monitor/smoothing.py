from __future__ import annotations

import math
import time
from dataclasses import dataclass


@dataclass(slots=True)
class TimeEma:
    """Frame-rate-independent exponential smoothing with an alert fast-path."""

    time_constant_seconds: float = 0.26
    alert_threshold: float | None = None
    alert_time_constant_seconds: float = 0.06
    _value: float | None = None
    _last_time: float | None = None

    @property
    def value(self) -> float | None:
        return self._value

    def reset(self, value: float | None = None, now: float | None = None) -> float | None:
        self._value = value
        self._last_time = time.monotonic() if now is None else now
        return value

    def update(self, sample: float | None, now: float | None = None) -> float | None:
        timestamp = time.monotonic() if now is None else now
        if sample is None:
            self._last_time = timestamp
            return self._value
        sample = float(sample)
        if not math.isfinite(sample):
            self._last_time = timestamp
            return self._value
        if self._value is None or self._last_time is None:
            return self.reset(sample, timestamp)
        dt = max(0.0, timestamp - self._last_time)
        self._last_time = timestamp
        tau = self.time_constant_seconds
        if self.alert_threshold is not None and sample >= self.alert_threshold and sample > self._value:
            tau = min(tau, self.alert_time_constant_seconds)
        if tau <= 0.0:
            self._value = sample
            return sample
        alpha = 1.0 - math.exp(-dt / tau)
        self._value += alpha * (sample - self._value)
        return self._value


def clamp_percent(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return min(100.0, max(0.0, number))
