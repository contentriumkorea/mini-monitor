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


@pytest.mark.parametrize('dpi,size,font_size,icon_size', [
    (100, (348, 32), 14, 18), (150, (522, 48), 21, 27),
    (200, (696, 64), 28, 36), (250, (870, 80), 35, 45),
])
def test_taskbar_renderer_draws_at_native_dpi(monkeypatch, dpi, size, font_size, icon_size):
    taskbar = _taskbar()
    fonts, icons = [], []
    real_font, real_icon = taskbar.metric_mono, taskbar._icon
    def font_spy(size):
        fonts.append(size)
        return real_font(size)
    def icon_spy(*args, **kwargs):
        result = real_icon(*args, **kwargs)
        icons.append(result.size)
        return result
    monkeypatch.setattr(taskbar, 'metric_mono', font_spy)
    monkeypatch.setattr(taskbar, '_icon', icon_spy)
    frame = taskbar.render_taskbar_frame(_sensor(100, 100, 100), _codex('100%'), dpi_percent=dpi)
    assert frame.size == taskbar.taskbar_frame_size(dpi_percent=dpi) == size
    assert font_size in fonts
    assert icons == [(icon_size, icon_size)] * 5
    rgba = compose_overlay_rgba(frame, 0)
    assert rgba.getpixel((0, 0))[3] == 0
    assert rgba.getchannel('A').getextrema()[1] == 255


@pytest.mark.parametrize('dpi', [150, 200, 250])
@pytest.mark.parametrize('style', ['icon', 'text', 'both'])
@pytest.mark.parametrize('value', ['60,518 Credit', '999,999,999 Credit'])
def test_high_dpi_credit_text_not_clipped(dpi, style, value):
    taskbar = _taskbar()
    frame = taskbar.render_taskbar_frame(None, _codex(value), items=('codex',), style=style, dpi_percent=dpi)
    alpha = get_overlay_layers(frame).foreground.getchannel('A')
    assert alpha.getbbox()[2] < frame.width
    assert alpha.getbbox()[3] < frame.height
    assert alpha.getextrema()[1] == 255
    assert frame.width > 108 * dpi / 100


@pytest.mark.parametrize('dpi', [True, 99, 801, 150.5, float('nan'), None])
def test_renderer_rejects_invalid_dpi(dpi):
    with pytest.raises(ValueError, match='DPI'):
        _taskbar().render_taskbar_frame(None, dpi_percent=dpi)


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


@pytest.mark.parametrize("style", ["icon", "text", "both"])
@pytest.mark.parametrize("balance,want", [("60518.43", "60,518 Credit"), ("123456789012.25", "123,456,789,012 Credit")])
def test_taskbar_credit_label_expands_without_truncation(style, balance, want):
    from ai_mini_monitor.ai.codex_account import CodexLimitWindow, _display
    from ai_mini_monitor.rendering.fonts import metric_mono

    taskbar = _taskbar()
    ai = _display("ready", (CodexLimitWindow(10080, 100, None),), credit_balance=balance)
    assert taskbar._readouts(None, ai)[4] == want
    frame = taskbar.render_taskbar_frame(None, ai, items=("cpu", "codex"), style=style)
    font = metric_mono(14 if style == "icon" else 13)
    prefix = {"icon": 26, "text": 4 + metric_mono(10).getlength("CODEX") + 8,
              "both": 26 + metric_mono(10).getlength("CODEX") + 8}[style]
    assert frame.width >= taskbar._STYLE_CELL_WIDTHS[style] + prefix + font.getlength(want) + 4
    alpha = get_overlay_layers(frame).foreground.getchannel("A")
    assert alpha.getbbox()[2] < frame.width


@pytest.mark.parametrize("balance", ["0", None])
def test_taskbar_without_credit_balance_keeps_zero_percent_and_compact_width(balance):
    from ai_mini_monitor.ai.codex_account import CodexLimitWindow, _display

    taskbar = _taskbar()
    ai = _display("ready", (CodexLimitWindow(10080, 100, None),), credit_balance=balance)
    assert taskbar._readouts(None, ai)[4] == "0%"
    assert taskbar.render_taskbar_frame(None, ai).size == (348, 32)


