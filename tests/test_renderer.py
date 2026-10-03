# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from itertools import combinations

import pytest
from PIL import Image, ImageChops, ImageColor

from ai_mini_monitor.models import (
    AIData,
    AIProviderKind,
    ConnectionData,
    ConnectionStatus,
    DisplaySnapshot,
    Metric,
    SyncStatus,
)
from ai_mini_monitor.rendering.fonts import digits_are_tabular, metric_mono, mono
from ai_mini_monitor.rendering.layout import (
    CPU,
    GPU,
    LANDSCAPE_LAYOUT,
    MEMORY,
    PORTRAIT_LAYOUT,
    DashboardLayout,
    Rect,
)
from ai_mini_monitor.rendering.renderer import (
    CPU_GAUGE,
    GPU_GAUGE,
    MEMORY_GAUGE,
    DashboardRenderer,
    OverlayLayers,
    get_overlay_layers,
)


def snapshot(value: int | None, *, connection: ConnectionStatus = ConnectionStatus.ONLINE) -> DisplaySnapshot:
    history = (0.0, 9.0, 10.0, 99.0, 100.0, None)
    return DisplaySnapshot(
        timestamp=datetime(2026, 8, 10, 3, 0, tzinfo=timezone.utc),
        cpu_model="Intel Core i7-14700K",
        gpu_model="NVIDIA GeForce RTX 5070 Ti",
        cpu_percent=Metric(value, "%"),
        cpu_temperature=Metric(89, "C"),
        gpu_percent=Metric(value, "%"),
        gpu_temperature=Metric(90, "C"),
        memory_percent=Metric(value, "%"),
        memory_used_gib=Metric(25.5, "GiB"),
        memory_total_gib=Metric(128.0, "GiB"),
        memory_available_gib=Metric(102.5, "GiB"),
        cpu_history=history,
        gpu_history=history,
        memory_history=history,
        connection=ConnectionData(connection, "COM123", "verified device"),
        ai=AIData(
            provider=AIProviderKind.OPENAI_API,
            title="OPENAI API",
            status=SyncStatus.OK,
            primary_value="$999.99",
            primary_label="TODAY COST",
            fields=(("REQUESTS", "1.0M"), ("INPUT", "999.9K"), ("OUTPUT", "100"), ("CACHED", "0")),
            last_sync=datetime(2026, 8, 10, 3, 0, tzinfo=timezone.utc),
            budget_ratio=1.0,
        ),
    )


@pytest.mark.parametrize("value", [0, 9, 10, 99, 100])
def test_boundary_values_render_at_exact_size_without_clipping(value: int) -> None:
    renderer = DashboardRenderer()
    image = renderer.render(snapshot(value))
    assert image.size == (480, 320)
    assert image.mode == "RGB"
    assert renderer.clipping_issues == ()
    assert renderer.last_placements["cpu_value"].text == f"{value}%"
    assert renderer.last_placements["gpu_value"].text == f"{value}%"
    assert renderer.last_placements["memory_value"].text == f"{value}%"
    for key in ("cpu_value", "gpu_value", "memory_value"):
        group = renderer.last_placements[key]
        digits = renderer.last_placements[f"{key}_digits"]
        suffix = renderer.last_placements[f"{key}_suffix"]
        assert group.drawn is False
        assert digits.text == str(value)
        assert suffix.text == "%"
        assert suffix.font_size == round(digits.font_size * 0.5)
        assert suffix.baseline_y is not None
        assert digits.baseline_y is not None
        assert abs(suffix.baseline_y - digits.baseline_y) <= 1
        assert digits.bbox[2] <= suffix.bbox[0]
        digit_height = digits.bbox[3] - digits.bbox[1]
        suffix_height = suffix.bbox[3] - suffix.bbox[1]
        assert 0.45 <= suffix_height / digit_height <= 0.60
        assert suffix.bbox[1] > digits.bbox[1]
        assert suffix.bbox[3] == digits.bbox[3]
        assert group.bbox == _bbox_union(digits.bbox, suffix.bbox)
        assert _bbox_inside(group.bbox, group.clip)


@pytest.mark.parametrize("layout", [LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT])
def test_overlay_layers_remove_canvas_grid_and_keep_card_content_opaque(
    layout: DashboardLayout,
) -> None:
    renderer = DashboardRenderer(layout=layout)
    image = renderer.render(snapshot(82))
    layers = get_overlay_layers(image)

    assert layers.size == layout.size
    assert layers.background.mode == "RGB"
    assert layers.foreground.mode == "RGBA"
    assert layers.surface_mask.mode == "L"
    assert renderer.clipping_issues == ()

    foreground_alpha = layers.foreground.getchannel("A")
    clear_background_xy = (0, 0)
    assert foreground_alpha.getpixel(clear_background_xy) == 0
    assert layers.surface_mask.getpixel(clear_background_xy) == 0
    assert layers.surface_mask.getpixel((16, 0)) == 0
    assert layers.surface_mask.getpixel((16, 32)) == 0
    card_surface_xy = next(
        (x, y)
        for y in range(layout.cpu.y, layout.cpu.bottom)
        for x in range(layout.cpu.x, layout.cpu.right)
        if (
            layers.surface_mask.getpixel((x, y)) == 255
            and foreground_alpha.getpixel((x, y)) == 0
        )
    )
    assert layers.surface_mask.getpixel(card_surface_xy) == 255

    for opacity in (0.35, 0.85):
        alpha = round(255 * opacity)
        translucent_background = layers.background.convert("RGBA")
        translucent_background.putalpha(
            layers.surface_mask.point(
                tuple(round(level * opacity) for level in range(256))
            )
        )
        overlay = Image.alpha_composite(translucent_background, layers.foreground)

        assert overlay.getpixel(clear_background_xy)[3] == 0
        assert overlay.getpixel(card_surface_xy)[3] == alpha
        for placement_key in (
            "cpu_label",
            "cpu_value_digits",
            "connection_status",
        ):
            placement = renderer.last_placements[placement_key]
            assert overlay.getchannel("A").crop(placement.bbox).getextrema()[1] == 255

        cpu_gauge = renderer.last_gauges["cpu"]
        assert cpu_gauge.fill is not None
        assert _alpha_max(overlay, cpu_gauge.track) == 255
        assert _alpha_max(overlay, cpu_gauge.fill) == 255
        assert _alpha_max(overlay, renderer.last_sparklines["cpu"]) == 255
        assert renderer.last_budget_bar is not None
        assert _alpha_max(overlay, renderer.last_budget_bar) == 255

    # FreeType edge coverage is retained in the transparent foreground rather
    # than being flattened into the adjustable background alpha.
    digit_bbox = renderer.last_placements["cpu_value_digits"].bbox
    digit_histogram = foreground_alpha.crop(digit_bbox).histogram()
    assert digit_histogram[255] > 0
    assert any(digit_histogram[value] > 0 for value in range(1, 255))


