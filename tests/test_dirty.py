# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from PIL import Image
import pytest

from ai_mini_monitor.rendering.dirty import (
    calculate_dirty_rectangles,
    merge_nearby,
    rectangle_distance,
    should_merge,
    union_rect,
)
from ai_mini_monitor.rendering.layout import PORTRAIT_LAYOUT, Rect


def frame() -> Image.Image:
    return Image.new("RGB", (480, 320), (5, 7, 13))


def test_union_and_distance_use_half_open_geometry() -> None:
    first = Rect(10, 10, 10, 10)
    adjacent = Rect(21, 10, 10, 10)
    assert rectangle_distance(first, adjacent) == (1, 0)
    assert union_rect(first, adjacent) == Rect(10, 10, 21, 10)


def test_near_collinear_rectangles_merge_with_bounded_overdraw() -> None:
    first = Rect(10, 10, 10, 10)
    second = Rect(21, 10, 10, 10)
    assert should_merge(first, second, proximity=3)
    assert merge_nearby([second, first], proximity=3) == [Rect(10, 10, 21, 10)]


def test_diagonal_or_distant_rectangles_do_not_merge() -> None:
    first = Rect(10, 10, 10, 10)
    diagonal = Rect(21, 21, 10, 10)
    distant = Rect(30, 10, 10, 10)
    assert not should_merge(first, diagonal, proximity=3)
    assert not should_merge(first, distant, proximity=3)
    assert merge_nearby([diagonal, first]) == [first, diagonal]


def test_dirty_pixels_are_padded_and_limited_to_changed_card() -> None:
    before = frame()
    after = before.copy()
    after.putpixel((20, 50), (255, 255, 255))
    assert calculate_dirty_rectangles(before, after) == [Rect(19, 49, 3, 3)]


def test_distant_changes_in_one_card_remain_separate() -> None:
    before = frame()
    after = before.copy()
    after.putpixel((10, 40), (255, 255, 255))
    after.putpixel((230, 125), (255, 255, 255))

    assert calculate_dirty_rectangles(before, after) == [
        Rect(9, 39, 3, 3),
        Rect(229, 124, 3, 3),
    ]


def test_card_edge_padding_is_clipped_to_the_card() -> None:
    before = frame()
    after = before.copy()
    after.putpixel((8, 38), (255, 255, 255))

    assert calculate_dirty_rectangles(before, after) == [Rect(8, 38, 2, 2)]


def test_unchanged_and_outside_card_pixels_do_not_generate_regions() -> None:
    before = frame()
    assert calculate_dirty_rectangles(before, before.copy()) == []
    after = before.copy()
    after.putpixel((0, 0), (255, 255, 255))
    assert calculate_dirty_rectangles(before, after) == []


def test_full_refresh_and_frame_validation_paths() -> None:
    current = frame()
    assert calculate_dirty_rectangles(None, current) == [Rect(0, 0, 480, 320)]
    assert calculate_dirty_rectangles(Image.new("RGB", (1, 1)), current) == [Rect(0, 0, 480, 320)]
    with pytest.raises(ValueError, match="480x320"):
        calculate_dirty_rectangles(None, Image.new("RGB", (320, 480)))


def test_portrait_dirty_pixel_near_bottom_is_not_clipped_by_landscape_bounds() -> None:
    before = Image.new("RGB", PORTRAIT_LAYOUT.size, (5, 7, 13))
    after = before.copy()
    point = (PORTRAIT_LAYOUT.ai.x + 5, PORTRAIT_LAYOUT.ai.bottom - 2)
    after.putpixel(point, (255, 255, 255))

    rectangles = calculate_dirty_rectangles(
        before,
        after,
        layout=PORTRAIT_LAYOUT,
    )

    assert rectangles == [Rect(point[0] - 1, point[1] - 1, 3, 3)]
    assert rectangles[0].bottom == PORTRAIT_LAYOUT.ai.bottom