def test_credit_balance_and_reset_refresh_cached_taskbar_frame(monkeypatch):
    from ai_mini_monitor.ai.codex_account import CodexLimitWindow, _display

    taskbar, _windows, presenters = _fake_taskbar(monkeypatch)
    window = taskbar.TaskbarWindow(object(), position=(-1800, 1040))
    window.show(notify=False)
    def reading(used, balance):
        return _display("ready", (CodexLimitWindow(10080, used, None),), credit_balance=balance)
    window.update_sensor(None, reading(100, "60518"))
    before = len(presenters[-1].frames)
    credit_width = window.state.width
    window.update_sensor(None, reading(100, "60517"))
    assert len(presenters[-1].frames) == before + 1
    window.update_sensor(None, reading(15, "60517"))
    assert len(presenters[-1].frames) == before + 2
    assert window.state.width < credit_width


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


def test_drag_defers_ui_and_disk_callbacks_and_coalesces_mouse_moves(monkeypatch):
    taskbar, windows, _presenters = _fake_taskbar(monkeypatch)
    saved = []
    window = taskbar.TaskbarWindow(object(), position=(-1500, 1040), on_state_change=saved.append)
    window.show(notify=False)
    before = window.state.x
    saved.clear()
    for _ in range(20):
        windows[0].on_drag(1)
    assert saved == []  # Never enter Tk or save configuration inside WNDPROC.
    window.update_sensor(None)
    assert window.state.x == before + 20
    assert len(saved) == 1
    window.update_sensor(None)
    assert len(saved) == 1


def test_hiding_or_explicit_position_change_discards_queued_drag(monkeypatch):
    taskbar, windows, _presenters = _fake_taskbar(monkeypatch)
    window = taskbar.TaskbarWindow(object(), position=(-1500, 1040))
    window.show(notify=False)
    windows[0].on_drag(20)
    window.hide(notify=False)
    window.show(notify=False)
    window.update_sensor(None)
    assert window.state.x == -1500
    windows[0].on_drag(20)
    window.set_position(-1400, 1040, notify=False)
    window.update_sensor(None)
    assert window.state.x == -1400


class _FakeWindow:
    def __init__(self, *, on_drag) -> None:
        self.on_drag = on_drag
        self.hwnd = 999
        self.mapped = False
        self.destroyed = False

    def show(self) -> None: self.mapped = True
    def hide(self) -> None: self.mapped = False
    def winfo_id(self) -> int: return self.hwnd
    def destroy(self) -> None: self.destroyed = True


class _FakePresenter:
    def __init__(self, _window) -> None:
        self.window = _window
        self.frames: list[tuple[object, int, int]] = []
        self.hwnd = 999

    def present(self, image, *, x: int, y: int) -> None:
        self.frames.append((image.copy(), x, y))

def _fake_taskbar(monkeypatch):
    taskbar = _taskbar()
    windows: list[_FakeWindow] = []
    presenters: list[_FakePresenter] = []

    def window_factory(*, on_drag):
        window = _FakeWindow(on_drag=on_drag)
        windows.append(window)
        return window

    def presenter_factory(window, *, no_activate=False):
        presenter = _FakePresenter(window)
        presenters.append(presenter)
        return presenter

    monkeypatch.setattr(taskbar, "NativeTaskbarSurface", window_factory, raising=False)
    monkeypatch.setattr(taskbar, "LayeredWindowPresenter", presenter_factory)
    monkeypatch.setattr(taskbar, "restore_taskbar_z_order", lambda _hwnd: False)
    monkeypatch.setattr(taskbar, "taskbar_dpi_percent", lambda: 100, raising=False)
    monkeypatch.setattr(taskbar, "taskbar_should_be_visible", lambda _hwnd: True, raising=False)
    monkeypatch.setattr(taskbar, "full_monitor_areas", lambda _root: (WorkArea(-1920, 0, 0, 1080), WorkArea(0, 0, 1920, 1080)))
    monkeypatch.setattr(taskbar, "windows_taskbar_rects", lambda: (WorkArea(-1920, 1032, 0, 1080), WorkArea(-242, 1032, 0, 1080)))
    return taskbar, windows, presenters


