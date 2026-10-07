# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
from pathlib import Path

import pytest

from ai_mini_monitor.models import AIData, AIProviderKind, Metric, SensorSnapshot
from ai_mini_monitor.rendering.renderer import get_overlay_layers
from ai_mini_monitor.ui.overlay import WorkArea, compose_overlay_rgba


def _taskbar():
    return importlib.import_module("ai_mini_monitor.ui.taskbar")


def _sensor(
    cpu: float | None,
    gpu: float | None,
    memory: float | None,
    *,
    vram_used: float | None = None,
    vram_total: float | None = None,
) -> SensorSnapshot:
    unavailable = Metric(None)
    return SensorSnapshot(
        captured_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
        cpu_percent=Metric(cpu, "%"),
        cpu_temperature=unavailable,
        gpu_percent=Metric(gpu, "%"),
        gpu_temperature=unavailable,
        memory_percent=Metric(memory, "%"),
        memory_used_gib=unavailable,
        memory_total_gib=unavailable,
        memory_available_gib=unavailable,
        gpu_vram_used_gib=Metric(vram_used, "GiB"),
        gpu_vram_total_gib=Metric(vram_total, "GiB"),
    )


def _codex(value: str, *, used_ratio: float | None = None) -> AIData:
    return AIData(provider=AIProviderKind.CODEX_ACCOUNT, primary_value=value, budget_ratio=used_ratio)


@pytest.mark.parametrize("value,want", [(None, "--"), (0, "0%"), (47.6, "48%"), (100, "100%"), (180, "100%"), (float("nan"), "--"), (-1, "--")])
def test_percentage_label_never_turns_missing_sensor_into_zero(value: float | None, want: str) -> None:
    taskbar = _taskbar()
    assert taskbar.percent_text(Metric(value, "%")) == want


def test_readouts_order_cpu_ram_gpu_vram_and_codex_remaining() -> None:
    taskbar = _taskbar()
    sensor = _sensor(11, 33, 22, vram_used=6, vram_total=8)
    assert taskbar._readouts(sensor, _codex("76%", used_ratio=0.24)) == ("11%", "22%", "33%", "75%", "76%")


@pytest.mark.parametrize("used,total,want", [
    (None, 8, "--"), (4, None, "--"), (4, 0, "--"),
    (-1, 8, "--"), (9, 8, "--"), (float("nan"), 8, "--"),
    (0, 8, "0%"), (8, 8, "100%"), (3, 8, "38%"),
])
def test_vram_percent_requires_valid_used_and_total(used, total, want) -> None:
    taskbar = _taskbar()
    assert taskbar._readouts(_sensor(None, None, None, vram_used=used, vram_total=total), None)[3] == want


@pytest.mark.parametrize("value,want", [
    ("--", "--"), ("SETUP", "--"), ("76%", "76%"),
    ("0%", "0%"), ("100%", "100%"), ("101%", "--"),
    ("-1%", "--"), ("nan%", "--"),
])
def test_codex_shows_main_remaining_percent_only(value, want) -> None:
    taskbar = _taskbar()
    assert taskbar._readouts(None, _codex(value, used_ratio=0.99))[4] == want


def test_non_codex_ai_percentage_is_not_mislabeled_as_codex() -> None:
    taskbar = _taskbar()
    ai = AIData(provider=AIProviderKind.OPENAI_API, primary_value="31%")
    assert taskbar._readouts(None, ai)[4] == "--"


