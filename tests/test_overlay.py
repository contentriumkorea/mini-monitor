# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import threading
import tkinter as tk

import pytest
from PIL import Image

from ai_mini_monitor.models import DisplaySnapshot, Metric
from ai_mini_monitor.rendering.layout import PORTRAIT_LAYOUT
from ai_mini_monitor.rendering.renderer import DashboardRenderer, get_overlay_layers
from ai_mini_monitor.ui import overlay as overlay_module
from ai_mini_monitor.ui.overlay import (
    MAX_OPACITY,
    MAX_SCALE_PERCENT,
    MIN_OPACITY,
    MIN_SCALE_PERCENT,
    OverlayGeometry,
    OverlayState,
    OverlayWindow,
    WorkArea,
    clamp_overlay_geometry,
    compose_overlay_rgba,
)
from ai_mini_monitor.ui.preview import MainThreadRequired


@dataclass(eq=False)
class FakeWidget:
    master: object | None = None


class FakeWindow(FakeWidget):
    def __init__(self, root, *, class_: str) -> None:
        super().__init__(root)
        self.class_name = class_
        self.title_text = ""
        self.background = ""
        self.override_redirect = False
        self.attribute_values: dict[str, object] = {}
        self.protocols: dict[str, object] = {}
        self.bindings: dict[str, object] = {}
        self.geometry_value = ""
        self.withdrawn = False
        self.lifted = False
        self.destroyed = False
        self.focused = False
        self.idle_updates = 0

    def title(self, value: str) -> None:
        self.title_text = value

    def configure(self, *, background: str) -> None:
        self.background = background

    def overrideredirect(self, value: bool) -> None:
        self.override_redirect = value

    def attributes(self, key: str, value: object) -> None:
        self.attribute_values[key] = value

    def protocol(self, name: str, callback) -> None:
        self.protocols[name] = callback

    def bind(self, event: str, callback, *, add: str) -> None:
        assert add == "+"
        self.bindings[event] = callback

    def geometry(self, value: str) -> None:
        self.geometry_value = value

    def withdraw(self) -> None:
        self.withdrawn = True

    def deiconify(self) -> None:
        self.withdrawn = False

    def lift(self) -> None:
        self.lifted = True

    def destroy(self) -> None:
        self.destroyed = True

    def focus_set(self) -> None:
        self.focused = True

    def update_idletasks(self) -> None:
        self.idle_updates += 1


class FakePresenter:
    def __init__(self, window: FakeWindow) -> None:
        self.window = window
        self.frames: list[tuple[Image.Image, int, int]] = []
        self.fail_next = False

    def present(self, image: Image.Image, *, x: int, y: int) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("injected native presentation failure")
        self.frames.append((image.copy(), x, y))


@pytest.fixture
def fake_tk(monkeypatch):
    windows: list[FakeWindow] = []
    presenters: list[FakePresenter] = []

    def make_window(root, *, class_: str):
        window = FakeWindow(root, class_=class_)
        windows.append(window)
        return window

    def make_presenter(window):
        presenter = FakePresenter(window)
        presenters.append(presenter)
        return presenter

    monkeypatch.setattr(overlay_module.tk, "Toplevel", make_window)
    return windows, presenters, make_presenter


def _frame(*, portrait: bool = False, value: float = 78.0) -> Image.Image:
    renderer = DashboardRenderer(layout=PORTRAIT_LAYOUT) if portrait else DashboardRenderer()
    return renderer.render(
        DisplaySnapshot(
            cpu_percent=Metric(value, "%"),
            gpu_percent=Metric(42, "%"),
            memory_percent=Metric(61, "%"),
            cpu_history=(0, 25, 50, 75, 100),
            gpu_history=(100, 75, 50, 25, 0),
            memory_history=(20, 40, 30, 60, 50),
        )
    )


