# SPDX-License-Identifier: GPL-3.0-or-later
#
# Device identifiers were audited against turing-smart-screen-python commit
# 262a28a3ab615f2047c0bf44afc482cc341c465c.
# Copyright (C) 2021 Matthieu Houdebine (mathoudebine) and contributors.
# Modified 2026-08-10 by AI Mini Monitor contributors: upstream's permissive
# OR/manual-port matching was replaced with mandatory VID+PID+serial matching.

"""Strict, read-only discovery for the one supported USB serial display."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from serial.tools.list_ports import comports

from .protocol_rev_a import (
    TARGET_SERIAL_NUMBER,
    TARGET_USB_PID,
    TARGET_USB_VID,
)


class DeviceSelectionError(RuntimeError):
    """Base class for safe device-selection failures."""


class DeviceNotFound(DeviceSelectionError):
    pass


class DeviceIdentityMismatch(DeviceSelectionError):
    pass


class AmbiguousDevice(DeviceSelectionError):
    pass


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    device: str
    vid: int | None
    pid: int | None
    serial_number: str | None
    description: str | None = None
    hwid: str | None = None
    location: str | None = None

    @classmethod
    def from_port(cls, port: Any) -> "DeviceInfo":
        device = getattr(port, "device", None)
        if not isinstance(device, str) or not device.strip():
            raise DeviceIdentityMismatch("serial port has no usable device name")
        return cls(
            device=device,
            vid=getattr(port, "vid", None),
            pid=getattr(port, "pid", None),
            serial_number=getattr(port, "serial_number", None),
            description=getattr(port, "description", None),
            hwid=getattr(port, "hwid", None),
            location=getattr(port, "location", None),
        )

    @property
    def is_exact_target(self) -> bool:
        return (
            self.vid == TARGET_USB_VID
            and self.pid == TARGET_USB_PID
            and self.serial_number == TARGET_SERIAL_NUMBER
        )

    def same_identity(self, other: "DeviceInfo") -> bool:
        return (
            self.device.casefold() == other.device.casefold()
            and self.vid == other.vid
            and self.pid == other.pid
            and self.serial_number == other.serial_number
        )


def is_exact_target(port: Any) -> bool:
    """Return false, rather than guessing, when any identity field is absent."""

    try:
        return DeviceInfo.from_port(port).is_exact_target
    except DeviceIdentityMismatch:
        return False


PortProvider = Callable[[], Iterable[Any]]


class DeviceDetector:
    """Enumerate and select only VID+PID+serial exact matches.

    Passing a manual COM name narrows the search; it never bypasses identity
    validation.
    """

    def __init__(self, port_provider: PortProvider = comports) -> None:
        self._port_provider = port_provider

    def list_ports(self) -> tuple[DeviceInfo, ...]:
        found: list[DeviceInfo] = []
        for port in self._port_provider():
            try:
                found.append(DeviceInfo.from_port(port))
            except DeviceIdentityMismatch:
                continue
        return tuple(found)

    def target_devices(self) -> tuple[DeviceInfo, ...]:
        return tuple(info for info in self.list_ports() if info.is_exact_target)

    def select(self, manual_port: str | None = None) -> DeviceInfo:
        ports = self.list_ports()
        if manual_port is not None:
            if not isinstance(manual_port, str) or not manual_port.strip():
                raise DeviceNotFound("manual COM port is empty")
            matches = tuple(
                info
                for info in ports
                if info.device.casefold() == manual_port.casefold()
            )
            if not matches:
                raise DeviceNotFound(f"manual port {manual_port!r} is not present")
            if len(matches) > 1:
                raise AmbiguousDevice(
                    f"manual port {manual_port!r} appears more than once"
                )
            selected = matches[0]
            if not selected.is_exact_target:
                raise DeviceIdentityMismatch(
                    f"refusing {selected.device}: expected VID_1A86&PID_5722 "
                    f"and serial {TARGET_SERIAL_NUMBER!r}; got "
                    f"VID={selected.vid!r}, PID={selected.pid!r}, "
                    f"serial={selected.serial_number!r}"
                )
            return selected

        targets = tuple(info for info in ports if info.is_exact_target)
        if not targets:
            raise DeviceNotFound("USB35INCHIPSV2 display is not present")
        if len(targets) > 1:
            names = ", ".join(info.device for info in targets)
            raise AmbiguousDevice(
                f"multiple exact USB35INCHIPSV2 displays found: {names}; "
                "select one explicitly"
            )
        return targets[0]

    def revalidate(self, expected: DeviceInfo) -> DeviceInfo:
        """Re-enumerate a chosen COM port immediately before the first byte."""

        current = self.select(expected.device)
        if not expected.same_identity(current):
            raise DeviceIdentityMismatch(
                f"identity for {expected.device} changed before transmission"
            )
        return current