def test_compact_frame_renders_five_distinct_icon_and_value_regions_without_surface() -> None:
    taskbar = _taskbar()
    unknown = taskbar.render_taskbar_frame(None)
    measured = taskbar.render_taskbar_frame(_sensor(37.0, 52.0, 68.0, vram_used=8, vram_total=8), _codex("100%"))
    assert unknown.size == measured.size == taskbar.FRAME_SIZE == (348, 32)
    assert measured.mode == "RGB"
    unknown_layers = get_overlay_layers(unknown)
    measured_layers = get_overlay_layers(measured)
    assert measured_layers.background.getpixel((40, 16)) == (8, 8, 8)
    assert unknown_layers.surface_mask.getpixel((0, 0)) == 0
    assert measured_layers.surface_mask.getextrema() == (0, 0)
    for index in range(5):
        x0 = index * 72
        icon_region = measured_layers.foreground.crop((x0 + 4, 6, x0 + 22, 26))
        text_unknown = unknown_layers.foreground.crop((x0 + 25, 4, x0 + 71, 28))
        text_measured = measured_layers.foreground.crop((x0 + 25, 4, x0 + 71, 28))
        assert icon_region.getchannel("A").getextrema()[1] >= 250
        assert text_measured.tobytes() != text_unknown.tobytes()
        assert text_measured.getchannel("A").getextrema()[1] == 255
    for boundary in (72, 144, 216, 288):
        assert measured_layers.foreground.getchannel("A").crop((boundary - 2, 0, boundary + 2, 32)).getextrema()[1] == 0
    composed = compose_overlay_rgba(measured, 0.65)
    assert composed.getpixel((0, 0))[3] == 0
    assert composed.getpixel((40, 2))[3] == 0
    assert composed.getchannel("A").getextrema()[1] == 255


def test_hardware_icon_generator_keeps_approved_codex_asset_unchanged(tmp_path, monkeypatch) -> None:
    from PIL import Image

    script = Path(__file__).resolve().parents[1] / "scripts" / "generate_taskbar_icons.py"
    spec = importlib.util.spec_from_file_location("generate_taskbar_icons", script)
    assert spec is not None and spec.loader is not None
    generate_taskbar_icons = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generate_taskbar_icons)

    monkeypatch.setattr(generate_taskbar_icons, "OUT", tmp_path)
    codex_asset = tmp_path / "codex.png"
    Image.new("RGBA", (32, 32), (0, 0, 0, 0)).save(codex_asset)
    preserved = codex_asset.read_bytes()
    generate_taskbar_icons.main()
    seen: set[bytes] = set()
    for name in ("cpu", "ram", "gpu", "vram"):
        with Image.open(tmp_path / f"{name}.png") as icon:
            assert icon.size == (32, 32)
            assert icon.mode == "RGBA"
            assert icon.getpixel((0, 0))[3] == 0
            assert icon.getchannel("A").getextrema()[1] == 255
            seen.add(icon.tobytes())
    assert len(seen) == 4
    assert codex_asset.read_bytes() == preserved


def test_codex_metric_asset_is_the_approved_unmodified_black_alpha_source() -> None:
    taskbar = _taskbar()
    asset = taskbar.resource_path("assets/icons/codex.png")
    assert hashlib.sha256(asset.read_bytes()).hexdigest() == "2e253debb9dce8a717a3c38711fd169ef407acdf2901647128c0f528dbbff818"


def test_codex_metric_icon_tints_black_source_white_preserving_alpha(tmp_path, monkeypatch) -> None:
    from PIL import Image

    taskbar = _taskbar()
    source = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    source.putpixel((16, 16), (0, 0, 0, 128))
    source.putpixel((16, 17), (0, 0, 0, 255))
    path = tmp_path / "codex.png"
    source.save(path)
    monkeypatch.setattr(taskbar, "resource_path", lambda _name: path)
    taskbar._icon.cache_clear()
    try:
        icon = taskbar._icon("codex")
        expected_alpha = source.getchannel("A").resize((18, 18), Image.Resampling.LANCZOS)
        assert icon.getchannel("A").tobytes() == expected_alpha.tobytes()
        assert all(icon.getpixel((x, y))[:3] == (255, 255, 255)
                   for y in range(18) for x in range(18) if icon.getpixel((x, y))[3] > 0)
    finally:
        taskbar._icon.cache_clear()