def _clear_card_point(frame: Image.Image, *, size: tuple[int, int] | None = None) -> tuple[int, int]:
    layers = get_overlay_layers(frame)
    mask = layers.surface_mask
    foreground = layers.foreground
    if size is not None and size != frame.size:
        mask = mask.resize(size, Image.Resampling.LANCZOS)
        foreground = foreground.resize(size, Image.Resampling.LANCZOS)
    for y in range(mask.height):
        for x in range(mask.width):
            if mask.getpixel((x, y)) == 255 and foreground.getpixel((x, y))[3] == 0:
                return x, y
    raise AssertionError("no clear card-surface pixel found")


def make_overlay(fake_tk, **overrides) -> OverlayWindow:
    defaults = {
        "position": (50, 60),
        "work_areas_provider": lambda _window: (WorkArea(0, 0, 800, 600),),
        "presenter_factory": fake_tk[2],
    }
    defaults.update(overrides)
    return OverlayWindow(object(), **defaults)


def test_clamp_uses_negative_coordinate_monitor_and_preserves_size() -> None:
    areas = (
        WorkArea(-1920, 0, 0, 1040),
        WorkArea(0, 0, 1920, 1040),
    )

    clamped = clamp_overlay_geometry(
        OverlayGeometry(-2050, 900, 480, 320),
        areas,
    )

    assert clamped == OverlayGeometry(-1920, 720, 480, 320)


def test_clamp_moves_removed_monitor_position_to_nearest_work_area() -> None:
    clamped = clamp_overlay_geometry(
        OverlayGeometry(5000, 400, 480, 320),
        (WorkArea(0, 0, 1920, 1040),),
    )

    assert clamped == OverlayGeometry(1440, 400, 480, 320)


def test_oversized_geometry_shrinks_uniformly_to_work_area() -> None:
    clamped = clamp_overlay_geometry(
        OverlayGeometry(20, 30, 1200, 800),
        (WorkArea(0, 0, 600, 350),),
    )

    assert clamped == OverlayGeometry(20, 0, 525, 350)
    assert clamped.width / clamped.height == pytest.approx(1.5)


def test_window_is_single_borderless_topmost_and_initially_hidden(fake_tk) -> None:
    overlay = make_overlay(fake_tk)
    window = fake_tk[0][0]

    assert len(fake_tk[0]) == 1
    assert window.class_name == "AIMiniMonitorOverlay"
    assert window.override_redirect is True
    assert window.background == overlay_module.BACKGROUND
    assert window.attribute_values == {"-topmost": True}
    assert "-alpha" not in window.attribute_values
    assert "-transparentcolor" not in window.attribute_values
    assert window.geometry_value == "480x320+50+60"
    assert window.withdrawn is True
    assert overlay.visible is False


@pytest.mark.parametrize(
    "overrides",
    (
        {"opacity": MIN_OPACITY - 0.01},
        {"scale_percent": MIN_SCALE_PERCENT - 1},
        {"work_areas_provider": lambda _window: ()},
    ),
)
def test_invalid_constructor_inputs_do_not_create_native_child(
    fake_tk,
    overrides,
) -> None:
    with pytest.raises(ValueError):
        make_overlay(fake_tk, **overrides)

    assert fake_tk[0] == []


def test_same_semantic_frame_is_scaled_with_native_aspect(fake_tk) -> None:
    overlay = make_overlay(fake_tk, scale_percent=50)
    source = _frame()

    overlay.update_image(source)

    assert fake_tk[1] == []
    overlay.show(notify=False)
    presented, x, y = fake_tk[1][0].frames[-1]
    assert presented.size == (240, 160)
    assert presented.mode == "RGBA"
    assert (x, y) == (50, 60)
    assert overlay.state.width == 240
    assert overlay.state.height == 160
    assert fake_tk[0][0].geometry_value == "240x160+50+60"


def test_portrait_frame_and_bounding_box_keep_aspect(fake_tk) -> None:
    overlay = make_overlay(fake_tk)
    overlay.update_image(_frame(portrait=True))

    assert overlay.set_size(500, 300) == (198, 298)
    assert overlay.state.scale_percent == 62
    assert fake_tk[1] == []
    overlay.show(notify=False)
    assert fake_tk[1][0].frames[-1][0].size == (198, 298)


