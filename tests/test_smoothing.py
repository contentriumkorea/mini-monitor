# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import math

import pytest

from ai_mini_monitor.smoothing import TimeEma, clamp_percent


def test_time_ema_is_independent_of_update_partitioning() -> None:
    partitioned = TimeEma(time_constant_seconds=1.0)
    partitioned.reset(0.0, now=0.0)
    partitioned.update(100.0, now=1.0)
    result = partitioned.update(100.0, now=2.0)

    single = TimeEma(time_constant_seconds=1.0)
    single.reset(0.0, now=0.0)
    expected = single.update(100.0, now=2.0)
    assert result == pytest.approx(expected)
    assert result == pytest.approx(100.0 * (1.0 - math.exp(-2.0)))


def test_alert_threshold_uses_fast_path_only_for_rising_alert() -> None:
    normal = TimeEma(time_constant_seconds=1.0)
    normal.reset(0.0, now=0.0)
    normal_value = normal.update(100.0, now=0.1)

    alert = TimeEma(
        time_constant_seconds=1.0,
        alert_threshold=90.0,
        alert_time_constant_seconds=0.1,
    )
    alert.reset(0.0, now=0.0)
    alert_value = alert.update(100.0, now=0.1)
    assert alert_value > normal_value
    assert alert_value == pytest.approx(100.0 * (1.0 - math.exp(-1.0)))

    alert.reset(100.0, now=0.0)
    falling = alert.update(0.0, now=0.1)
    assert falling == pytest.approx(100.0 * math.exp(-0.1))


def test_missing_sample_preserves_value_but_advances_clock() -> None:
    ema = TimeEma(time_constant_seconds=1.0)
    ema.reset(20.0, now=0.0)
    assert ema.update(None, now=5.0) == 20.0
    assert ema.update(100.0, now=6.0) == pytest.approx(20.0 + (1.0 - math.exp(-1.0)) * 80.0)


def test_non_finite_samples_are_treated_as_missing() -> None:
    ema = TimeEma(time_constant_seconds=1.0)
    ema.reset(20.0, now=0.0)
    assert ema.update(float("nan"), now=1.0) == 20.0
    assert ema.update(float("inf"), now=2.0) == 20.0
    assert clamp_percent(float("nan")) is None
    assert clamp_percent(float("inf")) is None


def test_zero_tau_and_non_monotonic_clock_are_safe() -> None:
    ema = TimeEma(time_constant_seconds=0.0)
    assert ema.update(10.0, now=5.0) == 10.0
    assert ema.update(80.0, now=4.0) == 80.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), (-1, 0.0), (0, 0.0), (50.5, 50.5), (100, 100.0), (101, 100.0)],
)
def test_percent_clamping(value: float | None, expected: float | None) -> None:
    assert clamp_percent(value) == expected
