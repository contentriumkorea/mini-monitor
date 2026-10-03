# SPDX-License-Identifier: GPL-3.0-or-later
#
# Portions of this file are derived from turing-smart-screen-python:
# https://github.com/mathoudebine/turing-smart-screen-python
# Upstream commit: 262a28a3ab615f2047c0bf44afc482cc341c465c
# Copyright (C) 2021 Matthieu Houdebine (mathoudebine) and contributors.
#
# Modified 2026-08-10 by AI Mini Monitor contributors: reduced the protocol
# surface to the verified rev-A screen-on (0x6D), brightness (0x6E),
# orientation (0x79), and bitmap (0xC5) commands, and added strict bounds,
# payload-length, and pixel-format validation.

"""Minimal, allowlisted protocol for the Turing/TURZX rev-A 3.5-inch LCD.

The target is the 320x480 ``USB35INCHIPSV2`` serial display used in a
480x320 landscape orientation.  This module deliberately has no generic
``send_command`` API: callers can only construct the four audited commands.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Final, Iterator


TARGET_USB_VID: Final[int] = 0x1A86
TARGET_USB_PID: Final[int] = 0x5722
TARGET_SERIAL_NUMBER: Final[str] = "USB35INCHIPSV2"

NATIVE_WIDTH: Final[int] = 320
NATIVE_HEIGHT: Final[int] = 480
LANDSCAPE_WIDTH: Final[int] = 480
LANDSCAPE_HEIGHT: Final[int] = 320

SCREEN_ON: Final[int] = 0x6D
SET_BRIGHTNESS: Final[int] = 0x6E
SET_ORIENTATION: Final[int] = 0x79
DISPLAY_BITMAP: Final[int] = 0xC5
ALLOWED_COMMAND_BYTES: Final[frozenset[int]] = frozenset(
    {SCREEN_ON, SET_BRIGHTNESS, SET_ORIENTATION, DISPLAY_BITMAP}
)
MIN_BRIGHTNESS_PERCENT: Final[int] = 1
MAX_BRIGHTNESS_PERCENT: Final[int] = 50
DEFAULT_BRIGHTNESS_PERCENT: Final[int] = 25

# Upstream sends image bytes in chunks of oriented display width * 8.  At
# 480 pixels this is four complete RGB565 rows per write.
DEFAULT_PAYLOAD_CHUNK_SIZE: Final[int] = LANDSCAPE_WIDTH * 8


class ProtocolValidationError(ValueError):
    """Raised before any I/O when a command or framebuffer is unsafe."""


class Orientation(IntEnum):
    """Values used by the audited rev-A orientation command."""

    PORTRAIT = 0
    REVERSE_PORTRAIT = 1
    LANDSCAPE = 2
    REVERSE_LANDSCAPE = 3


def build_screen_on_command() -> bytes:
    """Build the audited six-byte ``SCREEN_ON`` startup command."""

    return bytes((0, 0, 0, 0, 0, SCREEN_ON))


def build_brightness_command(percent: int = DEFAULT_BRIGHTNESS_PERCENT) -> bytes:
    """Build the safety-capped brightness command used by this startup path.

    The target protocol uses an inverted 8-bit value packed into the first
    ten-bit coordinate-style field. Only the verified 1..50 percent operating
    range is exposed; there is deliberately no raw-value command API.
    """

    if isinstance(percent, bool) or not isinstance(percent, int):
        raise ProtocolValidationError("brightness percent must be an integer")
    if not MIN_BRIGHTNESS_PERCENT <= percent <= MAX_BRIGHTNESS_PERCENT:
        raise ProtocolValidationError(
            "brightness percent must be within "
            f"{MIN_BRIGHTNESS_PERCENT}..{MAX_BRIGHTNESS_PERCENT}"
        )
    raw = int(255 - (percent / 100 * 255))
    command = bytearray(6)
    command[0] = raw >> 2
    command[1] = (raw & 0x03) << 6
    command[5] = SET_BRIGHTNESS
    return bytes(command)


def _coerce_orientation(value: Orientation | int) -> Orientation:
    if isinstance(value, bool):
        raise ProtocolValidationError("orientation must not be a boolean")
    try:
        return Orientation(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolValidationError(f"unsupported orientation: {value!r}") from exc


def oriented_dimensions(
    orientation: Orientation | int,
    *,
    native_width: int = NATIVE_WIDTH,
    native_height: int = NATIVE_HEIGHT,
) -> tuple[int, int]:
    """Return width/height after applying the device orientation."""

    selected = _coerce_orientation(orientation)
    if not 1 <= native_width <= 1024 or not 1 <= native_height <= 1024:
        raise ProtocolValidationError("native dimensions must be within 1..1024")
    if selected in (Orientation.PORTRAIT, Orientation.REVERSE_PORTRAIT):
        return native_width, native_height
    return native_height, native_width


def build_orientation_command(
    orientation: Orientation | int = Orientation.LANDSCAPE,
    *,
    native_width: int = NATIVE_WIDTH,
    native_height: int = NATIVE_HEIGHT,
) -> bytes:
    """Build the audited 16-byte ``SET_ORIENTATION`` command."""

    selected = _coerce_orientation(orientation)
    width, height = oriented_dimensions(
        selected, native_width=native_width, native_height=native_height
    )
    command = bytearray(16)
    command[5] = SET_ORIENTATION
    command[6] = selected.value + 100
    command[7] = width >> 8
    command[8] = width & 0xFF
    command[9] = height >> 8
    command[10] = height & 0xFF
    return bytes(command)


def _validate_rectangle(
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    display_width: int,
    display_height: int,
) -> tuple[int, int]:
    values = (x, y, width, height, display_width, display_height)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise ProtocolValidationError("rectangle and display dimensions must be integers")
    if not 1 <= display_width <= 1024 or not 1 <= display_height <= 1024:
        raise ProtocolValidationError("display dimensions must be within 1..1024")
    if x < 0 or y < 0:
        raise ProtocolValidationError("rectangle coordinates must be non-negative")
    if width <= 0 or height <= 0:
        raise ProtocolValidationError("rectangle dimensions must be positive")
    if x + width > display_width or y + height > display_height:
        raise ProtocolValidationError(
            f"rectangle ({x}, {y}, {width}, {height}) exceeds "
            f"{display_width}x{display_height} display"
        )
    x_end = x + width - 1
    y_end = y + height - 1
    if x_end > 0x3FF or y_end > 0x3FF:
        raise ProtocolValidationError("rev-A coordinates are limited to 10 bits")
    return x_end, y_end


def build_bitmap_command(
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    display_width: int = LANDSCAPE_WIDTH,
    display_height: int = LANDSCAPE_HEIGHT,
) -> bytes:
    """Build the six-byte bitmap-window command for an inclusive rectangle."""

    x_end, y_end = _validate_rectangle(
        x,
        y,
        width,
        height,
        display_width=display_width,
        display_height=display_height,
    )
    command = bytearray(6)
    command[0] = x >> 2
    command[1] = ((x & 0x03) << 6) | (y >> 4)
    command[2] = ((y & 0x0F) << 4) | (x_end >> 6)
    command[3] = ((x_end & 0x3F) << 2) | (y_end >> 8)
    command[4] = y_end & 0xFF
    command[5] = DISPLAY_BITMAP
    return bytes(command)


def _as_bytes(data: bytes | bytearray | memoryview, *, label: str) -> bytes:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ProtocolValidationError(f"{label} must be bytes-like")
    return bytes(data)


def validate_rgb565le_payload(
    payload: bytes | bytearray | memoryview,
    width: int,
    height: int,
) -> bytes:
    """Return an immutable payload after checking its exact pixel count."""

    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
    ):
        raise ProtocolValidationError("payload dimensions must be positive integers")
    immutable = _as_bytes(payload, label="RGB565LE payload")
    expected = width * height * 2
    if len(immutable) != expected:
        raise ProtocolValidationError(
            f"RGB565LE payload length must be {expected}, got {len(immutable)}"
        )
    return immutable


def rgb888_to_rgb565le(
    rgb: bytes | bytearray | memoryview,
    width: int,
    height: int,
) -> bytes:
    """Convert tightly packed row-major RGB888 pixels to little-endian RGB565."""

    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
    ):
        raise ProtocolValidationError("RGB dimensions must be positive integers")
    source = _as_bytes(rgb, label="RGB888 data")
    expected = width * height * 3
    if len(source) != expected:
        raise ProtocolValidationError(
            f"RGB888 data length must be {expected}, got {len(source)}"
        )

    output = bytearray(width * height * 2)
    output_offset = 0
    for source_offset in range(0, len(source), 3):
        red = source[source_offset]
        green = source[source_offset + 1]
        blue = source[source_offset + 2]
        pixel = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
        output[output_offset] = pixel & 0xFF
        output[output_offset + 1] = pixel >> 8
        output_offset += 2
    return bytes(output)


@dataclass(frozen=True, slots=True)
class BitmapTransfer:
    """A validated command/payload pair that must not be interleaved."""

    command: bytes
    payload: bytes

    def iter_writes(
        self, chunk_size: int = DEFAULT_PAYLOAD_CHUNK_SIZE
    ) -> Iterator[bytes]:
        if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
            raise ProtocolValidationError("chunk size must be a positive integer")
        yield self.command
        for offset in range(0, len(self.payload), chunk_size):
            yield self.payload[offset : offset + chunk_size]


def build_bitmap_transfer(
    x: int,
    y: int,
    width: int,
    height: int,
    rgb565le: bytes | bytearray | memoryview,
    *,
    display_width: int = LANDSCAPE_WIDTH,
    display_height: int = LANDSCAPE_HEIGHT,
) -> BitmapTransfer:
    """Validate and bind a bitmap window to its exact RGB565LE payload."""

    command = build_bitmap_command(
        x,
        y,
        width,
        height,
        display_width=display_width,
        display_height=display_height,
    )
    payload = validate_rgb565le_payload(rgb565le, width, height)
    return BitmapTransfer(command=command, payload=payload)


def encode_rgb888_transfer(
    x: int,
    y: int,
    width: int,
    height: int,
    rgb: bytes | bytearray | memoryview,
    *,
    display_width: int = LANDSCAPE_WIDTH,
    display_height: int = LANDSCAPE_HEIGHT,
) -> BitmapTransfer:
    """Convert RGB888 pixels and construct a validated bitmap transfer."""

    payload = rgb888_to_rgb565le(rgb, width, height)
    return build_bitmap_transfer(
        x,
        y,
        width,
        height,
        payload,
        display_width=display_width,
        display_height=display_height,
    )