@pytest.mark.parametrize('scale,size,y', [(150, (522, 48), 2076), (200, (696, 64), 2068), (250, (870, 80), 2060)])
def test_taskbar_scales_with_shell_dpi_in_physical_4k_bounds(monkeypatch, scale, size, y):
    taskbar, _windows, presenters = _fake_taskbar(monkeypatch)
    monkeypatch.setattr(taskbar, 'taskbar_dpi_percent', lambda: scale)
    monkeypatch.setattr(taskbar, 'full_monitor_areas', lambda _root: (WorkArea(0, 0, 3840, 2160),))
    monkeypatch.setattr(taskbar, 'windows_taskbar_rects', lambda: (WorkArea(0, 2052, 3840, 2148), WorkArea(3400, 2052, 3840, 2148)))
    window = taskbar.TaskbarWindow(object(), position=(0, 0))
    window.show(notify=False)
    assert (window.state.width, window.state.height) == size
    assert window.state.x == 3400 - size[0] - 2
    assert window.state.y == y
    assert presenters[-1].frames[-1][0].size == size


def test_fullscreen_suspends_actual_window_but_preserves_enabled_state_and_resumes_cached_pixels(monkeypatch):
    taskbar, windows, presenters = _fake_taskbar(monkeypatch)
    can_show = [True]
    monkeypatch.setattr(taskbar, 'taskbar_should_be_visible', lambda _hwnd: can_show[0])
    changes = []
    window = taskbar.TaskbarWindow(object(), position=(-1800, 1040), on_state_change=changes.append)
    sensor = _sensor(30, 40, 50)
    window.show()
    window.update_sensor(sensor)
    can_show[0] = False
    window.update_sensor(sensor)
    assert window.visible is True  # Logical ON remains saved; only presentation is hidden.
    assert windows[0].mapped is False
    before = len(presenters[-1].frames)
    window.update_sensor(_sensor(50, 50, 50))
    assert len(presenters[-1].frames) == before
    can_show[0] = True
    window.update_sensor(_sensor(50, 50, 50))
    assert windows[0].mapped is True
    assert len(presenters[-1].frames) > before
    assert all(state.visible for state in changes)
    window.hide()
    can_show[0] = False
    window.update_sensor(sensor)
    can_show[0] = True
    window.update_sensor(sensor)
    assert window.visible is False
    assert windows[0].mapped is False


def test_live_dpi_change_preserves_relative_horizontal_position_and_repaints_same_values(monkeypatch):
    taskbar, _windows, presenters = _fake_taskbar(monkeypatch)
    dpi = [100]
    areas = [WorkArea(0, 0, 1920, 1080)]
    shell = [WorkArea(0, 1032, 1920, 1080), WorkArea(1678, 1032, 1920, 1080)]
    monkeypatch.setattr(taskbar, 'taskbar_dpi_percent', lambda: dpi[0])
    monkeypatch.setattr(taskbar, 'full_monitor_areas', lambda _root: tuple(areas))
    monkeypatch.setattr(taskbar, 'windows_taskbar_rects', lambda: tuple(shell))
    window = taskbar.TaskbarWindow(object(), position=(664, 1040))
    window.show(notify=False)
    window.update_sensor(None)
    dpi[0] = 200
    areas[:] = [WorkArea(0, 0, 3840, 2160)]
    shell[:] = [WorkArea(0, 2064, 3840, 2160), WorkArea(3400, 2064, 3840, 2160)]
    window.update_sensor(None)
    assert (window.state.x, window.state.y, window.state.width, window.state.height) == (1351, 2080, 696, 64)
    assert window.horizontal_position()[0] == 50
    assert presenters[-1].frames[-1][0].size == (696, 64)


def test_enabled_during_fullscreen_waits_without_flash_or_changing_saved_switch(monkeypatch):
    taskbar, windows, presenters = _fake_taskbar(monkeypatch)
    can_show = [False]
    monkeypatch.setattr(taskbar, 'taskbar_should_be_visible', lambda _hwnd: can_show[0])
    window = taskbar.TaskbarWindow(object(), position=(-1800, 1040))
    window.show()
    window.update_sensor(None)
    assert window.state.visible is True
    assert windows[0].mapped is False
    assert not any(p.frames for p in presenters)
    can_show[0] = True
    window.update_sensor(None)
    assert window.state.visible is True
    assert windows[0].mapped is True
    assert presenters[-1].frames


