# SPDX-License-Identifier: GPL-3.0-or-later

"""Native dashboard contracts for the five-card design."""

from __future__ import annotations

from dataclasses import replace
from itertools import combinations

import pytest
from PIL import Image

from ai_mini_monitor.demo import demo_snapshot
from ai_mini_monitor.models import AIData, AIProviderKind, ConnectionStatus, Metric, SyncStatus
from ai_mini_monitor.rendering.fonts import digits_are_tabular, metric_mono
from ai_mini_monitor.rendering.layout import LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT, DashboardLayout
from ai_mini_monitor.rendering.renderer import DashboardRenderer, OverlayLayers, get_overlay_layers


LAYOUTS = (LANDSCAPE_LAYOUT, PORTRAIT_LAYOUT)


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("value", [None, 0, 100])
def test_all_five_cards_put_details_left_of_large_right_percent_and_gauge(layout: DashboardLayout, value: int | None) -> None:
    renderer = DashboardRenderer(layout=layout)
    ai = _codex("--" if value is None else f"{value}%")
    renderer.render(replace(_snapshot(value), ai=ai))
    for key in ("cpu", "memory", "gpu", "vram", "ai"):
        title_key = "ai_title" if key == "ai" else f"{key}_label"
        value_key = "ai_primary" if key == "ai" else f"{key}_value"
        title = renderer.last_placements[title_key]
        value_placement = renderer.last_placements[value_key]
        gauge = renderer.last_gauges[key].track
        assert title.bbox[3] < value_placement.bbox[1]
        assert value_placement.bbox[2] < gauge.x
        assert gauge.height >= 35
        if value is not None or key == "vram":
            digits = renderer.last_placements[f"{value_key}_digits"]
            visible_height = digits.bbox[3] - digits.bbox[1]
            assert abs(visible_height - gauge.height) <= 7
    assert renderer.clipping_issues == ()


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("cpu,gpu", [
    ("Intel Core i7-14700K", "NVIDIA GeForce RTX 5070 Ti"),
    ("AMD Ryzen 9 9950X3D 16-Core Processor", "AMD Radeon RX 7900 XTX"),
])
def test_realistic_hardware_models_wrap_fully_without_ellipsis_or_readout_overlap(layout: DashboardLayout, cpu: str, gpu: str) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(replace(_snapshot(), cpu_model=cpu, gpu_model=gpu))
    for key, original in (("cpu", cpu), ("gpu", gpu)):
        model = renderer.last_placements[f"{key}_model"]
        value = renderer.last_placements[f"{key}_value"]
        assert " ".join(model.text.split()) == original
        assert "..." not in model.text
        assert model.bbox[2] < value.bbox[0]
        assert not model.clipped
    assert renderer.clipping_issues == ()


@pytest.mark.parametrize("layout", LAYOUTS)
def test_five_cards_have_bottom_up_vertical_gauges_without_graphs(layout: DashboardLayout) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(_snapshot(25))
    assert set(renderer.last_gauges) == {"cpu", "memory", "gpu", "vram", "ai"}
    assert renderer.last_sparklines == {}
    assert renderer.last_budget_bar is None
    for key in ("cpu", "memory", "gpu", "vram", "ai"):
        card = layout.card_rects[key]
        gauge = renderer.last_gauges[key]
        assert gauge.track.height > gauge.track.width
        assert card.x < gauge.track.x < gauge.track.right < card.right
        assert card.y < gauge.track.y < gauge.track.bottom < card.bottom
        assert gauge.fill is not None
        assert gauge.fill.bottom == gauge.track.bottom - 1
        assert abs(gauge.fill.height / (gauge.track.height - 2) - gauge.ratio) < 0.02


