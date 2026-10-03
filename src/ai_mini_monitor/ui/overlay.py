# SPDX-License-Identifier: GPL-3.0-or-later

"""Main-thread-only, always-on-top desktop dashboard overlay.

The screen-wide canvas/grid stays fully transparent.  Card/connection surfaces
receive the user-selected alpha while the semantic foreground layer (text, numbers,
gauges, and graphs) keeps its original alpha.  Tk's whole-window ``-alpha``
and color-key transparency are deliberately not used.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import logging
import math
import sys
import tkinter as tk
from typing import Callable, Protocol, Sequence

from PIL import Image

from ..config import (
    DEFAULT_OVERLAY_OPACITY,
    DEFAULT_OVERLAY_SCALE_PERCENT,
    MAX_OVERLAY_OPACITY,
    MAX_OVERLAY_SCALE_PERCENT,
    MIN_OVERLAY_OPACITY,
    MIN_OVERLAY_SCALE_PERCENT,
)
from ..rendering.renderer import OverlayLayers, get_overlay_layers
from ..rendering.theme import DEFAULT_THEME
from .layered_window import LayeredWindowPresenter
from .preview import require_main_thread


BACKGROUND = DEFAULT_THEME.background
DEFAULT_FRAME_SIZE = (480, 320)
DEFAULT_OPACITY = DEFAULT_OVERLAY_OPACITY
MIN_OPACITY = MIN_OVERLAY_OPACITY
MAX_OPACITY = MAX_OVERLAY_OPACITY
MIN_SCALE_PERCENT = MIN_OVERLAY_SCALE_PERCENT
MAX_SCALE_PERCENT = MAX_OVERLAY_SCALE_PERCENT
DEFAULT_EDGE_MARGIN = 16
DRAG_THRESHOLD = 4
# Windows deliberately lets mouse messages pass through pixels whose layered
# alpha is exactly zero.  Keep a visually transparent one-step alpha only in
# the native window presentation so the user's 0% setting can still be dragged
# from the full dashboard surface.  The semantic compositor remains exact and
# returns alpha 0 for export/pixel assertions.
_WINDOW_HIT_TEST_ALPHA = 1
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkArea:
    """Half-open monitor work-area rectangle in virtual-desktop pixels."""

    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self) -> None:
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("work area must have positive width and height")

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True, slots=True)
class OverlayGeometry:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("overlay geometry must have positive dimensions")


@dataclass(frozen=True, slots=True)
class OverlayState:
    """Serializable state emitted after an overlay setting changes."""

    x: int
    y: int
    width: int
    height: int
    opacity: float
    scale_percent: int
    visible: bool


class _Presenter(Protocol):
    def present(self, image: Image.Image, *, x: int, y: int) -> None: ...


@dataclass(frozen=True, slots=True)
class _PresentationSnapshot:
    """Logical and native-presentation state used for atomic mutations."""

    geometry: OverlayGeometry
    opacity: float
    scale_percent: int
    layers: OverlayLayers | None
    background_layer: Image.Image | None
    native_size: tuple[int, int]
    last_composite: Image.Image | None
    presenter: _Presenter | None
    visible: bool


StateCallback = Callable[[OverlayState], None]
WorkAreasProvider = Callable[[tk.Misc], Sequence[WorkArea]]
PresenterFactory = Callable[[tk.Misc], _Presenter]


def clamp_overlay_geometry(
    geometry: OverlayGeometry,
    work_areas: Sequence[WorkArea],
) -> OverlayGeometry:
    """Keep an overlay fully visible on the best matching monitor."""

    areas = tuple(work_areas)
    if not areas:
        raise ValueError("at least one work area is required")

    def intersection(area: WorkArea) -> int:
        overlap_width = max(
            0,
            min(geometry.x + geometry.width, area.right)
            - max(geometry.x, area.left),
        )
        overlap_height = max(
            0,
            min(geometry.y + geometry.height, area.bottom)
            - max(geometry.y, area.top),
        )
        return overlap_width * overlap_height

    center_x = geometry.x + geometry.width / 2.0
    center_y = geometry.y + geometry.height / 2.0

    def distance_squared(area: WorkArea) -> float:
        nearest_x = min(max(center_x, area.left), area.right)
        nearest_y = min(max(center_y, area.top), area.bottom)
        return (center_x - nearest_x) ** 2 + (center_y - nearest_y) ** 2

    area = max(
        areas,
        key=lambda candidate: (
            intersection(candidate),
            -distance_squared(candidate),
        ),
    )
    fit = min(
        1.0,
        area.width / geometry.width,
        area.height / geometry.height,
    )
    width = max(1, min(area.width, round(geometry.width * fit)))
    height = max(1, min(area.height, round(geometry.height * fit)))
    x = max(area.left, min(geometry.x, area.right - width))
    y = max(area.top, min(geometry.y, area.bottom - height))
    return OverlayGeometry(x, y, width, height)


def _positive_dimension(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _finite_number(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _opacity(value: object) -> float:
    result = _finite_number(value, name="opacity")
    if not MIN_OPACITY <= result <= MAX_OPACITY:
        raise ValueError(
            f"opacity must be between {MIN_OPACITY:g} and {MAX_OPACITY:g}"
        )
    return result


def _scale_percent(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("scale percent must be an integer")
    if not MIN_SCALE_PERCENT <= value <= MAX_SCALE_PERCENT:
        raise ValueError(
            f"scale percent must be between {MIN_SCALE_PERCENT} and "
            f"{MAX_SCALE_PERCENT}"
        )
    return value


def compose_overlay_rgba(
    image: Image.Image,
    opacity: float,
    *,
    size: tuple[int, int] | None = None,
) -> Image.Image:
    """Compose semantic dashboard layers with translucent card surfaces only.

    Foreground alpha is never multiplied by ``opacity``, including when the
    card opacity is zero.  The screen-wide canvas/grid mask is always zero, so
    it never appears in the overlay.  Text anti-aliasing stays natural and,
    crucially, independent of the slider value.
    Layers are resized independently so LANCZOS cannot turn surface pixels into
    opaque color-key residue at non-native scales.
    """

    if not isinstance(image, Image.Image):
        raise TypeError("overlay image must be a Pillow Image")
    value = _opacity(opacity)
    layers = get_overlay_layers(image)
    target = layers.size if size is None else size
    if (
        len(target) != 2
        or isinstance(target[0], bool)
        or isinstance(target[1], bool)
        or not isinstance(target[0], int)
        or not isinstance(target[1], int)
        or target[0] <= 0
        or target[1] <= 0
    ):
        raise ValueError("overlay size must contain two positive integers")

    background = layers.background
    foreground = layers.foreground
    surface_mask = layers.surface_mask
    if background.size != target:
        background = background.resize(target, Image.Resampling.LANCZOS)
        foreground = foreground.resize(target, Image.Resampling.LANCZOS)
        surface_mask = surface_mask.resize(target, Image.Resampling.LANCZOS)
    background_rgba = background.convert("RGBA")
    background_rgba.putalpha(
        surface_mask.point(tuple(round(level * value) for level in range(256)))
    )
    return Image.alpha_composite(background_rgba, foreground)


def _with_native_hit_test_floor(image: Image.Image) -> Image.Image:
    """Keep semantically clear pixels draggable without reviving the grid."""

    alpha = image.getchannel("A")
    clear_pixels = alpha.point(tuple(255 if level == 0 else 0 for level in range(256)))
    native = image.copy()
    # Replace, rather than merely raising alpha, so hidden canvas/grid RGB can
    # never leak through as a 1/255 coloured ghost after premultiplication.
    native.paste(Image.new("RGBA", image.size, (0, 0, 0, _WINDOW_HIT_TEST_ALPHA)), mask=clear_pixels)
    return native


def _fallback_work_areas(window: tk.Misc) -> tuple[WorkArea, ...]:
    try:
        left = int(window.winfo_vrootx())
        top = int(window.winfo_vrooty())
        width = int(window.winfo_vrootwidth())
        height = int(window.winfo_vrootheight())
    except (AttributeError, tk.TclError, TypeError, ValueError):
        left = top = 0
        width = int(window.winfo_screenwidth())
        height = int(window.winfo_screenheight())
    if width <= 0 or height <= 0:
        left = top = 0
        width = max(1, int(window.winfo_screenwidth()))
        height = max(1, int(window.winfo_screenheight()))
    return (WorkArea(left, top, left + width, top + height),)


def _windows_work_areas() -> tuple[WorkArea, ...]:
    """Read every Windows monitor work area without using COM."""

    if sys.platform != "win32":
        return ()

    class MonitorInfo(ctypes.Structure):
        _fields_ = (
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        )

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_monitor_info = user32.GetMonitorInfoW
        get_monitor_info.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(MonitorInfo),
        )
        get_monitor_info.restype = wintypes.BOOL

        areas: list[tuple[bool, WorkArea]] = []
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HANDLE,
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.LPARAM,
        )

        @callback_type
        def collect_monitor(monitor, _hdc, _rect, _data):
            info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
            if get_monitor_info(monitor, ctypes.byref(info)):
                work = info.rcWork
                if work.right > work.left and work.bottom > work.top:
                    areas.append(
                        (
                            bool(info.dwFlags & 1),
                            WorkArea(
                                work.left,
                                work.top,
                                work.right,
                                work.bottom,
                            ),
                        )
                    )
            return True

        enum_display_monitors = user32.EnumDisplayMonitors
        enum_display_monitors.argtypes = (
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            callback_type,
            wintypes.LPARAM,
        )
        enum_display_monitors.restype = wintypes.BOOL
        if enum_display_monitors(None, None, collect_monitor, 0):
            areas.sort(key=lambda item: not item[0])
            return tuple(area for _primary, area in areas)
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return ()


def default_work_areas(window: tk.Misc) -> tuple[WorkArea, ...]:
    """Return all usable monitor rectangles, with a portable Tk fallback."""

    return _windows_work_areas() or _fallback_work_areas(window)


def _validated_work_areas(
    provider: WorkAreasProvider,
    window: tk.Misc,
) -> tuple[WorkArea, ...]:
    areas = tuple(provider(window))
    if not areas:
        raise ValueError("work area provider returned no monitors")
    if not all(isinstance(area, WorkArea) for area in areas):
        raise TypeError("work area provider must return WorkArea values")
    return areas


class OverlayWindow:
    """Borderless, draggable, single-HWND dashboard overlay."""

    def __init__(
        self,
        root: tk.Misc,
        *,
        title: str = "AI Mini Monitor Overlay",
        frame_size: tuple[int, int] = DEFAULT_FRAME_SIZE,
        opacity: float = DEFAULT_OPACITY,
        scale_percent: int = DEFAULT_OVERLAY_SCALE_PERCENT,
        position: tuple[int, int] | None = None,
        on_state_change: StateCallback | None = None,
        work_areas_provider: WorkAreasProvider = default_work_areas,
        presenter_factory: PresenterFactory = LayeredWindowPresenter,
    ) -> None:
        require_main_thread()
        native_width = _positive_dimension(frame_size[0], name="frame width")
        native_height = _positive_dimension(frame_size[1], name="frame height")
        requested_opacity = _opacity(opacity)
        requested_scale = _scale_percent(scale_percent)
        if position is not None:
            if len(position) != 2 or any(isinstance(item, bool) for item in position):
                raise ValueError("position must contain two integer coordinates")
            if not all(isinstance(item, int) for item in position):
                raise ValueError("position must contain two integer coordinates")

        # Validate before creating a native child so a bad provider cannot
        # leak an invisible top-level window during application startup.
        initial_work_areas = _validated_work_areas(work_areas_provider, root)
        self._window = tk.Toplevel(root, class_="AIMiniMonitorOverlay")
        self._closed = False
        self._visible = False
        self._layers: OverlayLayers | None = None
        self._background_layer: Image.Image | None = None
        self._native_size = (native_width, native_height)
        self._scale_percent = requested_scale
        self._opacity = requested_opacity
        self._on_state_change = on_state_change
        self._work_areas_provider = work_areas_provider
        self._initial_work_areas: tuple[WorkArea, ...] | None = initial_work_areas
        self._presenter_factory = presenter_factory
        self._presenter: _Presenter | None = None
        self._last_composite: Image.Image | None = None
        self._interactive_widgets: set[tk.Misc] = set()
        self._drag_pointer: tuple[int, int] | None = None
        self._drag_origin: tuple[int, int] | None = None
        self._drag_moved = False

        width, height = self._dimensions_for_scale(self._scale_percent)
        if position is None:
            work_area = initial_work_areas[0]
            x = work_area.right - width - DEFAULT_EDGE_MARGIN
            y = work_area.top + DEFAULT_EDGE_MARGIN
        else:
            x, y = position
        self._geometry = OverlayGeometry(x, y, width, height)

        self._window.title(title)
        self._window.configure(background=BACKGROUND)
        self._window.overrideredirect(True)
        self._window.attributes("-topmost", True)
        # Never call Tk '-alpha' or '-transparentcolor': both destroy the
        # per-pixel foreground opacity contract or conflict with ULW_ALPHA.
        self._window.protocol("WM_DELETE_WINDOW", self.close)
        self._window.bind("<ButtonPress-1>", self._begin_drag, add="+")
        self._window.bind("<B1-Motion>", self._continue_drag, add="+")
        self._window.bind("<ButtonRelease-1>", self._finish_drag, add="+")
        self._window.bind("<Escape>", lambda _event: self.close(), add="+")
        for event, dx, dy in (
            ("<Alt-Left>", -1, 0),
            ("<Alt-Right>", 1, 0),
            ("<Alt-Up>", 0, -1),
            ("<Alt-Down>", 0, 1),
            ("<Alt-Shift-Left>", -10, 0),
            ("<Alt-Shift-Right>", 10, 0),
            ("<Alt-Shift-Up>", 0, -10),
            ("<Alt-Shift-Down>", 0, 10),
        ):
            self._window.bind(
                event,
                lambda _event, x=dx, y=dy: self._nudge(x, y),
                add="+",
            )
        self._window.withdraw()
        self._apply_geometry(render=False)

    @property
    def window(self) -> tk.Toplevel:
        return self._window

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def visible(self) -> bool:
        require_main_thread()
        return not self._closed and self._visible

    @property
    def state(self) -> OverlayState:
        require_main_thread()
        geometry = self._geometry
        return OverlayState(
            geometry.x,
            geometry.y,
            geometry.width,
            geometry.height,
            self._opacity,
            self._scale_percent,
            self._visible and not self._closed,
        )

    def update_image(self, image: Image.Image) -> None:
        """Publish a renderer frame without presenting while hidden."""

        require_main_thread()
        self._ensure_open()
        if not isinstance(image, Image.Image):
            raise TypeError("overlay image must be a Pillow Image")
        if image.width <= 0 or image.height <= 0:
            raise ValueError("overlay image must have positive dimensions")
        layers = get_overlay_layers(image)
        if layers.size != image.size:
            raise ValueError("overlay layer size does not match the framebuffer")
        snapshot = self._presentation_snapshot()
        try:
            if layers.size != self._native_size:
                self._background_layer = None
            self._layers = layers
            self._native_size = layers.size
            if self._background_layer is None:
                self._background_layer = layers.background
            width, height = self._dimensions_for_scale(self._scale_percent)
            current = self._geometry
            self._geometry = OverlayGeometry(current.x, current.y, width, height)
            self._apply_geometry(render=self._visible)
        except Exception:
            self._restore_presentation(snapshot)
            raise

    def set_opacity(self, opacity: float, *, notify: bool = True) -> float:
        """Set card opacity; the grid stays clear and information stays opaque.

        At zero, the visible foreground remains available while callers can
        always recover or relocate the overlay through ``set_position``.
        """

        require_main_thread()
        self._ensure_open()
        requested_opacity = _opacity(opacity)
        snapshot = self._presentation_snapshot()
        try:
            self._opacity = requested_opacity
            if self._visible and self._layers is not None:
                self._render_source()
        except Exception:
            self._restore_presentation(snapshot)
            raise
        if notify:
            self._notify_state_change()
        return self._opacity

    def set_scale(self, scale_percent: int, *, notify: bool = True) -> int:
        """Resize relative to the native frame while preserving its aspect."""

        require_main_thread()
        self._ensure_open()
        requested_scale = _scale_percent(scale_percent)
        snapshot = self._presentation_snapshot()
        try:
            self._scale_percent = requested_scale
            width, height = self._dimensions_for_scale(self._scale_percent)
            current = self._geometry
            self._geometry = OverlayGeometry(current.x, current.y, width, height)
            self._apply_geometry(render=self._visible)
        except Exception:
            self._restore_presentation(snapshot)
            raise
        if notify:
            self._notify_state_change()
        return self._scale_percent

    def set_size(
        self,
        width: int,
        height: int | None = None,
        *,
        notify: bool = True,
    ) -> tuple[int, int]:
        """Fit the native aspect into a width or bounding box."""

        require_main_thread()
        self._ensure_open()
        target_width = _positive_dimension(width, name="width")
        native_width, native_height = self._native_size
        if height is None:
            target_height = max(1, round(target_width * native_height / native_width))
        else:
            bound_height = _positive_dimension(height, name="height")
            fit = min(target_width / native_width, bound_height / native_height)
            target_width = max(1, round(native_width * fit))
            target_height = max(1, round(native_height * fit))
        requested_scale = math.floor(target_width * 100.0 / native_width)
        if not MIN_SCALE_PERCENT <= requested_scale <= MAX_SCALE_PERCENT:
            raise ValueError(
                f"requested size must map to {MIN_SCALE_PERCENT}.."
                f"{MAX_SCALE_PERCENT}%"
            )
        requested_scale = _scale_percent(requested_scale)
        snapshot = self._presentation_snapshot()
        try:
            self._scale_percent = requested_scale
            target_width, target_height = self._dimensions_for_scale(
                self._scale_percent
            )
            current = self._geometry
            self._geometry = OverlayGeometry(
                current.x,
                current.y,
                target_width,
                target_height,
            )
            self._apply_geometry(render=self._visible)
        except Exception:
            self._restore_presentation(snapshot)
            raise
        if notify:
            self._notify_state_change()
        return self._geometry.width, self._geometry.height

    def set_position(self, x: int, y: int, *, notify: bool = True) -> tuple[int, int]:
        """Move the overlay and clamp it into the current monitor set."""

        require_main_thread()
        self._ensure_open()
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, int)
            or not isinstance(y, int)
        ):
            raise ValueError("position coordinates must be integers")
        snapshot = self._presentation_snapshot()
        try:
            current = self._geometry
            self._geometry = OverlayGeometry(x, y, current.width, current.height)
            self._apply_geometry(render=False)
        except Exception:
            self._restore_presentation(snapshot)
            raise
        if notify:
            self._notify_state_change()
        return self._geometry.x, self._geometry.y

    def reset_position(self, *, notify: bool = True) -> tuple[int, int]:
        """Move to the top-right of the first current monitor work area."""

        require_main_thread()
        self._ensure_open()
        areas = _validated_work_areas(self._work_areas_provider, self._window)
        area = areas[0]
        snapshot = self._presentation_snapshot()
        try:
            current = self._geometry
            self._geometry = OverlayGeometry(
                area.right - current.width - DEFAULT_EDGE_MARGIN,
                area.top + DEFAULT_EDGE_MARGIN,
                current.width,
                current.height,
            )
            self._apply_geometry(render=False)
        except Exception:
            self._restore_presentation(snapshot)
            raise
        if notify:
            self._notify_state_change()
        return self._geometry.x, self._geometry.y

    def register_interactive_widget(self, widget: tk.Misc) -> None:
        require_main_thread()
        self._ensure_open()
        self._interactive_widgets.add(widget)

    def unregister_interactive_widget(self, widget: tk.Misc) -> None:
        require_main_thread()
        self._ensure_open()
        self._interactive_widgets.discard(widget)

    def show(self, *, notify: bool = True) -> None:
        require_main_thread()
        self._ensure_open()
        snapshot = self._presentation_snapshot()
        try:
            self._apply_geometry(render=False)
            self._window.attributes("-topmost", True)
            self._window.deiconify()
            self._window.lift()
            self._window.update_idletasks()
            self._visible = True
            if self._layers is not None:
                self._render_source()
        except Exception:
            self._restore_presentation(snapshot)
            raise
        if notify:
            self._notify_state_change()

    def hide(self, *, notify: bool = True) -> None:
        require_main_thread()
        if self._closed:
            return
        self._window.withdraw()
        self._visible = False
        if notify:
            self._notify_state_change()

    def toggle(self, *, notify: bool = True) -> None:
        require_main_thread()
        if self.visible:
            self.hide(notify=notify)
        else:
            self.show(notify=notify)

    def close(self) -> None:
        """Handle a user close request by hiding, never terminating the app."""

        self.hide()

    def destroy(self) -> None:
        """Release the native window during application shutdown."""

        require_main_thread()
        if self._closed:
            return
        self._closed = True
        self._visible = False
        self._layers = None
        self._background_layer = None
        self._last_composite = None
        self._presenter = None
        self._window.destroy()

    def _dimensions_for_scale(self, scale_percent: int) -> tuple[int, int]:
        native_width, native_height = self._native_size
        multiplier = scale_percent / 100.0
        return (
            max(1, round(native_width * multiplier)),
            max(1, round(native_height * multiplier)),
        )

    def _presentation_snapshot(self) -> _PresentationSnapshot:
        return _PresentationSnapshot(
            geometry=self._geometry,
            opacity=self._opacity,
            scale_percent=self._scale_percent,
            layers=self._layers,
            background_layer=self._background_layer,
            native_size=self._native_size,
            last_composite=self._last_composite,
            presenter=self._presenter,
            visible=self._visible,
        )

    def _restore_presentation(self, snapshot: _PresentationSnapshot) -> None:
        """Best-effort native rollback with unconditional logical rollback.

        A layered-window update can fail after Tk geometry has already changed.
        Restore all Python state first, then restore the prior window rectangle
        and pixels.  A second native failure must never replace the original
        exception or leave the persisted state pointing at the rejected value.
        """

        self._geometry = snapshot.geometry
        self._opacity = snapshot.opacity
        self._scale_percent = snapshot.scale_percent
        self._layers = snapshot.layers
        self._background_layer = snapshot.background_layer
        self._native_size = snapshot.native_size
        self._last_composite = snapshot.last_composite
        self._presenter = snapshot.presenter
        self._visible = snapshot.visible

        geometry = snapshot.geometry
        try:
            self._window.geometry(
                f"{geometry.width}x{geometry.height}"
                f"{geometry.x:+d}{geometry.y:+d}"
            )
        except Exception:
            pass

        if not snapshot.visible:
            try:
                self._window.withdraw()
            except Exception:
                pass
            return

        try:
            self._window.attributes("-topmost", True)
            self._window.deiconify()
            self._window.lift()
            self._window.update_idletasks()
        except Exception:
            pass
        if snapshot.presenter is not None and snapshot.last_composite is not None:
            try:
                snapshot.presenter.present(
                    snapshot.last_composite,
                    x=geometry.x,
                    y=geometry.y,
                )
            except Exception:
                pass

    def _apply_geometry(self, *, render: bool) -> None:
        if self._initial_work_areas is not None:
            areas = self._initial_work_areas
            self._initial_work_areas = None
        else:
            areas = _validated_work_areas(
                self._work_areas_provider,
                self._window,
            )
        clamped = clamp_overlay_geometry(self._geometry, areas)
        dimensions_changed = (
            clamped.width != self._geometry.width
            or clamped.height != self._geometry.height
        )
        self._geometry = clamped
        self._window.geometry(
            f"{clamped.width}x{clamped.height}{clamped.x:+d}{clamped.y:+d}"
        )
        if self._visible and self._layers is not None:
            # Flush Tk's queued geometry first.  Otherwise a later idle pass
            # can overwrite the absolute ULW destination (especially when
            # Tk parsed a negative offset using right/bottom semantics).
            self._window.update_idletasks()
        if (
            self._visible
            and self._layers is not None
            and (render or dimensions_changed)
        ):
            self._render_source()
        elif (
            self._visible
            and self._presenter is not None
            and self._last_composite is not None
        ):
            # Tk interprets a negative ``geometry`` offset as distance from
            # the right/bottom edge, not as a virtual-desktop coordinate.
            # ULW's destination point is absolute, so re-present the cached
            # pixels to move precisely on left/upper monitors without a
            # second HWND or a global mouse hook.
            self._presenter.present(
                self._last_composite,
                x=clamped.x,
                y=clamped.y,
            )

    def _render_source(self) -> None:
        if self._layers is None or self._background_layer is None:
            return
        size = (self._geometry.width, self._geometry.height)
        background = self._background_layer
        foreground = self._layers.foreground
        surface_mask = self._layers.surface_mask
        if background.size != size:
            background = background.resize(size, Image.Resampling.LANCZOS)
            foreground = foreground.resize(size, Image.Resampling.LANCZOS)
            surface_mask = surface_mask.resize(size, Image.Resampling.LANCZOS)
        background_rgba = background.convert("RGBA")
        background_rgba.putalpha(
            surface_mask.point(
                tuple(round(level * self._opacity) for level in range(256))
            )
        )
        composite = Image.alpha_composite(background_rgba, foreground)
        # Fully transparent layered pixels are click-through on Windows.  The
        # native-only neutral floor preserves whole-surface dragging while the
        # public semantic composite keeps the outer canvas exactly alpha-zero.
        native_composite = _with_native_hit_test_floor(composite)
        if self._presenter is None:
            self._presenter = self._presenter_factory(self._window)
        self._presenter.present(
            native_composite,
            x=self._geometry.x,
            y=self._geometry.y,
        )
        self._last_composite = native_composite

    def _is_interactive_target(self, widget: object) -> bool:
        candidate = widget
        while candidate is not None:
            if candidate in self._interactive_widgets:
                return True
            candidate = getattr(candidate, "master", None)
        return False

    def _begin_drag(self, event: tk.Event) -> None:
        if self._is_interactive_target(event.widget):
            self._drag_pointer = None
            self._drag_origin = None
            return
        self._window.focus_set()
        self._drag_pointer = (int(event.x_root), int(event.y_root))
        self._drag_origin = (self._geometry.x, self._geometry.y)
        self._drag_moved = False

    def _continue_drag(self, event: tk.Event) -> None:
        if self._drag_pointer is None or self._drag_origin is None:
            return
        delta_x = int(event.x_root) - self._drag_pointer[0]
        delta_y = int(event.y_root) - self._drag_pointer[1]
        crossed_threshold = (
            delta_x * delta_x + delta_y * delta_y
            >= DRAG_THRESHOLD * DRAG_THRESHOLD
        )
        if not self._drag_moved and not crossed_threshold:
            return
        self.set_position(
            self._drag_origin[0] + delta_x,
            self._drag_origin[1] + delta_y,
            notify=False,
        )
        self._drag_moved = True

    def _finish_drag(self, _event: tk.Event) -> None:
        moved = self._drag_moved and self._drag_pointer is not None
        self._drag_pointer = None
        self._drag_origin = None
        self._drag_moved = False
        if moved:
            self._notify_state_change()

    def _notify_state_change(self) -> None:
        if self._on_state_change is not None:
            try:
                self._on_state_change(self.state)
            except Exception as error:
                LOGGER.error(
                    "Overlay state callback failed (%s)",
                    type(error).__name__,
                )

    def _nudge(self, delta_x: int, delta_y: int) -> str:
        self.set_position(
            self._geometry.x + delta_x,
            self._geometry.y + delta_y,
        )
        return "break"

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("overlay window is closed")