def test_lost_tray_geometry_suspends_instead_of_becoming_desktop_overlay(monkeypatch):
    taskbar, windows, _presenters = _fake_taskbar(monkeypatch)
    shell = [(WorkArea(-1920, 1032, 0, 1080), WorkArea(-242, 1032, 0, 1080))]
    monkeypatch.setattr(taskbar, 'windows_taskbar_rects', lambda: shell[0])
    window = taskbar.TaskbarWindow(object(), position=(-590, 1040))
    window.show(notify=False)
    shell[0] = None
    window.update_sensor(None)
    assert window.visible is True
    assert windows[0].mapped is False
    shell[0] = (WorkArea(-1920, 1032, 0, 1080), WorkArea(-242, 1032, 0, 1080))
    window.update_sensor(None)
    assert windows[0].mapped is True


@pytest.mark.parametrize('bar,notify', [
    (WorkArea(0, 984, 1920, 1080), WorkArea(650, 984, 1920, 1080)),
    (WorkArea(0, 1060, 1920, 1080), WorkArea(1600, 1060, 1920, 1080)),
])
def test_no_usable_taskbar_surface_never_floats_above_apps(monkeypatch, bar, notify):
    taskbar, windows, presenters = _fake_taskbar(monkeypatch)
    monkeypatch.setattr(taskbar, 'taskbar_dpi_percent', lambda: 200)
    monkeypatch.setattr(taskbar, 'full_monitor_areas', lambda _root: (WorkArea(0, 0, 1920, 1080),))
    monkeypatch.setattr(taskbar, 'windows_taskbar_rects', lambda: (bar, notify))
    window = taskbar.TaskbarWindow(object(), position=(500, 990))
    window.show(notify=False)
    window.update_sensor(None)
    assert window.visible is True
    assert windows[0].mapped is False
    assert not any(p.frames for p in presenters)


def test_taskbar_native_lifecycle_releases_surface(monkeypatch) -> None:
    taskbar, windows, presenters = _fake_taskbar(monkeypatch)

    window = taskbar.TaskbarWindow(object(), position=(-1800, 1040))
    assert window.state.width == 348
    assert window.state.height == 32
    assert window.visible is False
    window.show(notify=False)
    window.update_sensor(_sensor(0, 100, None))
    assert window.visible is True
    assert window.state.opacity == 0
    assert windows[0].mapped is True
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


def test_cached_readings_restore_shell_order_without_redraw_or_focus(monkeypatch) -> None:
    taskbar, windows, presenters = _fake_taskbar(monkeypatch)
    stack = [999, 111]

    def restore(hwnd):
        assert hwnd == 999
        if stack == [111, 999]:
            stack[:] = [999, 111]

    monkeypatch.setattr(taskbar, "restore_taskbar_z_order", restore, raising=False)
    window = taskbar.TaskbarWindow(object(), position=(-1800, 1040))
    window.show(notify=False)
    sensor = _sensor(30, 40, 50)
    window.update_sensor(sensor)
    before = len(presenters[-1].frames)
    stack[:] = [111, 999]  # Explorer promotes its topmost taskbar after a click.
    window.update_sensor(sensor)
    assert stack == [999, 111]
    assert len(presenters[-1].frames) == before
    window.hide(notify=False)
    stack[:] = [111, 999]
    window.update_sensor(sensor)
    assert stack == [111, 999]  # An explicitly hidden bar must stay hidden.


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


def test_narrow_taskbar_never_moves_above_or_below_apps() -> None:
    taskbar = _taskbar()
    primary = WorkArea(0, 0, 800, 1080)
    bottom_bar = WorkArea(0, 1032, 800, 1080)
    bottom_notify = WorkArea(430, 1032, 800, 1080)
    assert taskbar.clamp_taskbar_position((260, 1040), (540, 32), primary, (bottom_bar, bottom_notify)) == (260, 1040)
    top_bar = WorkArea(0, 0, 800, 48)
    top_notify = WorkArea(430, 0, 800, 48)
    assert taskbar.clamp_taskbar_position((260, 8), (540, 32), primary, (top_bar, top_notify)) == (260, 8)