@pytest.mark.parametrize("layout", LAYOUTS)
def test_vram_is_own_card_with_percentage_and_capacity(layout: DashboardLayout) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(_snapshot(vram=(7.5, 12.0)))
    assert renderer.last_placements["vram_label"].text == "VRAM"
    assert renderer.last_placements["vram_value"].text == "62%"
    assert renderer.last_placements["vram_used"].text == "7.5 / 12.0 GiB"
    assert renderer.last_gauges["vram"].ratio == 0.625
    assert renderer.clipping_issues == ()


@pytest.mark.parametrize("layout", LAYOUTS)
def test_codex_gauge_tracks_displayed_remaining_percent(layout: DashboardLayout) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(replace(_snapshot(), ai=_codex("25%")))
    assert renderer.last_placements["ai_primary"].text == "25%"
    assert renderer.last_gauges["ai"].ratio == 0.25


@pytest.mark.parametrize("remaining,state,color", [
    (100, "NORMAL", "ai_accent"),
    (85, "NORMAL", "ai_accent"),
    (20, "CAUTION", "warning"),
    (5, "WARNING", "error"),
    (0, "WARNING", None),
])
@pytest.mark.parametrize("layout", LAYOUTS)
def test_codex_remaining_gauge_severity_and_fill(layout: DashboardLayout, remaining: int, state: str, color: str | None) -> None:
    renderer = DashboardRenderer(layout=layout)
    frame = renderer.render(replace(_snapshot(), ai=_codex(f"{remaining}%")))
    gauge = renderer.last_gauges["ai"]
    assert gauge.ratio == remaining / 100
    assert gauge.state == state
    if remaining == 0:
        assert gauge.fill is None
    else:
        assert gauge.fill is not None
        point = (gauge.fill.x + gauge.fill.width // 2, gauge.fill.y + gauge.fill.height // 2)
        from PIL import ImageColor

        assert frame.getpixel(point) == ImageColor.getrgb(getattr(renderer.theme, color))


def test_non_codex_budget_keeps_load_style_severity() -> None:
    renderer = DashboardRenderer()
    ai = replace(_codex(), provider=AIProviderKind.OPENAI_API, budget_ratio=0.85)
    renderer.render(replace(_snapshot(), ai=ai))
    assert renderer.last_gauges["ai"].ratio == 0.85
    assert renderer.last_gauges["ai"].state == "CAUTION"


@pytest.mark.parametrize("layout", LAYOUTS)
def test_codex_keeps_unknown_credits_but_omits_reset_and_last_sync(layout: DashboardLayout) -> None:
    renderer = DashboardRenderer(layout=layout)
    ai = replace(_codex(), fields=(("5H LEFT", "62%"), ("7D RESET", "TOMORROW"), ("CREDITS", "--")))
    renderer.render(replace(_snapshot(), ai=ai))
    assert renderer.last_placements["ai_field_1_label"].text == "CREDITS"
    assert renderer.last_placements["ai_field_1_value"].text == "--"
    assert "ai_last_sync" not in renderer.last_placements


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("credit", ["1250.50", "UNLIMITED", "--"])
@pytest.mark.parametrize("capacity", [(89.6, 128.0), (128.0, 256.0)])
def test_dense_five_card_text_never_overlaps_other_text_or_vertical_gauge(layout: DashboardLayout, credit: str, capacity: tuple[float, float]) -> None:
    renderer = DashboardRenderer(layout=layout)
    ai = replace(_codex(), fields=(("5H LEFT", "62%"), ("CREDITS", credit)))
    snapshot = replace(
        _snapshot(vram=capacity),
        memory_used_gib=Metric(capacity[0], "GiB"),
        memory_total_gib=Metric(capacity[1], "GiB"),
        ai=ai,
    )
    renderer.render(snapshot)
    assert renderer.clipping_issues == ()
    for key, gauge in renderer.last_gauges.items():
        drawn = [placement for placement in renderer.last_placements.values() if placement.drawn and placement.clip == layout.card_rects[key]]
        for placement in drawn:
            assert placement.bbox[2] <= gauge.track.x or placement.bbox[0] >= gauge.track.right, (key, placement.key)
        for index, first in enumerate(drawn):
            for second in drawn[index + 1:]:
                assert (first.bbox[2] <= second.bbox[0] or second.bbox[2] <= first.bbox[0]
                        or first.bbox[3] <= second.bbox[1] or second.bbox[3] <= first.bbox[1]), (key, first.key, second.key)


def _codex(value: str = "79%", *, status: SyncStatus = SyncStatus.OK) -> AIData:
    return AIData(
        provider=AIProviderKind.CODEX_ACCOUNT,
        title="CODEX",
        status=status,
        primary_value=value,
        primary_label="7D LEFT",
        fields=(("5H LEFT", "62%"), ("5H RESET", "13:30"), ("CREDITS", "120")),
        budget_ratio=0.79,
        budget_label="7D LEFT",
    )


def _snapshot(value: int | None = 79, *, vram: tuple[float | None, float | None] = (7.5, 12.0)):
    return replace(
        demo_snapshot(),
        cpu_percent=Metric(value, "%"),
        gpu_percent=Metric(value, "%"),
        memory_percent=Metric(value, "%"),
        gpu_vram_used_gib=Metric(vram[0], "GiB"),
        gpu_vram_total_gib=Metric(vram[1], "GiB"),
        gpu_power_w=Metric(210, "W"),
        ai=_codex(),
    )


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("value", [0, 9, 99, 100])
def test_native_percent_anchors_and_glyph_baselines(layout: DashboardLayout, value: int) -> None:
    renderer = DashboardRenderer(layout=layout)
    image = renderer.render(replace(_snapshot(value), ai=_codex(f"{value}%")))
    assert image.size == layout.size
    assert image.mode == "RGB"
    assert renderer.clipping_issues == ()
    for key, card in (("cpu", layout.cpu), ("memory", layout.memory), ("gpu", layout.gpu), ("ai", layout.ai)):
        title = "ai_title" if key == "ai" else f"{key}_label"
        readout = "ai_primary" if key == "ai" else f"{key}_value"
        digits = renderer.last_placements[f"{readout}_digits"]
        suffix = renderer.last_placements[f"{readout}_suffix"]
        group = renderer.last_placements[readout]
        assert renderer.last_placements[title].font_size >= 18
        assert group.anchor_xy == (renderer.last_gauges[key].track.x - 6, card.bottom - 10)
        assert digits.font_size >= 48
        assert suffix.font_size == round(digits.font_size * 0.5)
        assert digits.bbox[3] == suffix.bbox[3]
        assert digits.bbox[2] <= suffix.bbox[0]


@pytest.mark.parametrize("layout", LAYOUTS)
def test_vertical_tracks_do_not_cover_readouts(layout: DashboardLayout) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(_snapshot())
    for key, card in (("cpu", layout.cpu), ("memory", layout.memory), ("gpu", layout.gpu), ("vram", layout.vram), ("ai", layout.ai)):
        track = renderer.last_gauges[key].track
        assert track.height >= 35
        assert card.x < track.x < track.right < card.right
        assert card.y < track.y < track.bottom < card.bottom
        for placement in renderer.last_placements.values():
            if placement.drawn and placement.clip == card:
                assert placement.bbox[2] <= track.x or placement.bbox[0] >= track.right
    assert renderer.last_sparklines == {}
    assert renderer.last_budget_bar is None


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("used,total,text", [(7.5, 12.0, "7.5 / 12.0 GiB"), (None, 12.0, "-- / -- GiB"), (8.0, None, "-- / -- GiB"), (16.0, 16.0, "16.0 / 16.0 GiB"), (64.0, 128.0, "64.0 / 128.0 GiB")])
def test_vram_capacity_is_on_own_card(layout: DashboardLayout, used: float | None, total: float | None, text: str) -> None:
    renderer = DashboardRenderer(layout=layout)
    image = renderer.render(_snapshot(vram=(used, total)))
    value = renderer.last_placements["vram_used"]
    assert value.text == text
    assert not value.clipped
    assert value.clip == layout.vram
    assert renderer.last_placements["vram_value"].text == (f"{round(used / total * 100)}%" if used is not None and total is not None and total > 0 else "--")


@pytest.mark.parametrize("layout", LAYOUTS)
def test_power_is_omitted_when_sensor_unavailable(layout: DashboardLayout) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(replace(_snapshot(), gpu_power_w=Metric(None, "W")))
    assert "gpu_power" not in renderer.last_placements


@pytest.mark.parametrize("layout", LAYOUTS)
def test_pathological_unbroken_models_are_reported_without_silent_ellipsis(layout: DashboardLayout) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(replace(_snapshot(), cpu_model="X" * 100, gpu_model="Y" * 100))
    assert renderer.last_placements["cpu_model"].text == "X" * 100
    assert renderer.last_placements["gpu_model"].text == "Y" * 100
    assert all("..." not in item.text for item in renderer.last_placements.values())
    if layout is LANDSCAPE_LAYOUT:
        assert {issue.key for issue in renderer.clipping_issues} == {"cpu_model", "gpu_model"}
    assert renderer.last_placements["gpu_value"].anchor_xy == (renderer.gpu_gauge.x - 6, layout.gpu.bottom - 10)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_codex_card_keeps_longest_window_primary_and_omits_reset(layout: DashboardLayout) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(_snapshot())
    text = " ".join(item.text for item in renderer.last_placements.values())
    assert renderer.last_placements["ai_primary"].text == "79%"
    assert "7D LEFT" in text
    assert "5H LEFT" in text
    assert "CREDITS" in text
    assert "RESET" not in text
    assert renderer.last_placements["ai_field_0_label"].text == "5H LEFT"
    assert renderer.last_placements["ai_field_1_label"].text == "CREDITS"
    assert renderer.last_placements["ai_field_1_value"].text == "120"
    first = renderer.last_placements["ai_field_0_label"]
    second = renderer.last_placements["ai_field_1_label"]
    assert first.bbox[3] < second.bbox[1]
    assert renderer.last_gauges["ai"].ratio == 0.79
    assert renderer.clipping_issues == ()


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("value, expected", [
    ("60518.4300000000", "60,518"),
    ("60518.9900000000", "60,518"),
    ("1250.50", "1,250"),
    ("0.0000000000", "0"),
    ("123456789012.12345678", "123,456,789,012"),
    ("UNLIMITED", "UNLTD"),
    ("--", "--"),
])
def test_credit_balance_shows_every_whole_digit_with_separators_on_card(layout: DashboardLayout, value: str, expected: str) -> None:
    renderer = DashboardRenderer(layout=layout)
    ai = replace(_codex(), fields=(("5H LEFT", "62%"), ("CREDITS", value)))
    renderer.render(replace(_snapshot(), ai=ai))
    balance = renderer.last_placements["ai_field_1_value"]
    assert balance.text == expected
    assert balance.bbox[0] >= renderer.last_placements["ai_field_1_label"].bbox[2] + 6
    assert not balance.clipped
    assert renderer.clipping_issues == ()


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("status", [SyncStatus.OK, SyncStatus.DELAYED, SyncStatus.AUTH_ERROR, SyncStatus.NETWORK_ERROR, SyncStatus.SETUP_REQUIRED])
def test_codex_status_badge_is_absent_but_unknown_number_stays_visible(layout: DashboardLayout, status: SyncStatus) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(replace(_snapshot(), ai=_codex("--", status=status)))
    assert "ai_status" not in renderer.last_placements
    assert renderer.last_placements["ai_primary"].text == "--"
    assert renderer.clipping_issues == ()


@pytest.mark.parametrize("layout", LAYOUTS)
def test_overlay_semantics_keep_foreground_opaque_and_outer_canvas_clear(layout: DashboardLayout) -> None:
    renderer = DashboardRenderer(layout=layout)
    image = renderer.render(_snapshot())
    layers = get_overlay_layers(image)
    assert isinstance(layers, OverlayLayers)
    assert layers.size == layout.size
    assert layers.surface_mask.getpixel((0, 0)) == 0
    assert layers.surface_mask.getpixel((layout.cpu.x + 5, layout.cpu.y + 5)) == 255
    title = renderer.last_placements["cpu_label"].bbox
    crop = layers.foreground.crop(title)
    assert crop.getchannel("A").getextrema()[1] == 255
    assert layers.background.size == layers.foreground.size == image.size
    # Layer readers receive copies, so caller mutation cannot corrupt future overlay frames.
    changed = layers.foreground
    changed.putpixel((0, 0), (255, 0, 0, 255))
    assert layers.foreground.getpixel((0, 0))[3] == 0


@pytest.mark.parametrize("layout", LAYOUTS)
def test_card_background_is_soft_charcoal_without_canvas_grid(layout: DashboardLayout) -> None:
    renderer = DashboardRenderer(layout=layout)
    image = renderer.render(_snapshot())
    assert image.getpixel((0, 0)) == image.getpixel((4, 4))
    assert image.getpixel((layout.cpu.x + 5, layout.cpu.y + 5)) != image.getpixel((0, 0))
    assert all(not first.intersects(second) for first, second in combinations(layout.card_rects.values(), 2))


def test_extra_bold_metric_font_has_tabular_digits() -> None:
    assert digits_are_tabular(metric_mono(42))
    assert metric_mono(42).getname()[1] == "ExtraBold"


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("value,ratio,state", [(None, None, "NO DATA"), (0, 0.0, "NORMAL"), (80, 0.8, "CAUTION"), (100, 1.0, "WARNING")])
def test_gauge_fill_is_clamped_inside_track(layout: DashboardLayout, value: int | None, ratio: float | None, state: str) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(_snapshot(value))
    for key in ("cpu", "gpu", "memory"):
        gauge = renderer.last_gauges[key]
        assert gauge.ratio == ratio
        assert gauge.state == state
        if ratio in (None, 0.0):
            assert gauge.fill is None
        else:
            assert gauge.fill is not None
            assert gauge.track.x < gauge.fill.x < gauge.fill.right < gauge.track.right
            assert gauge.track.y < gauge.fill.y < gauge.fill.bottom < gauge.track.bottom


@pytest.mark.parametrize("layout", LAYOUTS)
def test_overlay_layers_recompose_physical_frame_without_altering_original(layout: DashboardLayout) -> None:
    renderer = DashboardRenderer(layout=layout)
    image = renderer.render(_snapshot())
    layers = get_overlay_layers(image)
    recomposed = Image.alpha_composite(layers.background.convert("RGBA"), layers.foreground).convert("RGB")
    assert recomposed.tobytes() == image.tobytes()


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("status", [ConnectionStatus.ONLINE, ConnectionStatus.RECONNECTING, ConnectionStatus.DISCONNECTED])
def test_connection_status_cannot_escape_thin_header(layout: DashboardLayout, status: ConnectionStatus) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(replace(_snapshot(), connection=replace(_snapshot().connection, status=status)))
    assert "connection_status" not in renderer.last_placements
    assert not renderer.last_placements["connection_detail"].clipped


def test_demo_error_preview_keeps_unknown_value_without_status_badge() -> None:
    renderer = DashboardRenderer()
    renderer.render(demo_snapshot("ai_error"))
    assert renderer.last_placements["connection_detail"].text == "DEMO"
    assert renderer.last_placements["ai_primary"].text == "--"
    assert "ai_status" not in renderer.last_placements
    assert renderer.clipping_issues == ()


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("value", [None, 0, 80, 100])
def test_metric_gauges_use_visual_states_without_status_words(layout: DashboardLayout, value: int | None) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(_snapshot(value))
    for key in ("cpu", "gpu", "memory"):
        assert key in renderer.last_gauges
        assert f"{key}_gauge_status" not in renderer.last_placements
