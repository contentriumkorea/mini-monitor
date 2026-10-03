# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_mini_monitor.transport.device import (
    AmbiguousDevice,
    DeviceDetector,
    DeviceIdentityMismatch,
    DeviceNotFound,
    is_exact_target,
)


def port(
    device: str,
    *,
    vid: int | None = 0x1A86,
    pid: int | None = 0x5722,
    serial_number: str | None = "USB35INCHIPSV2",
) -> SimpleNamespace:
    return SimpleNamespace(
        device=device,
        vid=vid,
        pid=pid,
        serial_number=serial_number,
        description="UsbMonitor",
        hwid=f"USB VID:PID={vid!s}:{pid!s}",
        location="1-2",
    )


def test_auto_detection_requires_vid_pid_and_exact_serial() -> None:
    exact = port("COM7")
    detector = DeviceDetector(
        lambda: [
            port("COM1", vid=0x9999),
            port("COM2", serial_number=None),
            exact,
        ]
    )
    selected = detector.select()
    assert selected.device == "COM7"
    assert is_exact_target(exact)


@pytest.mark.parametrize(
    "candidate",
    [
        port("COM3", vid=0x9999),
        port("COM3", pid=0x9999),
        port("COM3", serial_number="USB35INCHIPS"),
        port("COM3", serial_number=None),
    ],
)
def test_manual_port_never_bypasses_identity(candidate: SimpleNamespace) -> None:
    detector = DeviceDetector(lambda: [candidate])
    with pytest.raises(DeviceIdentityMismatch):
        detector.select("COM3")


def test_manual_port_is_case_insensitive_but_still_exact_identity() -> None:
    detector = DeviceDetector(lambda: [port("COM12")])
    assert detector.select("com12").device == "COM12"


def test_missing_manual_port_is_not_substituted_with_another_device() -> None:
    detector = DeviceDetector(lambda: [port("COM7")])
    with pytest.raises(DeviceNotFound):
        detector.select("COM3")


def test_multiple_exact_devices_require_explicit_selection() -> None:
    detector = DeviceDetector(lambda: [port("COM4"), port("COM8")])
    with pytest.raises(AmbiguousDevice):
        detector.select()
    assert detector.select("COM8").device == "COM8"


def test_revalidation_detects_identity_change() -> None:
    calls = 0

    def provider() -> list[SimpleNamespace]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return [port("COM6")]
        return [port("COM6", serial_number="OTHER")]

    detector = DeviceDetector(provider)
    selected = detector.select()
    with pytest.raises(DeviceIdentityMismatch):
        detector.revalidate(selected)