def test_hidden_credit_and_options_resume_latest_frame_or_remain_suppressed(monkeypatch):
    taskbar, windows, presenters = _fake_taskbar(monkeypatch)
    allowed = [True]
    rects = [WorkArea(-1920, 1032, 0, 1080), WorkArea(-242, 1032, 0, 1080)]
    monkeypatch.setattr(taskbar, 'taskbar_should_be_visible', lambda _hwnd: allowed[0])
    monkeypatch.setattr(taskbar, 'windows_taskbar_rects', lambda: tuple(rects))
    window = taskbar.TaskbarWindow(object(), position=(-1800, 1040))
    window.show(notify=False)
    window.set_horizontal_percent(50, notify=False)
    allowed[0] = False
    window.update_sensor(None, _codex('999,999,999 Credit'))
    window.set_options(('cpu', 'codex'), 'both')
    rects[1] = WorkArea(-1800, 1032, 0, 1080)
    allowed[0] = True
    window.update_sensor(None, _codex('60,518 Credit'))
    assert not windows[0].mapped and window.visible
    rects[1] = WorkArea(-242, 1032, 0, 1080)
    window.update_sensor(None, _codex('60,518 Credit'))
    assert windows[0].mapped
    assert window._last_readouts[2] == ('--', '60,518 Credit')
    assert presenters[-1].frames[-1][0].width == window.state.width


def test_drag_clamps_x_and_keeps_taskbar_y(monkeypatch):
    taskbar, windows, _presenters = _fake_taskbar(monkeypatch)
    window = taskbar.TaskbarWindow(object(), position=(-1800, 1040))
    window.show(notify=False)
    windows[0].on_drag(-99999)
    window.update_sensor(None)
    assert (window.state.x, window.state.y) == (-1920, 1040)
    windows[0].on_drag(99999)
    window.update_sensor(None)
    assert (window.state.x, window.state.y) == (-592, 1040)


@pytest.mark.parametrize('left,initial_x,want_x', [(0, 664, 490), (-1920, -1256, -1430)])
def test_dpi_change_while_shell_missing_preserves_last_valid_fraction(monkeypatch, left, initial_x, want_x):
    taskbar, windows, _presenters = _fake_taskbar(monkeypatch)
    shell = (WorkArea(left, 984, left + 1920, 1080), WorkArea(left + 1678, 984, left + 1920, 1080))
    rects, dpi = [shell], [100]
    monkeypatch.setattr(taskbar, 'full_monitor_areas', lambda _root: (WorkArea(left, 0, left + 1920, 1080),))
    monkeypatch.setattr(taskbar, 'windows_taskbar_rects', lambda: rects[0])
    monkeypatch.setattr(taskbar, 'taskbar_dpi_percent', lambda: dpi[0])
    window = taskbar.TaskbarWindow(object(), position=(initial_x, 1016))
    window.show(notify=False)
    assert window.horizontal_position() == (50.0, initial_x)
    rects[0] = None
    dpi[0] = 200
    window.update_sensor(None)
    assert not windows[0].mapped and window.visible
    rects[0] = shell
    window.update_sensor(None)
    assert window.state.width == 696
    assert window.horizontal_position() == (50.0, want_x)
    assert windows[0].mapped


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
    monkeypatch.setattr(taskbar, "windows_taskbar_rects", lambda: None)
    window = taskbar.TaskbarWindow(object(), position=(2000, 1040))
    window.set_horizontal_percent(100, notify=False)
    assert (window.state.x, window.state.y) == (3492, 1040)
    window.nudge_horizontal(-1, notify=False)
    assert (window.state.x, window.state.y) == (3491, 1040)


