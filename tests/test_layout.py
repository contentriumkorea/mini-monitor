# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from itertools import combinations

from ai_mini_monitor.rendering.layout import (
    AI,
    VRAM,
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
        "cpu": Rect(8, 36, 228, 88),
        "memory": Rect(244, 36, 228, 88),
        "gpu": Rect(8, 132, 228, 88),
        "vram": Rect(244, 132, 228, 88),
        "ai": Rect(8, 228, 464, 84),
    }
    assert CONNECTION.bottom + CARD_GAP == CPU.y
    assert CPU.right + CARD_GAP == MEMORY.x
    assert CPU.bottom + CARD_GAP == GPU.y
    assert GPU.right + CARD_GAP == VRAM.x
    assert GPU.bottom + CARD_GAP == AI.y
    assert CONNECTION.right == SCREEN_WIDTH - OUTER_MARGIN
    assert AI.bottom == SCREEN_HEIGHT - OUTER_MARGIN


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
        "vram": VRAM.area / CPU.area,
        "ai": AI.area / CPU.area,
    }
    assert ratios["gpu"] == 1.0
    assert ratios["memory"] == ratios["vram"] == 1.0
    assert ratios["ai"] > 1.0
    assert ratios["connection"] == (464 * 20) / (228 * 88)


def test_half_open_rectangles_and_clamping_are_stable() -> None:
    assert not Rect(0, 0, 10, 10).intersects(Rect(10, 0, 5, 5))
    assert Rect(0, 0, 10, 10).intersects(Rect(9, 9, 2, 2))
    assert Rect(-5, -7, 20, 20).clamp() == Rect(0, 0, 15, 13)
    assert Rect(475, 315, 20, 20).clamp() == Rect(475, 315, 5, 5)


def test_portrait_layout_is_native_size_horizontal_text_geometry() -> None:
    assert PORTRAIT_LAYOUT.size == (320, 480)
    assert PORTRAIT_LAYOUT.card_rects == {
        "connection": Rect(8, 8, 304, 20),
        "cpu": Rect(8, 36, 304, 81),
        "memory": Rect(8, 125, 304, 81),
        "gpu": Rect(8, 214, 304, 81),
        "vram": Rect(8, 303, 304, 81),
        "ai": Rect(8, 392, 304, 80),
    }
    assert validate_layout(PORTRAIT_LAYOUT) == []
    cards = tuple(PORTRAIT_LAYOUT.card_rects.values())
    for rect in cards:
        assert 0 <= rect.x < rect.right <= 320
        assert 0 <= rect.y < rect.bottom <= 480
    for first, second in combinations(cards, 2):
        assert not first.intersects(second)


def test_five_card_reading_order_and_full_width_codex() -> None:
    from ai_mini_monitor.rendering.layout import LANDSCAPE_LAYOUT

    landscape = LANDSCAPE_LAYOUT
    assert set(landscape.card_rects) == {"connection", "cpu", "memory", "gpu", "vram", "ai"}
    assert landscape.cpu.y == landscape.memory.y
    assert landscape.gpu.y == landscape.vram.y > landscape.cpu.y
    assert landscape.ai.y > landscape.gpu.y
    assert landscape.ai.x == landscape.cpu.x
    assert landscape.ai.width == landscape.connection.width
    assert validate_layout(landscape) == []

    portrait = PORTRAIT_LAYOUT
    assert [portrait.card_rects[key].y for key in ("cpu", "memory", "gpu", "vram", "ai")] == sorted(
        portrait.card_rects[key].y for key in ("cpu", "memory", "gpu", "vram", "ai")
    )
    assert validate_layout(portrait) == []