def test_default_position_uses_horizontal_tray_left_and_desktop_fallback() -> None:
    taskbar = _taskbar()
    primary = WorkArea(0, 0, 1920, 1080)
    desktop = WorkArea(0, 0, 1920, 1040)
    tray = WorkArea(0, 1040, 1920, 1080)
    notify = WorkArea(1680, 1040, 1920, 1080)
    assert taskbar.choose_initial_position(primary, desktop, (tray, notify)) == (1330, 1044)
    assert taskbar.choose_initial_position(primary, desktop, None) == (1556, 992)
    vertical = WorkArea(1880, 0, 1920, 1080)
    assert taskbar.choose_initial_position(primary, desktop, (vertical, notify)) == (1556, 992)


def test_full_monitor_provider_preserves_negative_monitor_bounds(monkeypatch) -> None:
    taskbar = _taskbar()
    expected = (WorkArea(-1920, 0, 0, 1080), WorkArea(0, 0, 1920, 1080))
    monkeypatch.setattr(taskbar, "windows_monitor_bounds", lambda: expected)
    assert taskbar.full_monitor_areas(object()) == expected


class _FakeWindow:
    def __init__(self, _root, *, class_: str) -> None:
        self.class_name = class_
        self.title_text = ""
        self.attributes_set: dict[str, object] = {}
        self.bindings: dict[str, object] = {}
        self.geometry_value = ""
        self.focused = False
        self.destroyed = False

    def title(self, value: str) -> None: self.title_text = value
    def configure(self, **_kwargs) -> None: pass
    def overrideredirect(self, _value: bool) -> None: pass
    def attributes(self, key: str, value: object) -> None: self.attributes_set[key] = value
    def protocol(self, _name: str, _callback) -> None: pass
    def bind(self, name: str, callback, *, add: str) -> None: self.bindings[name] = callback
    def geometry(self, value: str) -> None: self.geometry_value = value
    def withdraw(self) -> None: pass
    def deiconify(self) -> None: pass
    def lift(self) -> None: pass
    def update_idletasks(self) -> None: pass
    def destroy(self) -> None: self.destroyed = True
    def focus_set(self) -> None: self.focused = True


class _FakePresenter:
    def __init__(self, _window) -> None:
        self.frames: list[tuple[object, int, int]] = []

    def present(self, image, *, x: int, y: int) -> None:
        self.frames.append((image.copy(), x, y))


def _fake_taskbar(monkeypatch):
    taskbar = _taskbar()
    windows: list[_FakeWindow] = []
    presenters: list[_FakePresenter] = []

    def window_factory(root, *, class_):
        window = _FakeWindow(root, class_=class_)
        windows.append(window)
        return window

    def presenter_factory(window):
        presenter = _FakePresenter(window)
        presenters.append(presenter)
        return presenter

    monkeypatch.setattr(taskbar.tk, "Toplevel", window_factory)
    monkeypatch.setattr(taskbar, "LayeredWindowPresenter", presenter_factory)
    monkeypatch.setattr(taskbar, "full_monitor_areas", lambda _root: (WorkArea(-1920, 0, 0, 1080), WorkArea(0, 0, 1920, 1080)))
    return taskbar, windows, presenters


def test_taskbar_window_uses_existing_overlay_lifecycle_without_stealing_focus(monkeypatch) -> None:
    taskbar, windows, presenters = _fake_taskbar(monkeypatch)

    window = taskbar.TaskbarWindow(object(), position=(-1800, 1040))
    assert window.state.width == 348
    assert window.state.height == 32
    assert window.visible is False
    window.show(notify=False)
    window.update_sensor(_sensor(0, 100, None))
    assert window.visible is True
    assert window.state.opacity == 0
    assert windows[0].attributes_set["-topmost"] is True
    assert windows[0].title_text == "Mini Monitor · 작업 표시줄 바"
    assert windows[0].focused is False
    assert presenters[-1].frames[-1][1:] == (-1800, 1040)
    window.hide(notify=False)
    assert window.visible is False
    window.update_sensor(None)
    window.destroy()
    assert window.closed is True
    assert windows[0].destroyed is True
    with pytest.raises(RuntimeError, match="closed"):
        window.update_sensor(None)