def test_hidden_frame_updates_allocate_no_presenter_or_composite(fake_tk) -> None:
    overlay = make_overlay(fake_tk)

    for value in range(12):
        overlay.update_image(_frame(value=float(value)))

    assert overlay.visible is False
    assert fake_tk[1] == []
    assert overlay._last_composite is None
    overlay.show(notify=False)
    assert len(fake_tk[1]) == 1
    assert len(fake_tk[1][0].frames) == 1


def test_rgb_without_semantic_layers_is_rejected(fake_tk) -> None:
    overlay = make_overlay(fake_tk)

    with pytest.raises(ValueError, match="overlay layers"):
        overlay.update_image(Image.new("RGB", (480, 320), "black"))


@pytest.mark.parametrize("portrait", [False, True])
@pytest.mark.parametrize("opacity", [0.0, 0.35, 1.0])
def test_background_alpha_changes_but_information_core_stays_opaque(
    portrait: bool,
    opacity: float,
) -> None:
    renderer = DashboardRenderer(layout=PORTRAIT_LAYOUT) if portrait else DashboardRenderer()
    frame = renderer.render(
        DisplaySnapshot(
            cpu_percent=Metric(78, "%"),
            cpu_history=(0, 30, 70, 100),
        )
    )
    composed = compose_overlay_rgba(frame, opacity)
    layers = get_overlay_layers(frame)
    foreground = layers.foreground
    card_point = _clear_card_point(frame)

    assert composed.mode == "RGBA"
    assert composed.getpixel((0, 0))[3] == 0
    assert composed.getpixel(card_point)[3] == round(opacity * 255)

    digit_bbox = renderer.last_placements["cpu_value_digits"].bbox
    opaque_text_pixels = [
        (x, y)
        for y in range(digit_bbox[1], digit_bbox[3])
        for x in range(digit_bbox[0], digit_bbox[2])
        if foreground.getpixel((x, y))[3] == 255
    ]
    assert opaque_text_pixels
    text_point = opaque_text_pixels[0]
    assert composed.getpixel(text_point)[3] == 255

    track = renderer.cpu_gauge
    track_point = (track.x + track.width // 2, track.y + track.height // 2)
    assert foreground.getpixel(track_point)[3] == 255
    assert composed.getpixel(track_point)[3] == 255

    spark = renderer.last_sparklines["cpu"]
    spark_core = [
        (x, y)
        for y in range(spark.y, spark.bottom)
        for x in range(spark.x, spark.right)
        if foreground.getpixel((x, y))[3] == 255
    ]
    assert spark_core
    assert composed.getpixel(spark_core[0])[3] == 255


@pytest.mark.parametrize("portrait", [False, True])
@pytest.mark.parametrize("scale_percent", [50, 100, 200])
@pytest.mark.parametrize("opacity", [0.0, 0.35, 1.0])
def test_all_supported_surface_opacities_keep_scaled_foreground_opaque(
    portrait: bool,
    scale_percent: int,
    opacity: float,
) -> None:
    frame = _frame(portrait=portrait)
    target = (
        round(frame.width * scale_percent / 100),
        round(frame.height * scale_percent / 100),
    )
    composed = compose_overlay_rgba(frame, opacity, size=target)
    foreground = get_overlay_layers(frame).foreground
    surface_mask = get_overlay_layers(frame).surface_mask
    if foreground.size != target:
        foreground = foreground.resize(target, Image.Resampling.LANCZOS)
        surface_mask = surface_mask.resize(target, Image.Resampling.LANCZOS)

    opaque_foreground = [
        (x, y)
        for y in range(target[1])
        for x in range(target[0])
        if foreground.getpixel((x, y))[3] == 255
    ]

    assert composed.size == target
    assert composed.getpixel((0, 0))[3] == 0
    card_point = _clear_card_point(frame, size=target)
    assert surface_mask.getpixel(card_point) == 255
    assert composed.getpixel(card_point)[3] == round(opacity * 255)
    assert opaque_foreground
    assert all(composed.getpixel(point)[3] == 255 for point in opaque_foreground)


@pytest.mark.parametrize("portrait", [False, True])
def test_full_opacity_keeps_card_rgb_but_removes_outer_canvas(portrait) -> None:
    frame = _frame(portrait=portrait)

    composed = compose_overlay_rgba(frame, 1.0)
    mask = get_overlay_layers(frame).surface_mask

    assert composed.getpixel((0, 0))[3] == 0
    for y in range(frame.height):
        for x in range(frame.width):
            if mask.getpixel((x, y)) == 255:
                assert composed.getpixel((x, y))[:3] == frame.getpixel((x, y))


def test_resized_layers_keep_foreground_core_opaque(fake_tk) -> None:
    overlay = make_overlay(
        fake_tk,
        opacity=MIN_OPACITY,
        scale_percent=200,
        work_areas_provider=lambda _window: (WorkArea(0, 0, 2200, 1400),),
    )
    overlay.update_image(_frame())
    overlay.show(notify=False)
    presented = fake_tk[1][0].frames[-1][0]

    assert presented.size == (960, 640)
    # The semantic canvas is absent; the native-only neutral alpha=1 floor
    # preserves dragging across the full rectangular overlay.
    assert presented.getpixel((0, 0)) == (0, 0, 0, 1)
    assert presented.getpixel(_clear_card_point(_frame(), size=(960, 640)))[3] == 1
    assert presented.getchannel("A").getextrema()[1] == 255


def test_zero_opacity_overlay_can_hide_show_and_move_without_losing_frame(
    fake_tk,
) -> None:
    overlay = make_overlay(fake_tk, opacity=0.0)
    overlay.update_image(_frame())

    overlay.show(notify=False)
    first = fake_tk[1][0].frames[-1][0]
    # The public setting remains exactly 0%.  The native presentation uses
    # one invisible, colourless alpha step only at native presentation so the
    # removed canvas/grid stays visually absent and the full surface drags.
    assert overlay.state.opacity == 0.0
    assert first.getpixel((0, 0)) == (0, 0, 0, 1)
    assert first.getpixel(_clear_card_point(_frame()))[3] == 1
    assert first.getchannel("A").getextrema()[1] == 255

    overlay.hide(notify=False)
    assert overlay.visible is False
    assert overlay.set_position(120, 140, notify=False) == (120, 140)

    overlay.show(notify=False)
    second, x, y = fake_tk[1][0].frames[-1]
    assert overlay.visible is True
    assert (x, y) == (120, 140)
    assert second.tobytes() == first.tobytes()


def test_opacity_and_scale_accept_exact_safe_public_limits(fake_tk) -> None:
    states: list[OverlayState] = []
    overlay = make_overlay(
        fake_tk,
        on_state_change=states.append,
        work_areas_provider=lambda _window: (WorkArea(0, 0, 4000, 3000),),
    )

    assert overlay.set_opacity(MIN_OPACITY) == MIN_OPACITY
    assert overlay.set_opacity(MAX_OPACITY) == MAX_OPACITY
    assert overlay.set_scale(MIN_SCALE_PERCENT) == MIN_SCALE_PERCENT
    assert overlay.set_scale(MAX_SCALE_PERCENT) == MAX_SCALE_PERCENT
    assert states[-1].scale_percent == MAX_SCALE_PERCENT
    assert states[-1].opacity == MAX_OPACITY


def test_visible_scale_failure_restores_state_geometry_and_native_pixels(
    fake_tk,
) -> None:
    states: list[OverlayState] = []
    overlay = make_overlay(fake_tk, on_state_change=states.append)
    overlay.update_image(_frame())
    overlay.show(notify=False)
    presenter = fake_tk[1][0]
    previous_state = overlay.state
    previous_composite = overlay._last_composite
    previous_geometry = fake_tk[0][0].geometry_value
    assert previous_composite is not None

    presenter.fail_next = True
    with pytest.raises(RuntimeError, match="presentation failure"):
        overlay.set_scale(150)

    assert overlay.state == previous_state
    assert overlay._last_composite is previous_composite
    assert fake_tk[0][0].geometry_value == previous_geometry
    restored, x, y = presenter.frames[-1]
    assert restored.tobytes() == previous_composite.tobytes()
    assert (x, y) == (previous_state.x, previous_state.y)
    assert states == []

    assert overlay.set_scale(150, notify=False) == 150
    assert overlay.state.width == 720
    assert overlay.state.height == 480


def test_visible_opacity_failure_restores_value_and_composite(fake_tk) -> None:
    overlay = make_overlay(fake_tk)
    overlay.update_image(_frame())
    overlay.show(notify=False)
    presenter = fake_tk[1][0]
    previous_state = overlay.state
    previous_composite = overlay._last_composite
    assert previous_composite is not None

    presenter.fail_next = True
    with pytest.raises(RuntimeError, match="presentation failure"):
        overlay.set_opacity(0.0, notify=False)

    assert overlay.state == previous_state
    assert overlay._last_composite is previous_composite
    assert presenter.frames[-1][0].tobytes() == previous_composite.tobytes()


def test_visible_frame_failure_restores_previous_layers_size_and_pixels(
    fake_tk,
) -> None:
    overlay = make_overlay(fake_tk)
    overlay.update_image(_frame(value=12.0))
    overlay.show(notify=False)
    presenter = fake_tk[1][0]
    previous_state = overlay.state
    previous_layers = overlay._layers
    previous_background = overlay._background_layer
    previous_native_size = overlay._native_size
    previous_composite = overlay._last_composite
    assert previous_composite is not None

    presenter.fail_next = True
    with pytest.raises(RuntimeError, match="presentation failure"):
        overlay.update_image(_frame(portrait=True, value=88.0))

    assert overlay.state == previous_state
    assert overlay._layers is previous_layers
    assert overlay._background_layer is previous_background
    assert overlay._native_size == previous_native_size
    assert overlay._last_composite is previous_composite
    assert presenter.frames[-1][0].tobytes() == previous_composite.tobytes()

    overlay.update_image(_frame(portrait=True, value=88.0))
    assert overlay.state.width == 320
    assert overlay.state.height == 480


def test_show_failure_stays_hidden_and_preserves_pending_image(fake_tk) -> None:
    created: list[FakePresenter] = []

    def presenter_factory(window: FakeWindow) -> FakePresenter:
        presenter = FakePresenter(window)
        presenter.fail_next = not created
        created.append(presenter)
        return presenter

    overlay = make_overlay(fake_tk, presenter_factory=presenter_factory)
    frame = _frame(value=37.0)
    overlay.update_image(frame)
    pending_layers = overlay._layers
    previous_state = overlay.state

    with pytest.raises(RuntimeError, match="presentation failure"):
        overlay.show(notify=False)

    assert overlay.state == previous_state
    assert overlay.visible is False
    assert fake_tk[0][0].withdrawn is True
    assert overlay._layers is pending_layers
    assert overlay._presenter is None
    assert overlay._last_composite is None

    overlay.show(notify=False)
    assert overlay.visible is True
    assert len(created) == 2
    assert created[-1].frames[-1][0].size == frame.size


@pytest.mark.parametrize(
    "value",
    [True, float("nan"), float("inf"), "0.5", MIN_OPACITY - 0.01, 1.01],
)
def test_opacity_rejects_ambiguous_nonfinite_or_out_of_range_values(
    fake_tk,
    value,
) -> None:
    overlay = make_overlay(fake_tk)

    with pytest.raises(ValueError):
        overlay.set_opacity(value)


@pytest.mark.parametrize(
    "value",
    [True, 100.0, float("nan"), "100", MIN_SCALE_PERCENT - 1, 201],
)
def test_scale_rejects_non_integer_or_out_of_range_values(fake_tk, value) -> None:
    overlay = make_overlay(fake_tk)

    with pytest.raises(ValueError):
        overlay.set_scale(value)


def test_position_api_clamps_to_monitor_work_area(fake_tk) -> None:
    states: list[OverlayState] = []
    overlay = make_overlay(fake_tk, on_state_change=states.append)

    assert overlay.set_position(900, -100) == (320, 0)
    assert states[-1].x == 320
    assert states[-1].y == 0


def test_reset_position_uses_first_work_area_top_right(fake_tk) -> None:
    areas = (
        WorkArea(-1920, 0, 0, 1040),
        WorkArea(0, 0, 1920, 1040),
    )
    overlay = make_overlay(
        fake_tk,
        position=(400, 400),
        work_areas_provider=lambda _window: areas,
    )

    assert overlay.reset_position(notify=False) == (-496, 16)


def test_visible_move_uses_absolute_layered_destination_on_negative_monitor(
    fake_tk,
) -> None:
    overlay = make_overlay(
        fake_tk,
        position=(-1800, 40),
        work_areas_provider=lambda _window: (
            WorkArea(-1920, 0, 0, 1040),
            WorkArea(0, 0, 1920, 1040),
        ),
    )
    overlay.update_image(_frame())
    overlay.show(notify=False)

    assert overlay.set_position(-1500, 120, notify=False) == (-1500, 120)
    _image, x, y = fake_tk[1][0].frames[-1]
    assert (x, y) == (-1500, 120)


def test_drag_from_noninteractive_surface_clamps_and_notifies_once(fake_tk) -> None:
    states: list[OverlayState] = []
    overlay = make_overlay(fake_tk, on_state_change=states.append)
    window = fake_tk[0][0]
    surface = FakeWidget(window)

    window.bindings["<ButtonPress-1>"](
        SimpleNamespace(widget=surface, x_root=100, y_root=100)
    )
    window.bindings["<B1-Motion>"](
        SimpleNamespace(widget=surface, x_root=1000, y_root=-100)
    )
    assert (overlay.state.x, overlay.state.y) == (320, 0)
    assert states == []

    window.bindings["<ButtonRelease-1>"](
        SimpleNamespace(widget=surface, x_root=1000, y_root=-100)
    )
    assert len(states) == 1
    assert (states[0].x, states[0].y) == (320, 0)
    assert window.focused is True


def test_drag_waits_for_four_pixel_threshold(fake_tk) -> None:
    states: list[OverlayState] = []
    overlay = make_overlay(fake_tk, on_state_change=states.append)
    window = fake_tk[0][0]
    surface = FakeWidget(window)

    window.bindings["<ButtonPress-1>"](
        SimpleNamespace(widget=surface, x_root=100, y_root=100)
    )
    window.bindings["<B1-Motion>"](
        SimpleNamespace(widget=surface, x_root=103, y_root=100)
    )
    window.bindings["<ButtonRelease-1>"](
        SimpleNamespace(widget=surface, x_root=103, y_root=100)
    )

    assert (overlay.state.x, overlay.state.y) == (50, 60)
    assert states == []


def test_alt_arrow_keyboard_movement_has_normal_and_large_steps(fake_tk) -> None:
    states: list[OverlayState] = []
    overlay = make_overlay(fake_tk, on_state_change=states.append)
    window = fake_tk[0][0]

    assert window.bindings["<Alt-Right>"](SimpleNamespace()) == "break"
    assert (overlay.state.x, overlay.state.y) == (51, 60)
    assert window.bindings["<Alt-Shift-Down>"](SimpleNamespace()) == "break"
    assert (overlay.state.x, overlay.state.y) == (51, 70)
    assert len(states) == 2


def test_callback_failure_does_not_escape_tk_event_and_is_sanitized(
    fake_tk,
    caplog,
) -> None:
    def failing_callback(_state: OverlayState) -> None:
        raise RuntimeError("private path C:/Users/example/.codex")

    overlay = make_overlay(fake_tk, on_state_change=failing_callback)

    with caplog.at_level("ERROR"):
        overlay.show()

    assert overlay.visible is True
    assert "RuntimeError" in caplog.text
    assert "private path" not in caplog.text


def test_registered_control_and_descendants_do_not_start_drag(fake_tk) -> None:
    overlay = make_overlay(fake_tk)
    window = fake_tk[0][0]
    control = FakeWidget(window)
    control_child = FakeWidget(control)
    overlay.register_interactive_widget(control)

    window.bindings["<ButtonPress-1>"](
        SimpleNamespace(widget=control_child, x_root=100, y_root=100)
    )
    window.bindings["<B1-Motion>"](
        SimpleNamespace(widget=control_child, x_root=300, y_root=300)
    )
    window.bindings["<ButtonRelease-1>"](
        SimpleNamespace(widget=control_child, x_root=300, y_root=300)
    )

    assert (overlay.state.x, overlay.state.y) == (50, 60)


def test_user_close_hides_but_shutdown_destroy_releases_window(fake_tk) -> None:
    overlay = make_overlay(fake_tk)
    window = fake_tk[0][0]
    overlay.show()

    window.protocols["WM_DELETE_WINDOW"]()

    assert overlay.closed is False
    assert overlay.visible is False
    assert window.destroyed is False
    overlay.destroy()
    assert overlay.closed is True
    assert window.destroyed is True


def test_topmost_is_reasserted_whenever_overlay_is_shown(fake_tk) -> None:
    overlay = make_overlay(fake_tk)
    window = fake_tk[0][0]
    window.attribute_values["-topmost"] = False

    overlay.show(notify=False)

    assert window.attribute_values["-topmost"] is True
    assert window.lifted is True
    assert overlay.visible is True


def test_tk_operations_reject_worker_threads(fake_tk) -> None:
    overlay = make_overlay(fake_tk)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            overlay.set_position(10, 20)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=2)

    assert len(errors) == 1
    assert isinstance(errors[0], MainThreadRequired)


