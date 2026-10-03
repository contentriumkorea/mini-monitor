# SPDX-License-Identifier: GPL-3.0-or-later

"""Native dashboard contracts for the balanced four-card design."""

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


def _codex(value: str = "79%", *, status: SyncStatus = SyncStatus.OK) -> AIData:
    return AIData(
        provider=AIProviderKind.CODEX_ACCOUNT,
        title="CODEX",
        status=status,
        primary_value=value,
        primary_label="7D LEFT",
        fields=(("5H LEFT", "62%"), ("5H RESET", "13:30")),
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
    portrait = layout is PORTRAIT_LAYOUT
    for key, card in (("cpu", layout.cpu), ("gpu", layout.gpu), ("memory", layout.memory), ("ai", layout.ai)):
        title = "memory_label" if key == "memory" else "ai_title" if key == "ai" else f"{key}_label"
        readout = "memory_value" if key == "memory" else "ai_primary" if key == "ai" else f"{key}_value"
        digits = renderer.last_placements[f"{readout}_digits"]
        suffix = renderer.last_placements[f"{readout}_suffix"]
        group = renderer.last_placements[readout]
        assert renderer.last_placements[title].font_size >= 18
        assert group.anchor_xy == (card.x + 112, card.y + (73 if portrait else 80))
        assert digits.font_size == (40 if portrait else 42)
        assert suffix.font_size == (20 if portrait else 21)
        assert digits.bbox[3] == suffix.bbox[3]
        assert digits.bbox[2] <= suffix.bbox[0]


@pytest.mark.parametrize("layout", LAYOUTS)
def test_tracks_and_sparklines_are_separate_and_within_cards(layout: DashboardLayout) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(_snapshot())
    for key, card in (("cpu", layout.cpu), ("gpu", layout.gpu), ("memory", layout.memory)):
        track = renderer.last_gauges[key].track
        graph = renderer.last_sparklines[key]
        assert track.height >= (8 if layout is PORTRAIT_LAYOUT else 10)
        assert card.x < track.x < track.right < card.right
        assert card.y < track.y < track.bottom < graph.y < graph.bottom < card.bottom
        for placement in renderer.last_placements.values():
            if placement.drawn and placement.clip == card and placement.key not in {f"{key}_gauge_status"}:
                assert placement.bbox[3] <= track.y or placement.bbox[1] >= track.bottom
    assert renderer.last_budget_bar is not None
    assert renderer.last_budget_bar.height >= (8 if layout is PORTRAIT_LAYOUT else 10)


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("used,total,text", [(7.5, 12.0, "VRAM 7.5 / 12.0 GiB"), (None, 12.0, "VRAM -- / -- GiB"), (8.0, None, "VRAM -- / -- GiB"), (16.0, 16.0, "VRAM 16.0 / 16.0 GiB"), (64.0, 128.0, "VRAM 64.0 / 128.0 GiB")])
def test_gpu_vram_is_always_a_dedicated_pair(layout: DashboardLayout, used: float | None, total: float | None, text: str) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(_snapshot(vram=(used, total)))
    assert renderer.last_placements["gpu_vram"].text == text
    assert not renderer.last_placements["gpu_vram"].clipped


@pytest.mark.parametrize("layout", LAYOUTS)
def test_power_is_omitted_when_sensor_unavailable(layout: DashboardLayout) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(replace(_snapshot(), gpu_power_w=Metric(None, "W")))
    assert "gpu_power" not in renderer.last_placements


@pytest.mark.parametrize("layout", LAYOUTS)
def test_long_models_ellipsize_without_moving_values_or_clipping(layout: DashboardLayout) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(replace(_snapshot(), cpu_model="X" * 100, gpu_model="Y" * 100))
    assert renderer.clipping_issues == ()
    assert renderer.last_placements["cpu_model"].text.endswith("...")
    assert renderer.last_placements["gpu_model"].text.endswith("...")
    model = renderer.last_placements["gpu_model"].bbox
    power = renderer.last_placements["gpu_power"].bbox
    assert model[2] <= power[0] or power[2] <= model[0] or model[3] <= power[1] or power[3] <= model[1]
    assert renderer.last_placements["gpu_value"].anchor_xy == (layout.gpu.x + 112, layout.gpu.y + (73 if layout is PORTRAIT_LAYOUT else 80))


@pytest.mark.parametrize("layout", LAYOUTS)
def test_codex_card_keeps_longest_window_primary_and_omits_reset(layout: DashboardLayout) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(_snapshot())
    text = " ".join(item.text for item in renderer.last_placements.values())
    assert renderer.last_placements["ai_primary"].text == "79%"
    assert "7D LEFT" in text
    assert "5H LEFT" in text
    assert "RESET" not in text
    assert renderer.clipping_issues == ()


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("status", [SyncStatus.OK, SyncStatus.DELAYED, SyncStatus.AUTH_ERROR, SyncStatus.NETWORK_ERROR, SyncStatus.SETUP_REQUIRED])
def test_codex_statuses_and_unknown_number_stay_bounded(layout: DashboardLayout, status: SyncStatus) -> None:
    renderer = DashboardRenderer(layout=layout)
    renderer.render(replace(_snapshot(), ai=_codex("--", status=status)))
    assert renderer.last_placements["ai_status"].text == status.value
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
    assert not renderer.last_placements["connection_status"].clipped
    assert not renderer.last_placements["connection_detail"].clipped


def test_demo_error_preview_names_both_demo_and_error() -> None:
    renderer = DashboardRenderer()
    renderer.render(demo_snapshot("ai_error"))
    badge = renderer.last_placements["ai_status"].text
    assert "DEMO" in badge
    assert "AUTH ERROR" in badge
    assert renderer.clipping_issues == ()