def test_unchanged_sensor_readings_do_not_represent_native_window(monkeypatch) -> None:
    taskbar, _windows, presenters = _fake_taskbar(monkeypatch)
    window = taskbar.TaskbarWindow(object(), position=(-1800, 1040))
    window.show(notify=False)
    window.update_sensor(_sensor(30, 40, 50))
    before = len(presenters[-1].frames)
    window.update_sensor(_sensor(30, 40, 50))
    assert len(presenters[-1].frames) == before
    window.update_sensor(None)
    assert len(presenters[-1].frames) == before + 1
    window.update_sensor(None)
    assert len(presenters[-1].frames) == before + 1


def test_codex_main_remaining_change_refreshes_cached_frame(monkeypatch) -> None:
    taskbar, _windows, presenters = _fake_taskbar(monkeypatch)
    window = taskbar.TaskbarWindow(object(), position=(-1800, 1040))
    window.show(notify=False)
    sensor = _sensor(30, 40, 50, vram_used=2, vram_total=8)
    window.update_sensor(sensor, _codex("76%", used_ratio=0.24))
    before = len(presenters[-1].frames)
    window.update_sensor(sensor, _codex("76%", used_ratio=0.33))
    assert len(presenters[-1].frames) == before
    window.update_sensor(sensor, _codex("75%", used_ratio=0.25))
    assert len(presenters[-1].frames) == before + 1


@pytest.mark.parametrize("style,cell_width", [("icon", 72), ("text", 86), ("both", 108)])
@pytest.mark.parametrize("count", [1, 2, 3, 4, 5])
def test_selected_taskbar_items_render_dynamic_width_and_opaque_readout(style: str, cell_width: int, count: int) -> None:
    taskbar = _taskbar()
    items = ("cpu", "ram", "gpu", "vram", "codex")[:count]
    frame = taskbar.render_taskbar_frame(_sensor(100, 100, 100, vram_used=8, vram_total=8), _codex("100%"), items=items, style=style)
    assert frame.size == (count * cell_width - (12 if style == "icon" else 0), 32)
    layers = get_overlay_layers(frame)
    assert layers.surface_mask.getextrema() == (0, 0)
    assert layers.foreground.getchannel("A").getextrema()[1] == 255
    assert compose_overlay_rgba(frame, 0.0).getpixel((0, 0))[3] == 0


def test_saved_or_dragged_taskbar_position_stays_left_of_primary_notification_area() -> None:
    taskbar = _taskbar()
    primary = WorkArea(0, 0, 1920, 1080)
    bar = WorkArea(0, 1032, 1920, 1080)
    notify = WorkArea(1678, 1032, 1920, 1080)
    clamp = getattr(taskbar, "clamp_taskbar_position", None)
    assert callable(clamp)
    assert clamp((1408, 1040), (360, 32), primary, (bar, notify)) == (1316, 1040)
    assert clamp((1660, 1040), (360, 32), primary, (bar, notify)) == (1316, 1040)
    assert clamp((1200, 1040), (360, 32), primary, (bar, notify)) == (1200, 1040)
    assert clamp((1660, 800), (360, 32), primary, (bar, notify)) == (1660, 800)
    assert clamp((1980, 1040), (360, 32), primary, (bar, notify)) == (1980, 1040)


def test_narrow_taskbar_moves_above_or_below_when_bar_cannot_fit_left_of_notification_area() -> None:
    taskbar = _taskbar()
    primary = WorkArea(0, 0, 800, 1080)
    bottom_bar = WorkArea(0, 1032, 800, 1080)
    bottom_notify = WorkArea(430, 1032, 800, 1080)
    assert taskbar.clamp_taskbar_position((260, 1040), (540, 32), primary, (bottom_bar, bottom_notify)) == (260, 984)
    top_bar = WorkArea(0, 0, 800, 48)
    top_notify = WorkArea(430, 0, 800, 48)
    assert taskbar.clamp_taskbar_position((260, 8), (540, 32), primary, (top_bar, top_notify)) == (260, 64)


