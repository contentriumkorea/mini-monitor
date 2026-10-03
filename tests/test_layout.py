# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from itertools import combinations

from ai_mini_monitor.rendering.layout import (
    AI,
    CARD_GAP,
    CARD_RECTS,
    CONNECTION,
    CPU,
    GPU,
    MEMORY,
    OUTER_MARGIN,
    PORTRAIT_LAYOUT,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    Rect,
    normalized_area_ratios,
    validate_layout,
)


def test_exact_480x320_geometry() -> None:
    assert (SCREEN_WIDTH, SCREEN_HEIGHT) == (480, 320)
    assert OUTER_MARGIN == 8
    assert CARD_GAP == 8
    assert CARD_RECTS == {
        "connection": Rect(8, 8, 464, 20),
        "cpu": Rect(8, 36, 228, 134),
        "gpu": Rect(244, 36, 228, 134),
        "memory": Rect(8, 178, 228, 134),
        "ai": Rect(244, 178, 228, 134),
    }
    assert CONNECTION.bottom + CARD_GAP == CPU.y
    assert CPU.right + CARD_GAP == GPU.x
    assert CPU.bottom + CARD_GAP == MEMORY.y
    assert MEMORY.right + CARD_GAP == AI.x
    assert CONNECTION.right == SCREEN_WIDTH - OUTER_MARGIN
    assert MEMORY.bottom == AI.bottom == SCREEN_HEIGHT - OUTER_MARGIN


def test_cards_never_overlap_or_leave_the_frame() -> None:
    assert validate_layout() == []
    for rect in CARD_RECTS.values():
        assert 0 <= rect.x < rect.right <= SCREEN_WIDTH
        assert 0 <= rect.y < rect.bottom <= SCREEN_HEIGHT
    for (_, first), (_, second) in combinations(CARD_RECTS.items(), 2):
        assert not first.intersects(second)


def test_area_ratios_preserve_the_integer_pixel_realization() -> None:
    ratios = normalized_area_ratios()
    assert ratios == {
        "connection": CONNECTION.area / CPU.area,
        "cpu": 1.0,
        "gpu": GPU.area / CPU.area,
        "memory": MEMORY.area / CPU.area,
        "ai": AI.area / CPU.area,
    }
    assert ratios["gpu"] == 1.0
    assert ratios["memory"] == ratios["ai"] == 1.0
    assert ratios["connection"] == (464 * 20) / (228 * 134)


def test_half_open_rectangles_and_clamping_are_stable() -> None:
    assert not Rect(0, 0, 10, 10).intersects(Rect(10, 0, 5, 5))
    assert Rect(0, 0, 10, 10).intersects(Rect(9, 9, 2, 2))
    assert Rect(-5, -7, 20, 20).clamp() == Rect(0, 0, 15, 13)
    assert Rect(475, 315, 20, 20).clamp() == Rect(475, 315, 5, 5)


def test_portrait_layout_is_native_size_horizontal_text_geometry() -> None:
    assert PORTRAIT_LAYOUT.size == (320, 480)
    assert PORTRAIT_LAYOUT.card_rects == {
        "connection": Rect(8, 8, 304, 20),
        "cpu": Rect(8, 36, 304, 103),
        "gpu": Rect(8, 147, 304, 103),
        "memory": Rect(8, 258, 304, 103),
        "ai": Rect(8, 369, 304, 103),
    }
    assert validate_layout(PORTRAIT_LAYOUT) == []
    cards = tuple(PORTRAIT_LAYOUT.card_rects.values())
    for rect in cards:
        assert 0 <= rect.x < rect.right <= 320
        assert 0 <= rect.y < rect.bottom <= 480
    for first, second in combinations(cards, 2):
        assert not first.intersects(second)