@pytest.mark.parametrize("layout", [LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT])
def test_overlay_layers_exactly_recompose_physical_rgb_and_survive_copy(
    layout: DashboardLayout,
) -> None:
    renderer = DashboardRenderer(layout=layout)
    image = renderer.render(snapshot(82))
    layers = get_overlay_layers(image)

    recomposed = Image.alpha_composite(
        layers.background.convert("RGBA"),
        layers.foreground,
    ).convert("RGB")
    assert image.mode == "RGB"
    assert image.size == layout.size
    assert ImageChops.difference(image, recomposed).getbbox() is None
    assert renderer.clipping_issues == ()

    copied_layers = get_overlay_layers(image.copy())
    mutable_copy = copied_layers.background
    original_pixel = layers.background.getpixel((0, 0))
    mutable_copy.putpixel((0, 0), (255, 0, 255))
    assert get_overlay_layers(image).background.getpixel((0, 0)) == original_pixel
    mutable_mask = copied_layers.surface_mask
    mutable_mask.putpixel((0, 0), 255)
    assert get_overlay_layers(image).surface_mask.getpixel((0, 0)) == 0
    with pytest.raises(ValueError, match="does not match image size"):
        get_overlay_layers(image.crop((0, 0, image.width // 2, image.height)))


def test_overlay_layers_require_a_matching_grayscale_surface_mask() -> None:
    background = Image.new("RGB", (20, 10), "black")
    foreground = Image.new("RGBA", (20, 10), (0, 0, 0, 0))

    with pytest.raises(ValueError, match="surface mask"):
        OverlayLayers(background, foreground, Image.new("RGB", (20, 10)))
    with pytest.raises(ValueError, match="surface mask"):
        OverlayLayers(background, foreground, Image.new("L", (10, 10)))


@pytest.mark.parametrize(
    ("layout", "expected_sha256"),
    [
        (LANDSCAPE_LAYOUT, "a88743bb68c6cad961c3f0e0f17a80a040516e842c545d850b6786394c448f55"),
        (PORTRAIT_LAYOUT, "05dcd9a44d50dbc2fd69c2a357f45a8aba9b0b689f42e8af07be902c0a32e091"),
    ],
)
def test_semantic_layers_do_not_change_default_physical_framebuffer(
    layout: DashboardLayout,
    expected_sha256: str,
) -> None:
    image = DashboardRenderer(layout=layout).render(DisplaySnapshot())

    assert image.mode == "RGB"
    assert image.size == layout.size
    assert sha256(image.tobytes()).hexdigest() == expected_sha256


def test_unavailable_percentages_render_without_clipping() -> None:
    renderer = DashboardRenderer()
    image = renderer.render(snapshot(None))
    assert image.size == (480, 320)
    assert renderer.clipping_issues == ()
    for key in ("cpu", "gpu", "memory"):
        assert renderer.last_placements[f"{key}_value"].text == "--"
        assert renderer.last_placements[f"{key}_value"].drawn is True
        assert f"{key}_value_suffix" not in renderer.last_placements
        assert renderer.last_placements[f"{key}_gauge_status"].text == "NO DATA"
        assert renderer.last_gauges[key].ratio is None
        assert renderer.last_gauges[key].fill is None


def test_numeric_anchor_positions_do_not_shift_when_digit_count_changes() -> None:
    renderer = DashboardRenderer()
    observed: dict[str, list[tuple[tuple[int, int], int]]] = {
        "cpu_value": [],
        "gpu_value": [],
        "memory_value": [],
    }
    for value in (0, 9, 10, 99, 100):
        renderer.render(snapshot(value))
        for key in observed:
            placement = renderer.last_placements[key]
            observed[key].append((placement.anchor_xy, placement.bbox[2]))

    assert {item[0] for item in observed["cpu_value"]} == {(CPU.x + 101, CPU.bottom - 15)}
    assert {item[0] for item in observed["gpu_value"]} == {(GPU.x + 101, GPU.bottom - 15)}
    assert {item[0] for item in observed["memory_value"]} == {(MEMORY.x + 112, MEMORY.y + 83)}
    for placements in observed.values():
        assert len({right_edge for _, right_edge in placements}) == 1

    for key in observed:
        renderer.render(snapshot(100))
        group = renderer.last_placements[key]
        suffix = renderer.last_placements[f"{key}_suffix"]
        assert suffix.bbox[2] == group.anchor_xy[0]


def test_monospace_display_font_uses_tabular_digits() -> None:
    for size in (8, 10, 11, 38, 40, 42, 44):
        font = mono(size)
        assert digits_are_tabular(font)
        assert font.getlength("0%") == pytest.approx(font.getlength("9%"))
        assert font.getlength("10%") == pytest.approx(font.getlength("99%"))


def test_large_numeric_font_is_explicitly_extra_bold_and_tabular() -> None:
    for size in (22, 26, 28, 34, 38, 40, 42, 44):
        bold = mono(size, "Bold")
        extra_bold = metric_mono(size)
        assert extra_bold.getname()[1] == "ExtraBold"
        assert digits_are_tabular(extra_bold)
        assert sum(extra_bold.getmask("100%")) > sum(bold.getmask("100%"))


@pytest.mark.parametrize("layout", [LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT])
def test_card_identity_lanes_are_large_bold_text_plus_distinct_geometry(
    layout: DashboardLayout,
) -> None:
    base = snapshot(82)
    renderer = DashboardRenderer(layout=layout)
    image = renderer.render(
        replace(base, ai=replace(base.ai, title="CODEX LIMITS", demo=False))
    )
    expected = {
        "cpu_label": (layout.cpu, "CPU", renderer.theme.cpu_accent),
        "gpu_label": (layout.gpu, "GPU", renderer.theme.gpu_accent),
        "memory_label": (layout.memory, "RAM", renderer.theme.memory_accent),
        "ai_title": (layout.ai, "CODEX LIMITS", renderer.theme.ai_accent),
    }

    assert len({accent for _card, _text, accent in expected.values()}) == 4
    for key, (card, text, accent) in expected.items():
        placement = renderer.last_placements[key]
        assert placement.text == text
        assert placement.font_size == 18
        assert placement.font_name[1] == "ExtraBold"
        assert _bbox_inside(placement.bbox, card)
        assert image.getpixel((card.x + 10, card.y + 14)) == ImageColor.getrgb(accent)
        if key != "ai_title" or layout is LANDSCAPE_LAYOUT:
            underline_y = min(card.bottom - 2, placement.bbox[3] + 2)
            assert image.getpixel((card.x + 19, underline_y)) == ImageColor.getrgb(accent)
        assert image.getpixel((card.x, card.y + card.height // 2)) == ImageColor.getrgb(
            renderer.theme.card_outline
        )

    assert _contrast_ratio(renderer.theme.primary_text, renderer.theme.card) >= 7.0
    assert _contrast_ratio(renderer.theme.primary_text, renderer.theme.raised_card) >= 7.0
    assert _contrast_ratio(renderer.theme.card_outline, renderer.theme.card) >= 3.0
    assert renderer.theme.card_outline == "#466486"
    assert _contrast_ratio(renderer.theme.card_outline, renderer.theme.raised_card) >= 2.8
    for left_key, right_key in (
        ("cpu_label", "cpu_gauge_status"),
        ("gpu_label", "gpu_gauge_status"),
        ("memory_label", "memory_used"),
        ("ai_title", "ai_status"),
    ):
        assert not _bbox_intersects(
            renderer.last_placements[left_key].bbox,
            renderer.last_placements[right_key].bbox,
        )


@pytest.mark.parametrize("layout", [LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT])
def test_primary_metrics_and_bars_dominate_reduced_trend_graphs(
    layout: DashboardLayout,
) -> None:
    base = snapshot(82)
    renderer = DashboardRenderer(layout=layout)
    renderer.render(
        replace(
            base,
            ai=replace(
                base.ai,
                title="CODEX LIMITS",
                primary_value="82%",
                primary_label="7D LEFT",
                budget_ratio=0.18,
                budget_label="7D USED 18%",
                demo=False,
            ),
        )
    )

    portrait = layout is PORTRAIT_LAYOUT
    assert renderer.last_placements["cpu_value"].font_size == (38 if portrait else 42)
    assert renderer.last_placements["gpu_value"].font_size == (38 if portrait else 42)
    assert renderer.last_placements["memory_value"].font_size == (40 if portrait else 44)
    assert renderer.last_placements["ai_primary"].font_size == (34 if portrait else 40)

    for key, card in (
        ("cpu", layout.cpu),
        ("gpu", layout.gpu),
        ("memory", layout.memory),
    ):
        gauge = renderer.last_gauges[key].track
        spark = renderer.last_sparklines[key]
        assert gauge.width == card.width - 22
        assert gauge.height >= 9
        assert spark.area / card.area <= 0.12
        assert spark.height <= 20
        if key in {"cpu", "gpu"}:
            assert spark.width <= round(card.width * 0.45)
        assert not _rect_intersects_bbox(gauge, renderer.last_placements[f"{key}_value"].bbox)

    assert renderer.last_budget_bar is not None
    assert renderer.last_budget_bar.width == layout.ai.width - 22
    assert renderer.last_budget_bar.height >= 9
    assert renderer.clipping_issues == ()


@pytest.mark.parametrize("layout", [LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT])
def test_metric_gauges_and_sparklines_never_cover_text(layout: DashboardLayout) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(
        replace(
            snapshot(100),
            cpu_model="Intel Core i9-14900KS Processor " + "X" * 32,
            gpu_model="NVIDIA GeForce RTX 5070 Ti " + "Y" * 32,
        )
    )

    for key, card in (("cpu", layout.cpu), ("gpu", layout.gpu), ("memory", layout.memory)):
        shapes = (renderer.last_gauges[key].track, renderer.last_sparklines[key])
        placements = [
            placement
            for placement in renderer.last_placements.values()
            if placement.drawn and placement.clip == card
        ]
        for shape in shapes:
            for placement in placements:
                assert not _rect_intersects_bbox(shape, placement.bbox), (key, placement.key)


@pytest.mark.parametrize("layout", [LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT])
@pytest.mark.parametrize("status", list(SyncStatus))
def test_large_ai_budget_bar_never_covers_dense_provider_text(
    layout: DashboardLayout,
    status: SyncStatus,
) -> None:
    ai = AIData(
        provider=AIProviderKind.OPENAI_API,
        title="OPENAI API ORGANIZATION USAGE",
        status=status,
        primary_value="$999.99",
        primary_label="TODAY COST",
        fields=(("REQUESTS", "1.0M"), ("INPUT", "999.9K"), ("OUTPUT", "100"), ("CACHED", "0")),
        last_sync=datetime(2026, 8, 10, 3, 0, tzinfo=timezone.utc),
        budget_ratio=1.0,
        budget_label="DAY 100% | MONTH 100%",
    )
    renderer = DashboardRenderer(layout=layout)
    renderer.render(replace(snapshot(100), ai=ai))

    assert renderer.last_budget_bar is not None
    placements = [
        placement
        for placement in renderer.last_placements.values()
        if placement.drawn and placement.clip == layout.ai
    ]
    for placement in placements:
        assert not _rect_intersects_bbox(renderer.last_budget_bar, placement.bbox), placement.key
    for left, right in combinations(placements, 2):
        assert not _bbox_intersects(left.bbox, right.bbox), (left.key, right.key)
    assert renderer.clipping_issues == ()


@pytest.mark.parametrize("layout", [LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT])
@pytest.mark.parametrize(
    ("primary", "label", "field_value"),
    [
        ("NO DATA", "CODEX SESSIONS", "NOT FOUND"),
        ("NO DATA", "USE CODEX FIRST", "NO LIMIT EVENT"),
        ("UNAVAILABLE", "LOCAL READ", "READ ERROR"),
    ],
)
def test_real_codex_error_values_fit_beside_labels_without_overlap(
    layout: DashboardLayout,
    primary: str,
    label: str,
    field_value: str,
) -> None:
    ai = AIData(
        provider=AIProviderKind.CODEX_LOCAL,
        title="CODEX LIMITS",
        status=SyncStatus.SETUP_REQUIRED,
        primary_value=primary,
        primary_label=label,
        fields=(("STATUS", field_value),),
    )
    renderer = DashboardRenderer(layout=layout)
    renderer.render(replace(snapshot(None), ai=ai))

    ai_placements = [
        placement
        for placement in renderer.last_placements.values()
        if placement.drawn and placement.clip == layout.ai
    ]
    for left, right in combinations(ai_placements, 2):
        assert not _bbox_intersects(left.bbox, right.bbox), (left.key, right.key)
    assert renderer.clipping_issues == ()


@pytest.mark.parametrize("layout", [LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT])
@pytest.mark.parametrize(
    "model",
    [None, "CPU", "Intel Core i7-14700K", "NVIDIA GeForce RTX 5070 Ti", "X" * 64],
)
def test_optional_hardware_model_lane_is_stable_fitted_and_non_overlapping(
    layout: DashboardLayout,
    model: str | None,
) -> None:
    base = snapshot(100)
    renderer = DashboardRenderer(layout=layout)
    renderer.render(replace(base, cpu_model=model, gpu_model=model))

    for key, card in (("cpu", layout.cpu), ("gpu", layout.gpu)):
        model_key = f"{key}_model"
        if model is None:
            assert model_key not in renderer.last_placements
        else:
            placement = renderer.last_placements[model_key]
            assert placement.font_size >= 9
            assert placement.font_name[1] == "Bold"
            assert _bbox_inside(placement.bbox, card)
            if len(model) == 64:
                assert placement.text.endswith("...")
            for other_key in (
                f"{key}_label",
                f"{key}_gauge_status",
                f"{key}_temperature",
                f"{key}_value",
            ):
                assert not _bbox_intersects(
                    placement.bbox,
                    renderer.last_placements[other_key].bbox,
                )
            assert not _rect_intersects_bbox(renderer.last_sparklines[key], placement.bbox)
            assert not _rect_intersects_bbox(renderer.last_gauges[key].track, placement.bbox)

    assert renderer.clipping_issues == ()


@pytest.mark.parametrize("layout", [LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT])
def test_model_presence_never_reflows_values_graphs_or_gauges(layout: DashboardLayout) -> None:
    base = snapshot(82)
    without = DashboardRenderer(layout=layout)
    without.render(replace(base, cpu_model=None, gpu_model=None))
    with_long = DashboardRenderer(layout=layout)
    with_long.render(replace(base, cpu_model="X" * 64, gpu_model="Y" * 64))

    for key in ("cpu", "gpu"):
        assert without.last_placements[f"{key}_value"].anchor_xy == with_long.last_placements[f"{key}_value"].anchor_xy
        assert without.last_gauges[key].track == with_long.last_gauges[key].track
        assert without.last_sparklines[key] == with_long.last_sparklines[key]


def test_memory_title_is_replaced_by_ram_everywhere() -> None:
    renderer = DashboardRenderer()
    renderer.render(snapshot(82))

    assert renderer.last_placements["memory_label"].text == "RAM"
    assert renderer.last_placements["memory_bar_label"].text == "RAM USAGE"
    assert all(placement.text != "MEMORY" for placement in renderer.last_placements.values())


@pytest.mark.parametrize("layout", [LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT])
@pytest.mark.parametrize("status", list(SyncStatus))
def test_readable_identity_lanes_preserve_no_data_and_ai_error_states(
    layout: DashboardLayout,
    status: SyncStatus,
) -> None:
    base = snapshot(None)
    renderer = DashboardRenderer(layout=layout)

    renderer.render(
        replace(
            base,
            ai=replace(
                base.ai,
                title="CHATGPT ACTIVITY SESSION LIMITS",
                status=status,
                primary_value="--",
                demo=False,
            ),
        )
    )

    assert renderer.clipping_issues == ()
    for key in ("cpu_value", "gpu_value", "memory_value", "ai_primary"):
        assert _bbox_inside(renderer.last_placements[key].bbox, renderer.last_placements[key].clip)
    for left_key, right_key in (
        ("cpu_label", "cpu_gauge_status"),
        ("gpu_label", "gpu_gauge_status"),
        ("ai_title", "ai_status"),
    ):
        assert not _bbox_intersects(
            renderer.last_placements[left_key].bbox,
            renderer.last_placements[right_key].bbox,
        )
    assert (
        renderer.last_placements["ai_title"].bbox[2] + 8
        <= renderer.last_placements["ai_status"].bbox[0]
    )


@pytest.mark.parametrize("layout", [LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT])
def test_long_ai_title_fits_dynamically_beside_long_status(
    layout: DashboardLayout,
) -> None:
    base = snapshot(82)
    renderer = DashboardRenderer(layout=layout)

    renderer.render(
        replace(
            base,
            ai=replace(
                base.ai,
                title="A VERY LONG AI PROVIDER LIMITS DASHBOARD",
                status=SyncStatus.SETUP_REQUIRED,
                demo=False,
            ),
        )
    )

    title = renderer.last_placements["ai_title"]
    status = renderer.last_placements["ai_status"]
    assert title.text.endswith("...")
    assert title.font_size == 12
    assert title.font_name[1] == "ExtraBold"
    assert title.bbox[2] + 8 <= status.bbox[0]
    assert not _bbox_intersects(title.bbox, status.bbox)
    assert renderer.clipping_issues == ()


@pytest.mark.parametrize("layout", [LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT])
@pytest.mark.parametrize("scenario", ["codex_no_data", "openai_wide_value", "activity_wide_value"])
def test_identity_change_does_not_overlap_dense_ai_lanes(
    layout: DashboardLayout,
    scenario: str,
) -> None:
    base = snapshot(None)
    if scenario == "codex_no_data":
        ai = AIData(
            provider=AIProviderKind.CODEX_LOCAL,
            title="CODEX LIMITS",
            status=SyncStatus.SETUP_REQUIRED,
            primary_value="NO DATA",
            primary_label="CODEX SESSIONS",
            fields=(("STATUS", "NOT FOUND"),),
            budget_ratio=0.5,
            budget_label="A DELIBERATELY VERY LONG BUDGET LABEL 100%",
        )
    elif scenario == "openai_wide_value":
        ai = AIData(
            provider=AIProviderKind.OPENAI_API,
            title="OPENAI API",
            status=SyncStatus.OK,
            primary_value="$999.99",
            primary_label="TODAY COST",
            fields=(("REQUESTS", "1.0M"), ("INPUT", "999.9K"), ("OUTPUT", "100"), ("CACHED", "0")),
            budget_ratio=0.5,
            budget_label="A DELIBERATELY VERY LONG BUDGET LABEL 100%",
        )
    else:
        ai = AIData(
            provider=AIProviderKind.CHATGPT_ACTIVITY,
            title="CHATGPT ACTIVITY",
            status=SyncStatus.OK,
            primary_value="123H 59M",
            primary_label="ACTIVE TODAY",
            fields=(("SESSIONS", "99"),),
            budget_ratio=None,
        )
    renderer = DashboardRenderer(layout=layout)

    renderer.render(replace(base, ai=ai))

    assert renderer.clipping_issues == ()
    heading = renderer.last_placements["ai_title"]
    status = renderer.last_placements["ai_status"]
    assert 12 <= heading.font_size <= 18
    assert heading.bbox[2] + 8 <= status.bbox[0]
    ai_placements = [
        placement
        for key, placement in renderer.last_placements.items()
        if key.startswith("ai_") and placement.drawn
    ]
    for left, right in combinations(ai_placements, 2):
        assert not _bbox_intersects(left.bbox, right.bbox), (left.key, right.key)


@pytest.mark.parametrize("status", list(SyncStatus))
@pytest.mark.parametrize(
    "budget_label",
    ["Monthly budget remaining 100%", "DAY 100% | MONTH 100%"],
)
def test_landscape_openai_four_field_budget_lane_never_overlaps(
    status: SyncStatus,
    budget_label: str,
) -> None:
    base = snapshot(None)
    ai = AIData(
        provider=AIProviderKind.OPENAI_API,
        title="OPENAI API ORGANIZATION USAGE",
        status=status,
        primary_value="$999.99",
        primary_label="TODAY COST",
        fields=(("REQUESTS", "1.0M"), ("INPUT", "999.9K"), ("OUTPUT", "100"), ("CACHED", "0")),
        budget_ratio=0.5,
        budget_label=budget_label,
    )
    renderer = DashboardRenderer(layout=LANDSCAPE_LAYOUT)

    renderer.render(replace(base, ai=ai))

    assert renderer.clipping_issues == ()
    assert (
        renderer.last_placements["ai_title"].bbox[2] + 8
        <= renderer.last_placements["ai_status"].bbox[0]
    )
    budget = renderer.last_placements["ai_budget_label"]
    field_value = renderer.last_placements["ai_field_2_value"]
    sync = renderer.last_placements["ai_last_sync"]
    assert field_value.bbox[3] <= budget.bbox[1]
    assert budget.bbox[3] <= sync.bbox[1]
    ai_placements = [
        placement
        for key, placement in renderer.last_placements.items()
        if key.startswith("ai_") and placement.drawn
    ]
    for left, right in combinations(ai_placements, 2):
        assert not _bbox_intersects(left.bbox, right.bbox), (left.key, right.key)


def test_codex_remaining_card_keeps_compact_fields_and_budget_semantics_inside_card() -> None:
    base = snapshot(82)
    codex = AIData(
        provider=AIProviderKind.CODEX_LOCAL,
        title="CODEX LIMITS",
        status=SyncStatus.DELAYED,
        primary_value="82%",
        primary_label="7D LEFT",
        fields=(("7D RESET", "5D 13H"),),
        last_sync=datetime(2026, 8, 10, 12, 59, 27, tzinfo=timezone.utc),
        budget_ratio=0.18,
        budget_label="7D USED 18%",
    )
    renderer = DashboardRenderer()
    renderer.render(
        DisplaySnapshot(
            timestamp=base.timestamp,
            cpu_percent=base.cpu_percent,
            cpu_temperature=base.cpu_temperature,
            gpu_percent=base.gpu_percent,
            gpu_temperature=base.gpu_temperature,
            memory_percent=base.memory_percent,
            memory_used_gib=base.memory_used_gib,
            memory_total_gib=base.memory_total_gib,
            memory_available_gib=base.memory_available_gib,
            cpu_history=base.cpu_history,
            gpu_history=base.gpu_history,
            memory_history=base.memory_history,
            ai=codex,
            connection=base.connection,
        )
    )

    assert renderer.clipping_issues == ()
    assert renderer.last_placements["ai_primary"].text == "82%"
    ai_group = renderer.last_placements["ai_primary"]
    ai_digits = renderer.last_placements["ai_primary_digits"]
    ai_suffix = renderer.last_placements["ai_primary_suffix"]
    assert ai_group.drawn is False
    assert ai_digits.text == "82"
    assert ai_suffix.text == "%"
    assert ai_suffix.font_size == round(ai_digits.font_size * 0.5)
    assert ai_suffix.baseline_y is not None
    assert ai_digits.baseline_y is not None
    assert abs(ai_suffix.baseline_y - ai_digits.baseline_y) <= 1
    assert ai_suffix.bbox[1] > ai_digits.bbox[1]
    assert ai_suffix.bbox[3] == ai_digits.bbox[3]
    assert ai_group.bbox[2] == ai_group.anchor_xy[0]
    assert ai_digits.bbox[2] <= ai_suffix.bbox[0]
    assert ai_group.bbox == _bbox_union(ai_digits.bbox, ai_suffix.bbox)
    assert renderer.last_placements["ai_primary_label"].text == "7D LEFT"
    assert renderer.last_placements["ai_field_0_label"].text == "7D RESET"
    assert renderer.last_placements["ai_budget_label"].text == "7D USED 18%"
    assert all(placement.text != "5H RESET" for placement in renderer.last_placements.values())


@pytest.mark.parametrize("layout", [LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT])
@pytest.mark.parametrize("primary", ["0%", "9%", "10%", "78%", "99%", "100%", "--"])
def test_codex_quota_value_uses_the_same_fixed_right_edge_as_ram(
    layout: DashboardLayout,
    primary: str,
) -> None:
    base = snapshot(74)
    codex = AIData(
        provider=AIProviderKind.CODEX_LOCAL,
        title="CODEX LIMITS",
        status=SyncStatus.OK,
        primary_value=primary,
        primary_label="5H LEFT",
        fields=(("7D LEFT", "79%"), ("7D RESET", "3D 8H")),
        last_sync=base.timestamp,
        budget_ratio=0.22,
        budget_label="5H USED 22%",
    )
    renderer = DashboardRenderer(layout=layout)
    renderer.render(replace(base, ai=codex))

    card = layout.ai
    primary_placement = renderer.last_placements["ai_primary"]
    assert primary_placement.bbox[2] == card.x + 112
    assert primary_placement.anchor_xy[0] == card.x + 112
    assert primary_placement.font_size == (40 if layout is PORTRAIT_LAYOUT else 44)
    assert renderer.clipping_issues == ()

    if primary.endswith("%"):
        digits = renderer.last_placements["ai_primary_digits"]
        suffix = renderer.last_placements["ai_primary_suffix"]
        assert digits.bbox[2] <= suffix.bbox[0]
        assert digits.bbox[3] == suffix.bbox[3]
        assert suffix.bbox[2] == card.x + 112
    else:
        assert "ai_primary_suffix" not in renderer.last_placements


def test_codex_quota_value_has_matching_title_gap_in_both_orientations() -> None:
    base = snapshot(74)
    codex = AIData(
        provider=AIProviderKind.CODEX_LOCAL,
        title="CODEX LIMITS",
        status=SyncStatus.DELAYED,
        primary_value="78%",
        primary_label="5H LEFT",
        fields=(("7D LEFT", "79%"), ("7D RESET", "3D 8H")),
        last_sync=base.timestamp,
        budget_ratio=0.22,
        budget_label="5H USED 22%",
    )
    gaps: list[int] = []
    tops: list[int] = []
    for layout in (LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT):
        renderer = DashboardRenderer(layout=layout)
        renderer.render(replace(base, ai=codex))
        card = layout.ai
        title = renderer.last_placements["ai_title"]
        value = renderer.last_placements["ai_primary"]
        gaps.append(value.bbox[1] - title.bbox[3])
        tops.append(value.bbox[1] - card.y)
        assert renderer.clipping_issues == ()
        for left_key, right_key in combinations(renderer.last_placements, 2):
            left = renderer.last_placements[left_key]
            right = renderer.last_placements[right_key]
            if left.clip == card and right.clip == card and left.drawn and right.drawn:
                assert not _bbox_intersects(left.bbox, right.bbox), (left_key, right_key)

    assert all(16 <= gap <= 28 for gap in gaps)
    assert abs(tops[0] - tops[1]) <= 12

    # The CODEX quota KPI deliberately reuses the RAM card's exact visual
    # geometry in each orientation, so the two large readouts scan as one row.
    for layout in (LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT):
        renderer = DashboardRenderer(layout=layout)
        renderer.render(replace(base, memory_percent=Metric(78, "%"), ai=codex))
        ai_value = renderer.last_placements["ai_primary"]
        ram_value = renderer.last_placements["memory_value"]
        assert ai_value.anchor_xy[0] - layout.ai.x == ram_value.anchor_xy[0] - layout.memory.x
        assert ai_value.anchor_xy[1] - layout.ai.y == ram_value.anchor_xy[1] - layout.memory.y
        assert ai_value.font_size == ram_value.font_size
        assert tuple(
            coordinate - offset
            for coordinate, offset in zip(
                ai_value.bbox,
                (layout.ai.x, layout.ai.y, layout.ai.x, layout.ai.y),
            )
        ) == tuple(
            coordinate - offset
            for coordinate, offset in zip(
                ram_value.bbox,
                (layout.memory.x, layout.memory.y, layout.memory.x, layout.memory.y),
            )
        )


def test_non_codex_portrait_primary_keeps_its_existing_left_anchor() -> None:
    base = snapshot(74)
    renderer = DashboardRenderer(layout=PORTRAIT_LAYOUT)
    renderer.render(base)

    assert renderer.last_placements["ai_primary"].anchor_xy == (
        PORTRAIT_LAYOUT.ai.x + 11,
        PORTRAIT_LAYOUT.ai.y + 52,
    )


@pytest.mark.parametrize("layout", [LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT])
@pytest.mark.parametrize("primary", ["$999.99", "5H 13M", "READY", "--", "READY 50%"])
def test_non_percentage_ai_primary_values_remain_single_full_size_runs(
    primary: str,
    layout: DashboardLayout,
) -> None:
    base = snapshot(82)
    renderer = DashboardRenderer(layout=layout)
    renderer.render(replace(base, ai=replace(base.ai, primary_value=primary)))

    placement = renderer.last_placements["ai_primary"]
    assert placement.text == primary
    assert placement.drawn is True
    assert "ai_primary_digits" not in renderer.last_placements
    assert "ai_primary_suffix" not in renderer.last_placements


@pytest.mark.parametrize("layout", [LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT])
@pytest.mark.parametrize("value", [0, 9, 10, 99, 100, None])
def test_percentage_boundaries_keep_compact_suffixes_inside_both_layouts(
    value: int | None,
    layout: DashboardLayout,
) -> None:
    base = snapshot(value)
    primary = "--" if value is None else f"{value}%"
    portrait_snapshot = replace(
        base,
        ai=replace(base.ai, primary_value=primary, primary_label="7D LEFT"),
    )
    renderer = DashboardRenderer(layout=layout)

    image = renderer.render(portrait_snapshot)

    assert image.size == layout.size
    assert renderer.clipping_issues == ()
    for key in ("cpu_value", "gpu_value", "memory_value", "ai_primary"):
        group = renderer.last_placements[key]
        assert _bbox_inside(group.bbox, group.clip)
        if value is None:
            assert group.drawn is True
            assert f"{key}_suffix" not in renderer.last_placements
            continue
        digits = renderer.last_placements[f"{key}_digits"]
        suffix = renderer.last_placements[f"{key}_suffix"]
        assert group.drawn is False
        assert digits.bbox[2] <= suffix.bbox[0]
        assert suffix.font_size == round(digits.font_size * 0.5)
        assert suffix.baseline_y is not None
        assert digits.baseline_y is not None
        assert abs(suffix.baseline_y - digits.baseline_y) <= 1
        assert suffix.bbox[1] > digits.bbox[1]
        assert suffix.bbox[3] == digits.bbox[3]
        assert group.bbox == _bbox_union(digits.bbox, suffix.bbox)


@pytest.mark.parametrize(
    ("value", "expected_state"),
    [(0, "NORMAL"), (9, "NORMAL"), (10, "NORMAL"), (80, "CAUTION"), (94, "CAUTION"), (99, "WARNING"), (100, "WARNING")],
)
def test_gauge_states_are_explicit_and_do_not_rely_on_length_or_color(value: int, expected_state: str) -> None:
    renderer = DashboardRenderer()
    renderer.render(snapshot(value))
    assert renderer.clipping_issues == ()
    for key in ("cpu", "gpu", "memory"):
        assert renderer.last_gauges[key].state == expected_state
        assert renderer.last_placements[f"{key}_gauge_status"].text == expected_state


@pytest.mark.parametrize("value", [0, 9, 10, 99, 100])
def test_gauge_fill_geometry_is_clamped_to_each_card(value: int) -> None:
    renderer = DashboardRenderer()
    renderer.render(snapshot(value))
    cards = {"cpu": CPU, "gpu": GPU, "memory": MEMORY}
    tracks = {"cpu": CPU_GAUGE, "gpu": GPU_GAUGE, "memory": MEMORY_GAUGE}

    for key, card in cards.items():
        gauge = renderer.last_gauges[key]
        assert gauge.track == tracks[key]
        assert card.x <= gauge.track.x < gauge.track.right <= card.right
        assert card.y <= gauge.track.y < gauge.track.bottom <= card.bottom
        assert gauge.ratio == pytest.approx(value / 100.0)
        if value == 0:
            assert gauge.fill is None
        else:
            assert gauge.fill is not None
            inner_width = gauge.track.width - 2
            assert gauge.fill.width == min(inner_width, max(1, round(inner_width * value / 100.0)))
            assert gauge.track.x < gauge.fill.x < gauge.fill.right < gauge.track.right
            assert gauge.track.y < gauge.fill.y < gauge.fill.bottom < gauge.track.bottom


def test_static_tracks_and_unavailable_pattern_have_bounded_pixels() -> None:
    renderer = DashboardRenderer()
    zero = renderer.render(snapshot(0))
    unavailable = renderer.render(snapshot(None))
    border = ImageColor.getrgb(renderer.theme.border)

    for track in (CPU_GAUGE, GPU_GAUGE, MEMORY_GAUGE):
        top_edge = (track.x + track.width // 2, track.y)
        assert zero.getpixel(top_edge) == border
        assert unavailable.getpixel(top_edge) == border
        zero_crop = zero.crop((track.x + 1, track.y + 1, track.right - 1, track.bottom - 1))
        unavailable_crop = unavailable.crop((track.x + 1, track.y + 1, track.right - 1, track.bottom - 1))
        assert zero_crop.tobytes() != unavailable_crop.tobytes()


@pytest.mark.parametrize("status", list(ConnectionStatus))
def test_every_connection_state_stays_inside_its_card(status: ConnectionStatus) -> None:
    renderer = DashboardRenderer()
    renderer.render(snapshot(100, connection=status))
    assert renderer.clipping_issues == ()
    assert renderer.last_placements["connection_status"].text == status.value


def test_portrait_dashboard_uses_active_cards_and_separate_compact_lanes() -> None:
    base = snapshot(100, connection=ConnectionStatus.RECONNECTING)
    portrait_snapshot = replace(
        base,
        connection=ConnectionData(
            ConnectionStatus.RECONNECTING,
            detail="NO VERIFIED DEVICE WITH A DELIBERATELY LONG DETAIL",
        ),
        ai=replace(base.ai, budget_label="DAY USED 100%"),
    )
    renderer = DashboardRenderer(layout=PORTRAIT_LAYOUT)

    image = renderer.render(portrait_snapshot)

    assert image.size == (320, 480)
    assert renderer.clipping_issues == ()
    expected_clips = {
        "connection": PORTRAIT_LAYOUT.connection,
        "cpu": PORTRAIT_LAYOUT.cpu,
        "gpu": PORTRAIT_LAYOUT.gpu,
        "memory": PORTRAIT_LAYOUT.memory,
        "ai": PORTRAIT_LAYOUT.ai,
    }
    for key, placement in renderer.last_placements.items():
        prefix = next(name for name in expected_clips if key.startswith(name))
        assert placement.clip == expected_clips[prefix]
        left, top, right, bottom = placement.bbox
        assert 0 <= left < right <= image.width
        assert 0 <= top < bottom <= image.height

    for gauge_key, card in (
        ("cpu", PORTRAIT_LAYOUT.cpu),
        ("gpu", PORTRAIT_LAYOUT.gpu),
        ("memory", PORTRAIT_LAYOUT.memory),
    ):
        track = renderer.last_gauges[gauge_key].track
        assert card.x <= track.x < track.right <= card.right
        assert card.y <= track.y < track.bottom <= card.bottom
        assert not _rect_intersects_bbox(track, renderer.last_placements[f"{gauge_key}_value"].bbox)

    connection_keys = ("connection_label", "connection_status", "connection_detail")
    for left_key, right_key in combinations(connection_keys, 2):
        assert not _bbox_intersects(
            renderer.last_placements[left_key].bbox,
            renderer.last_placements[right_key].bbox,
        )

    ai_placements = [
        placement
        for key, placement in renderer.last_placements.items()
        if key.startswith("ai_") and placement.drawn
    ]
    assert len([key for key in renderer.last_placements if key.startswith("ai_field_")]) == 8
    for left, right in combinations(ai_placements, 2):
        assert not _bbox_intersects(left.bbox, right.bbox), (left.key, right.key)


def _bbox_intersects(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> bool:
    return not (
        left[2] <= right[0]
        or right[2] <= left[0]
        or left[3] <= right[1]
        or right[3] <= left[1]
    )


def _bbox_union(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    return (
        min(left[0], right[0]),
        min(left[1], right[1]),
        max(left[2], right[2]),
        max(left[3], right[3]),
    )


def _bbox_inside(bbox: tuple[int, int, int, int], rect: Rect) -> bool:
    return rect.x <= bbox[0] < bbox[2] <= rect.right and rect.y <= bbox[1] < bbox[3] <= rect.bottom


def _rect_intersects_bbox(rect: Rect, bbox: tuple[int, int, int, int]) -> bool:
    return _bbox_intersects((rect.x, rect.y, rect.right, rect.bottom), bbox)


def _alpha_max(image: Image.Image, rect: Rect) -> int:
    return image.getchannel("A").crop((rect.x, rect.y, rect.right, rect.bottom)).getextrema()[1]


def _contrast_ratio(foreground: str, background: str) -> float:
    def luminance(color: str) -> float:
        channels = []
        for channel in ImageColor.getrgb(color):
            value = channel / 255.0
            channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    first = luminance(foreground)
    second = luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)