def test_taskbar_window_option_change_resizes_and_refreshes_cached_frame(monkeypatch) -> None:
    taskbar, _windows, presenters = _fake_taskbar(monkeypatch)
    bar = WorkArea(0, 1032, 1920, 1080)
    notify = WorkArea(1678, 1032, 1920, 1080)
    monkeypatch.setattr(taskbar, "full_monitor_areas", lambda _root: (WorkArea(0, 0, 1920, 1080),))
    monkeypatch.setattr(taskbar, "windows_taskbar_rects", lambda: (bar, notify))
    window = taskbar.TaskbarWindow(object(), position=(1408, 1040), items=("cpu", "ram", "gpu"), style="icon")
    assert window.state.width == 204
    assert window.state.x == 1408
    window.show(notify=False)
    sensor = _sensor(100, 100, 100)
    window.update_sensor(sensor)
    before = len(presenters[-1].frames)
    window.set_options(("cpu", "ram", "gpu", "vram", "codex"), "both")
    assert window.state.width == 540
    assert window.state.x == 1136
    assert len(presenters[-1].frames) > before
    assert presenters[-1].frames[-1][0].size == (540, 32)


def test_hidden_item_sensor_change_is_used_when_item_is_later_selected(monkeypatch) -> None:
    taskbar, _windows, _presenters = _fake_taskbar(monkeypatch)
    window = taskbar.TaskbarWindow(object(), position=(-1800, 1040), items=("cpu",))
    window.update_sensor(_sensor(30, 40, 50))
    window.update_sensor(_sensor(30, 90, 50))
    window.set_options(("gpu",), "icon")
    assert window._last_readouts[2] == ("90%",)


def test_horizontal_controls_use_safe_primary_tray_range_without_changing_y(monkeypatch) -> None:
    taskbar, _windows, _presenters = _fake_taskbar(monkeypatch)
    primary = WorkArea(0, 0, 1920, 1080)
    bar = WorkArea(0, 1032, 1920, 1080)
    notify = WorkArea(1678, 1032, 1920, 1080)
    monkeypatch.setattr(taskbar, "full_monitor_areas", lambda _root: (primary,))
    monkeypatch.setattr(taskbar, "windows_taskbar_rects", lambda: (bar, notify))
    window = taskbar.TaskbarWindow(object(), position=(1328, 1040))
    assert window.horizontal_position() == (100.0, 1328)
    window.set_horizontal_percent(0, notify=False)
    assert (window.state.x, window.state.y) == (0, 1040)
    window.nudge_horizontal(1, notify=False)
    assert (window.state.x, window.state.y) == (1, 1040)
    window.reset_horizontal_position(notify=False)
    assert (window.state.x, window.state.y) == (1328, 1040)


def test_horizontal_controls_follow_current_secondary_monitor(monkeypatch) -> None:
    taskbar, _windows, _presenters = _fake_taskbar(monkeypatch)
    primary = WorkArea(0, 0, 1920, 1080)
    secondary = WorkArea(1920, 0, 3840, 1080)
    monkeypatch.setattr(taskbar, "full_monitor_areas", lambda _root: (primary, secondary))
    monkeypatch.setattr(taskbar, "windows_taskbar_rects", lambda: (WorkArea(0, 1032, 1920, 1080), WorkArea(1678, 1032, 1920, 1080)))
    window = taskbar.TaskbarWindow(object(), position=(2000, 1040))
    window.set_horizontal_percent(100, notify=False)
    assert (window.state.x, window.state.y) == (3492, 1040)
    window.nudge_horizontal(-1, notify=False)
    assert (window.state.x, window.state.y) == (3491, 1040)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 100.1, True])
def test_horizontal_percent_rejects_invalid_input(monkeypatch, value) -> None:
    taskbar, _windows, _presenters = _fake_taskbar(monkeypatch)
    window = taskbar.TaskbarWindow(object(), position=(-1800, 1040))
    with pytest.raises(ValueError):
        window.set_horizontal_percent(value, notify=False)