def test_real_tk_layered_overlay_lifecycle_keeps_host_application_alive() -> None:
    if os.environ.get("MINI_MONITOR_OVERLAY_SMOKE_CHILD") != "1":
        env = os.environ.copy()
        env["MINI_MONITOR_OVERLAY_SMOKE_CHILD"] = "1"
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", f"{Path(__file__).resolve()}::test_real_tk_layered_overlay_lifecycle_keeps_host_application_alive"],
            env=env,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, (result.stdout + result.stderr).decode(errors="replace")
        return
    root = tk.Tk(className="AIMiniMonitorOverlayTestHost")
    root.withdraw()
    overlay = OverlayWindow(
        root,
        position=(20, 30),
        work_areas_provider=lambda _window: (
            WorkArea(-1920, 0, 0, 1032),
            WorkArea(0, 0, 1920, 1032),
        ),
    )
    try:
        overlay.update_image(_frame())
        overlay.show(notify=False)
        root.update_idletasks()
        assert overlay.window.overrideredirect() is True
        assert bool(overlay.window.attributes("-topmost")) is True
        # Tk reports default attributes even when the application never sets
        # them.  The fake-window regression above proves no such setter call;
        # the native readback must remain at the non-destructive defaults.
        assert float(overlay.window.attributes("-alpha")) == pytest.approx(1.0)
        assert overlay.window.attributes("-transparentcolor") == ""
        assert overlay.state == OverlayState(
            20,
            30,
            480,
            320,
            overlay_module.DEFAULT_OPACITY,
            100,
            True,
        )

        # Alpha-zero layered pixels are normally click-through on Windows.
        # The native presentation keeps a colourless alpha=1 input floor;
        # the removed canvas/grid remains visually absent.
        overlay.set_opacity(0.0, notify=False)
        root.update()
        assert overlay.state.opacity == 0.0
        assert overlay._last_composite.getpixel((0, 0)) == (0, 0, 0, 1)
        assert overlay._last_composite.getpixel(_clear_card_point(_frame()))[3] == 1
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.WindowFromPoint.argtypes = (wintypes.POINT,)
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        hit = user32.WindowFromPoint(wintypes.POINT(22, 32))
        assert user32.GetAncestor(hit, 2) == overlay._presenter.hwnd

        overlay.set_position(-1500, 120, notify=False)
        root.update()
        user32.GetWindowRect.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        )
        user32.GetWindowRect.restype = wintypes.BOOL
        rect = wintypes.RECT()
        assert user32.GetWindowRect(overlay._presenter.hwnd, ctypes.byref(rect))
        assert (rect.left, rect.top) == (-1500, 120)

        overlay.close()
        root.update_idletasks()
        assert overlay.closed is False
        assert overlay.visible is False
        assert bool(root.winfo_exists()) is True
    finally:
        overlay.destroy()
        root.destroy()
