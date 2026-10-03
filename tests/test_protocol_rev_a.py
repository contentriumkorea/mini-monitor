# SPDX-License-Identifier: GPL-3.0-or-later
# Golden command vectors were audited against turing-smart-screen-python commit
# 262a28a3ab615f2047c0bf44afc482cc341c465c.

from __future__ import annotations

import pytest

from ai_mini_monitor.transport.protocol_rev_a import (
    ALLOWED_COMMAND_BYTES,
    DEFAULT_BRIGHTNESS_PERCENT,
    DISPLAY_BITMAP,
    MAX_BRIGHTNESS_PERCENT,
    MIN_BRIGHTNESS_PERCENT,
    SCREEN_ON,
    SET_BRIGHTNESS,
    SET_ORIENTATION,
    Orientation,
    ProtocolValidationError,
    build_bitmap_command,
    build_bitmap_transfer,
    build_brightness_command,
    build_orientation_command,
    build_screen_on_command,
    oriented_dimensions,
    rgb888_to_rgb565le,
)


def test_allowlist_contains_only_audited_startup_and_render_commands() -> None:
    assert ALLOWED_COMMAND_BYTES == {0x6D, 0x6E, 0x79, 0xC5}
    assert SCREEN_ON == 0x6D
    assert SET_BRIGHTNESS == 0x6E
    assert SET_ORIENTATION == 0x79
    assert DISPLAY_BITMAP == 0xC5


def test_screen_on_matches_upstream_golden_vector() -> None:
    assert build_screen_on_command().hex() == "00000000006d"


def test_brightness_25_percent_matches_safety_cap_golden_vector() -> None:
    assert DEFAULT_BRIGHTNESS_PERCENT == 25
    assert build_brightness_command().hex() == "2fc00000006e"
    assert build_brightness_command(25).hex() == "2fc00000006e"


def test_brightness_verified_boundaries_keep_the_existing_packed_pattern() -> None:
    assert MIN_BRIGHTNESS_PERCENT == 1
    assert MAX_BRIGHTNESS_PERCENT == 50
    assert build_brightness_command(1).hex() == "3f000000006e"
    assert build_brightness_command(50).hex() == "1fc00000006e"


@pytest.mark.parametrize(
    "value",
    [True, False, None, 1.0, 50.0, "25", b"25", 0, 51, -1],
)
def test_brightness_rejects_unverified_values(value) -> None:
    with pytest.raises(ProtocolValidationError):
        build_brightness_command(value)


def test_landscape_orientation_matches_upstream_golden_vector() -> None:
    assert build_orientation_command(Orientation.LANDSCAPE).hex() == (
        "0000000000796601e001400000000000"
    )


@pytest.mark.parametrize(
    ("orientation", "dimensions", "vector"),
    [
        (
            Orientation.PORTRAIT,
            (320, 480),
            "00000000007964014001e00000000000",
        ),
        (
            Orientation.REVERSE_PORTRAIT,
            (320, 480),
            "00000000007965014001e00000000000",
        ),
        (
            Orientation.LANDSCAPE,
            (480, 320),
            "0000000000796601e001400000000000",
        ),
        (
            Orientation.REVERSE_LANDSCAPE,
            (480, 320),
            "0000000000796701e001400000000000",
        ),
    ],
)
def test_all_orientation_commands_carry_exact_logical_dimensions(
    orientation: Orientation,
    dimensions: tuple[int, int],
    vector: str,
) -> None:
    assert oriented_dimensions(orientation) == dimensions
    assert build_orientation_command(orientation).hex() == vector


def test_full_and_partial_bitmap_headers_match_upstream_golden_vectors() -> None:
    assert build_bitmap_command(0, 0, 480, 320).hex() == "0000077d3fc5"
    assert build_bitmap_command(10, 20, 100, 200).hex() == "028141b4dbc5"


def test_rgb888_is_serialized_as_little_endian_rgb565() -> None:
    rgb = bytes(
        (
            255,
            0,
            0,
            0,
            255,
            0,
            0,
            0,
            255,
            255,
            255,
            255,
        )
    )
    assert rgb888_to_rgb565le(rgb, 4, 1).hex() == "00f8e0071f00ffff"


@pytest.mark.parametrize(
    ("x", "y", "width", "height"),
    [
        (-1, 0, 1, 1),
        (0, -1, 1, 1),
        (0, 0, 0, 1),
        (0, 0, 1, 0),
        (479, 0, 2, 1),
        (0, 319, 1, 2),
        (480, 0, 1, 1),
        (0, 320, 1, 1),
    ],
)
def test_bitmap_bounds_are_rejected_before_io(
    x: int, y: int, width: int, height: int
) -> None:
    with pytest.raises(ProtocolValidationError):
        build_bitmap_command(x, y, width, height)


def test_payload_lengths_are_exact() -> None:
    with pytest.raises(ProtocolValidationError, match="payload length"):
        build_bitmap_transfer(0, 0, 2, 2, b"\x00" * 7)
    with pytest.raises(ProtocolValidationError, match="RGB888 data length"):
        rgb888_to_rgb565le(b"\x00" * 11, 2, 2)


def test_transfer_keeps_command_and_payload_ordered_and_chunked() -> None:
    transfer = build_bitmap_transfer(0, 0, 2, 2, b"\x00" * 8)
    writes = list(transfer.iter_writes(chunk_size=3))
    assert writes[0][-1] == DISPLAY_BITMAP
    assert writes[1:] == [b"\x00" * 3, b"\x00" * 3, b"\x00" * 2]