def test_saved_old_resolution_position_docks_to_real_taskbar_on_start(monkeypatch) -> None:
    taskbar, _windows, _presenters = _fake_taskbar(monkeypatch)
    primary = WorkArea(0, 0, 3840, 2160)
    bar = WorkArea(0, 2112, 3840, 2160)
    notify = WorkArea(3598, 2112, 3840, 2160)
    monkeypatch.setattr(taskbar, "full_monitor_areas", lambda _root: (primary,))
    monkeypatch.setattr(taskbar, "windows_taskbar_rects", lambda: (bar, notify))
    window = taskbar.TaskbarWindow(object(), position=(1296, 1040))
    assert (window.state.x, window.state.y) == (3248, 2120)


def test_live_display_change_reanchors_even_when_sensor_readings_are_unchanged(monkeypatch) -> None:
    taskbar, _windows, _presenters = _fake_taskbar(monkeypatch)
    areas = [WorkArea(0, 0, 1920, 1080)]
    shell = [WorkArea(0, 1032, 1920, 1080), WorkArea(1678, 1032, 1920, 1080)]
    monkeypatch.setattr(taskbar, "full_monitor_areas", lambda _root: tuple(areas))
    monkeypatch.setattr(taskbar, "windows_taskbar_rects", lambda: tuple(shell))
    changed = []
    window = taskbar.TaskbarWindow(object(), position=(1328, 1040), on_state_change=changed.append)
    sample = _sensor(30, 40, 50)
    window.update_sensor(sample)
    areas[:] = [WorkArea(0, 0, 3840, 2160)]
    shell[:] = [WorkArea(0, 2112, 3840, 2160), WorkArea(3598, 2112, 3840, 2160)]
    window.update_sensor(sample)
    assert (window.state.x, window.state.y) == (3248, 2120)
    assert (changed[-1].x, changed[-1].y) == (3248, 2120)


def test_shell_taskbar_move_preserves_horizontal_choice_and_snaps_vertical_drag(monkeypatch) -> None:
    taskbar, _windows, _presenters = _fake_taskbar(monkeypatch)
    primary = WorkArea(0, 0, 1920, 1080)
    shell = [WorkArea(0, 1032, 1920, 1080), WorkArea(1678, 1032, 1920, 1080)]
    monkeypatch.setattr(taskbar, "full_monitor_areas", lambda _root: (primary,))
    monkeypatch.setattr(taskbar, "windows_taskbar_rects", lambda: tuple(shell))
    window = taskbar.TaskbarWindow(object(), position=(664, 1040))
    shell[:] = [WorkArea(0, 0, 1920, 48), WorkArea(1678, 0, 1920, 48)]
    window.update_sensor(None)
    assert (window.state.x, window.state.y) == (664, 8)
    window.set_position(700, 500, notify=False)
    assert (window.state.x, window.state.y) == (700, 8)


def test_primary_monitor_relocation_and_tray_resize_use_live_shell_bounds(monkeypatch) -> None:
    taskbar, _windows, presenters = _fake_taskbar(monkeypatch)
    areas = [WorkArea(0, 0, 1920, 1080)]
    shell = [WorkArea(0, 1032, 1920, 1080), WorkArea(1678, 1032, 1920, 1080)]
    monkeypatch.setattr(taskbar, "full_monitor_areas", lambda _root: tuple(areas))
    monkeypatch.setattr(taskbar, "windows_taskbar_rects", lambda: tuple(shell))
    window = taskbar.TaskbarWindow(object(), position=(1328, 1040))
    window.show(notify=False)
    areas[:] = [WorkArea(-1920, -200, 0, 880)]
    shell[:] = [WorkArea(-1920, 832, 0, 880), WorkArea(-400, 832, 0, 880)]
    window.update_sensor(None)
    assert (window.state.x, window.state.y) == (-750, 840)
    assert presenters[-1].frames[-1][1:] == (-750, 840)
    assert window.horizontal_position() == (100.0, -750)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 100.1, True])
def test_horizontal_percent_rejects_invalid_input(monkeypatch, value) -> None:
    taskbar, _windows, _presenters = _fake_taskbar(monkeypatch)
    window = taskbar.TaskbarWindow(object(), position=(-1800, 1040))
    with pytest.raises(ValueError):
        window.set_horizontal_percent(value, notify=False)
