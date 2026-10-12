# SPDX-License-Identifier: GPL-3.0-or-later

"""A small, draggable sensor readout that can sit on the Windows taskbar.

This is an independent layered window.  It never injects into Explorer or
modifies the shell; a native surface owns its HWND and reuses the RGBA presenter.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from functools import lru_cache
from math import isfinite
import re
import sys
import tkinter as tk
from typing import Callable

from PIL import Image, ImageDraw

from ..config import DEFAULT_TASKBAR_ITEMS, validate_taskbar_options
from ..models import AIData, AIProviderKind, Metric, SensorSnapshot
from ..rendering.fonts import metric_mono
from ..rendering.renderer import OVERLAY_LAYERS_INFO_KEY, OverlayLayers
from ..resources import resource_path
from .layered_window import LayeredWindowPresenter
from .overlay import OverlayGeometry, OverlayState, WorkArea, _fallback_work_areas, default_work_areas, compose_overlay_rgba, _with_native_hit_test_floor
from .native_taskbar import NativeTaskbarSurface
from .preview import require_main_thread
from .taskbar_shell import taskbar_dpi_percent, taskbar_should_be_visible


FRAME_SIZE = (348, 32)
LOGGER = logging.getLogger(__name__)
_CELL_WIDTH = 72
_STYLE_CELL_WIDTHS = {"icon": 72, "text": 86, "both": 108}
_ICON_TRAILING_TRIM = 12
_TRAY_GAP = 2
_SURFACE_OPACITY = 0.0
_EDGE_MARGIN = 16


def percent_text(metric: Metric) -> str:
    """Keep absent or invalid samples distinct from a measured zero."""

    if metric.value is None:
        return "--"
    value = float(metric.value)
    if not isfinite(value) or value < 0:
        return "--"
    return f"{round(min(value, 100)):d}%"


@lru_cache(maxsize=40)
def _icon(name: str, dpi_percent: int = 100) -> Image.Image:
    path = resource_path(f"assets/icons/{name}.png")
    side = round(18 * _dpi_scale(dpi_percent))
    with Image.open(path) as source:
        icon = source.convert("RGBA").resize((side, side), Image.Resampling.LANCZOS)
    if name == "codex":
        white = Image.new("RGBA", icon.size, (255, 255, 255, 255))
        white.putalpha(icon.getchannel("A"))
        return white
    return icon


def _vram_percent(sensor: SensorSnapshot | None) -> str:
    if sensor is None or sensor.gpu_vram_used_gib.value is None or sensor.gpu_vram_total_gib.value is None:
        return "--"
    used = float(sensor.gpu_vram_used_gib.value)
    total = float(sensor.gpu_vram_total_gib.value)
    if not isfinite(used) or not isfinite(total) or used < 0 or total <= 0 or used > total:
        return "--"
    return f"{round(100 * used / total):d}%"


def _codex_remaining(ai: AIData | None) -> str:
    if ai is None:
        return "--"
    if not isinstance(ai, AIData):
        raise TypeError("ai must be an AIData or None")
    if ai.provider not in (AIProviderKind.CODEX_ACCOUNT, AIProviderKind.CODEX_LOCAL):
        return "--"
    value = ai.primary_value
    if re.fullmatch(r"(?:100|[1-9]?\d)%|(?:\d{1,3}(?:,\d{3}){0,3}|--|UNLTD) Credit", value) is None:
        return "--"
    return value


def _readouts(sensor: SensorSnapshot | None, ai: AIData | None = None) -> tuple[str, str, str, str, str]:
    if sensor is not None and not isinstance(sensor, SensorSnapshot):
        raise TypeError("sensor must be a SensorSnapshot or None")
    if sensor is None:
        return "--", "--", "--", "--", _codex_remaining(ai)
    return (
        percent_text(sensor.cpu_percent),
        percent_text(sensor.memory_percent),
        percent_text(sensor.gpu_percent),
        _vram_percent(sensor),
        _codex_remaining(ai),
    )


def _dpi_scale(dpi_percent: int) -> float:
    if isinstance(dpi_percent, bool) or not isinstance(dpi_percent, int) or not 100 <= dpi_percent <= 800:
        raise ValueError('DPI percent must be an integer from 100 to 800')
    return dpi_percent / 100


def taskbar_frame_size(items=DEFAULT_TASKBAR_ITEMS, style: str = "icon", *, dpi_percent: int = 100) -> tuple[int, int]:
    selected = validate_taskbar_options(items, style)
    # The icon readout fits even "100%" in 60px. Trim only the last cell's
    # unused transparent tail so its visible content can sit closer to the tray.
    trailing_trim = _ICON_TRAILING_TRIM if style == "icon" else 0
    scale = _dpi_scale(dpi_percent)
    return (len(selected) * round(_STYLE_CELL_WIDTHS[style] * scale) - round(trailing_trim * scale),
            round(FRAME_SIZE[1] * scale))


def _cell_width(name: str, reading: str, style: str, dpi_percent: int = 100) -> int:
    scale = _dpi_scale(dpi_percent)
    base = round(_STYLE_CELL_WIDTHS[style] * scale)
    if not reading.endswith(" Credit"):
        return base
    prefix = round((26 if style in ("icon", "both") else 4) * scale)
    if style in ("text", "both"):
        prefix += metric_mono(round(10 * scale)).getlength(name.upper()) + round(8 * scale)
    return max(base, int(prefix + metric_mono(round((14 if style == "icon" else 13) * scale)).getlength(reading) + round(5 * scale)))


def render_taskbar_frame(
    sensor: SensorSnapshot | None,
    ai: AIData | None = None,
    *,
    items=DEFAULT_TASKBAR_ITEMS,
    style: str = "icon",
    dpi_percent: int = 100,
) -> Image.Image:
    """Render one compact semantic frame for the existing layered presenter."""

    selected = validate_taskbar_options(items, style)
    scale = _dpi_scale(dpi_percent)
    px = lambda value: round(value * scale)
    readings = dict(zip(DEFAULT_TASKBAR_ITEMS, _readouts(sensor, ai)))
    widths = [_cell_width(name, readings[name], style, dpi_percent) for name in selected]
    # Trim only the percent cell's unused tail, never a full credit label.
    trim = _ICON_TRAILING_TRIM if style == "icon" and not readings[selected[-1]].endswith(" Credit") else 0
    size = (sum(widths) - px(trim), px(FRAME_SIZE[1]))
    background = Image.new("RGB", size, (8, 8, 8))
    mask = Image.new("L", size, 0)
    foreground = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(foreground)
    x = 0
    for name, width in zip(selected, widths):
        if style in ("icon", "both"):
            foreground.alpha_composite(_icon(name, dpi_percent), (x + px(4), px(7)))
        if style in ("text", "both"):
            label_x = x + px(26 if style == "both" else 4)
            draw.text((label_x, px(16)), name.upper(), font=metric_mono(px(10)), fill=(190, 190, 190, 255), anchor="lm")
        if style == "icon":
            draw.text((x + px(26), px(16)), readings[name], font=metric_mono(px(14)), fill=(248, 248, 248, 255), anchor="lm")
        else:
            draw.text((x + width - px(4), px(16)), readings[name], font=metric_mono(px(13)), fill=(248, 248, 248, 255), anchor="rm")
        x += width
    frame = Image.alpha_composite(background.convert("RGBA"), foreground).convert("RGB")
    frame.info[OVERLAY_LAYERS_INFO_KEY] = OverlayLayers(background, foreground, mask)
    return frame


def windows_monitor_bounds() -> tuple[WorkArea, ...]:
    """Return full monitor rectangles, not the taskbar-excluding work areas."""

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
        get_monitor_info.argtypes = (wintypes.HANDLE, ctypes.POINTER(MonitorInfo))
        get_monitor_info.restype = wintypes.BOOL
        collected: list[tuple[bool, WorkArea]] = []
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HANDLE,
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.LPARAM,
        )

        @callback_type
        def collect(monitor, _hdc, _rect, _data):
            info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
            if get_monitor_info(monitor, ctypes.byref(info)):
                rect = info.rcMonitor
                if rect.right > rect.left and rect.bottom > rect.top:
                    collected.append((bool(info.dwFlags & 1), WorkArea(rect.left, rect.top, rect.right, rect.bottom)))
            return True

        enum_monitors = user32.EnumDisplayMonitors
        enum_monitors.argtypes = (wintypes.HDC, ctypes.POINTER(wintypes.RECT), callback_type, wintypes.LPARAM)
        enum_monitors.restype = wintypes.BOOL
        if enum_monitors(None, None, collect, 0):
            collected.sort(key=lambda item: not item[0])
            return tuple(area for _primary, area in collected)
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return ()


def full_monitor_areas(root: tk.Misc) -> tuple[WorkArea, ...]:
    return windows_monitor_bounds() or _fallback_work_areas(root)


def windows_taskbar_rects() -> tuple[WorkArea, WorkArea] | None:
    """Read the primary taskbar and notification area without altering them."""

    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        find_window = user32.FindWindowW
        find_window.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        find_window.restype = wintypes.HWND
        find_child = user32.FindWindowExW
        find_child.argtypes = (wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
        find_child.restype = wintypes.HWND
        get_rect = user32.GetWindowRect
        get_rect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        get_rect.restype = wintypes.BOOL
        taskbar = find_window("Shell_TrayWnd", None)
        notify = find_child(taskbar, None, "TrayNotifyWnd", None) if taskbar else None
        if not taskbar:
            return None

        def bounds(handle) -> WorkArea | None:
            rect = wintypes.RECT()
            if not get_rect(handle, ctypes.byref(rect)) or rect.right <= rect.left or rect.bottom <= rect.top:
                return None
            return WorkArea(rect.left, rect.top, rect.right, rect.bottom)

        taskbar_bounds = bounds(taskbar)
        notify_bounds = bounds(notify) if notify else None
        if taskbar_bounds and notify_bounds:
            return taskbar_bounds, notify_bounds
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return None


def restore_taskbar_z_order(hwnd: int | None, *, _user32=None) -> bool:
    """Repair shell occlusion without activation, movement or owner changes.

    Explorer can promote its own topmost taskbar above our topmost window.
    Repainting does not repair that order. Only raise our HWND when the live
    shell is actually above it; leave unrelated popup windows alone otherwise.
    A missing/restarting shell or a failed native call is retried next poll,
    not treated as a rendering failure that disables the user's taskbar bar.
    """

    if sys.platform != "win32" or not hwnd:
        return False
    try:
        user32 = _user32 if _user32 is not None else ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        user32.FindWindowW.restype = wintypes.HWND
        shell = user32.FindWindowW("Shell_TrayWnd", None)
        if not shell or shell == hwnd:
            return False
        order: list[int] = []
        previous = None
        shell_predecessor = None
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def collect(handle, _data):
            nonlocal previous, shell_predecessor
            if handle == shell:
                shell_predecessor = previous
            if handle in (hwnd, shell):
                order.append(handle)
            previous = handle
            return len(order) < 2

        user32.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
        user32.EnumWindows.restype = wintypes.BOOL
        user32.EnumWindows(collect, 0)
        if order != [shell, hwnd]:
            return False
        user32.SetWindowPos.argtypes = (
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        )
        user32.SetWindowPos.restype = wintypes.BOOL
        # Insert immediately above the shell, below its existing predecessors
        # (context menus/flyouts). HWND_TOPMOST is needed only if shell is first.
        # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_NOOWNERZORDER.
        # Never reposition Explorer or activate our window.
        return bool(user32.SetWindowPos(hwnd, shell_predecessor or -1, 0, 0, 0, 0, 0x213))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def choose_initial_position(
    primary: WorkArea,
    desktop: WorkArea,
    taskbar_rects: tuple[WorkArea, WorkArea] | None,
    size: tuple[int, int] = FRAME_SIZE,
) -> tuple[int, int]:
    """Prefer the left edge of a horizontal notification area; otherwise desktop bottom-right."""

    width, height = size
    if taskbar_rects is not None:
        bar, notify = taskbar_rects
        x = notify.left - width - _TRAY_GAP
        y = bar.top + (bar.height - height) // 2
        horizontal = bar.width > bar.height * 3
        notify_inside = bar.left <= notify.left < notify.right <= bar.right and bar.top <= notify.top < notify.bottom <= bar.bottom
        if horizontal and notify_inside and primary.left <= x and x + width <= primary.right and primary.top <= y and y + height <= primary.bottom:
            return x, y
    x = desktop.right - width - _EDGE_MARGIN
    y = desktop.bottom - height - _EDGE_MARGIN
    return (
        max(primary.left, min(x, primary.right - width)),
        max(primary.top, min(y, primary.bottom - height)),
    )


def clamp_taskbar_position(
    position: tuple[int, int],
    size: tuple[int, int],
    primary: WorkArea,
    taskbar_rects: tuple[WorkArea, WorkArea] | None,
) -> tuple[int, int]:
    """Protect the primary notification area without moving off-taskbar windows."""

    if taskbar_rects is None:
        return position
    bar, notify = taskbar_rects
    x, y = position
    width, height = size
    if (bar.width <= bar.height * 3
            or not (primary.left <= bar.left < bar.right <= primary.right)
            or not (bar.left <= notify.left < notify.right <= bar.right)
            or x >= primary.right or x + width <= primary.left
            or y >= bar.bottom or y + height <= bar.top):
        return position
    safe_right = notify.left - _TRAY_GAP
    if x + width > safe_right and x < notify.right:
        if safe_right - width >= bar.left:
            return safe_right - width, y
    return position


class TaskbarWindow:
    """Native readout with logical ON independent from shell visibility."""

    def __init__(self, root: tk.Misc, *, position: tuple[int, int] | None = None,
                 items=DEFAULT_TASKBAR_ITEMS, style: str = "icon",
                 on_state_change: Callable[[OverlayState], None] | None = None) -> None:
        require_main_thread()
        self._root = root
        self.items = validate_taskbar_options(items, style)
        self.style = style
        self._on_state_change = on_state_change
        self._visible = self._closed = self._mapped = False
        self._shell_binding = None
        self._shell_binding_width = 0
        self._shell_binding_dpi = None
        self._shell_suppressed = True
        self._scale_percent = 100
        self._frame_dpi = None
        self._image = None
        self._last_readouts = None
        self._sensor = self._ai = None
        width, height = taskbar_frame_size(self.items, self.style)
        if position is None:
            position = choose_initial_position(full_monitor_areas(root)[0],
                default_work_areas(root)[0], windows_taskbar_rects(), (width, height))
        self._geometry = OverlayGeometry(*position, width, height)
        self._window = NativeTaskbarSurface(on_drag=self._drag_by)
        try:
            self._presenter = LayeredWindowPresenter(self._window)
            self.update_sensor(None)
        except BaseException:
            self._window.destroy()
            raise

    @property
    def window(self):
        return self._window

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def visible(self) -> bool:
        return self._visible and not self._closed

    @property
    def state(self) -> OverlayState:
        g = self._geometry
        return OverlayState(g.x, g.y, g.width, g.height, 0.0,
                            self._scale_percent, self.visible)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("taskbar window is closed")

    def _notify_state_change(self) -> None:
        if self._on_state_change is not None:
            try:
                self._on_state_change(self.state)
            except Exception as error:
                LOGGER.warning("taskbar state callback failed (%s)", type(error).__name__)

    def _sync(self, *, force: bool = False, notify: bool = True) -> None:
        old_geometry = self._geometry
        dpi = taskbar_dpi_percent()
        readings = dict(zip(DEFAULT_TASKBAR_ITEMS, _readouts(self._sensor, self._ai)))
        signature = (self.items, self.style, tuple(readings[name] for name in self.items))
        dirty = signature != self._last_readouts or dpi != self._frame_dpi
        if dirty:
            image = render_taskbar_frame(self._sensor, self._ai,
                items=self.items, style=self.style, dpi_percent=dpi)
            self._image = image
            self._last_readouts, self._frame_dpi = signature, dpi
        self._scale_percent = dpi
        width, height = self._image.size
        x, y = old_geometry.x, old_geometry.y
        primary = full_monitor_areas(self._root)[0]
        rects = windows_taskbar_rects()
        binding = None
        if rects is not None:
            bar, tray = rects
            if (bar.width > bar.height * 3 and height <= bar.height
                    and primary.left <= bar.left < bar.right <= primary.right
                    and primary.top <= bar.top < bar.bottom <= primary.bottom
                    and bar.left <= tray.left < tray.right <= bar.right
                    and bar.top <= tray.top < tray.bottom <= bar.bottom
                    and tray.left - width - _TRAY_GAP >= bar.left):
                binding = primary, bar, tray
        if binding is not None:
            _, bar, tray = binding
            minimum, maximum = bar.left, tray.left - width - _TRAY_GAP
            previous = self._shell_binding
            if previous is not None and (previous != binding or self._shell_binding_dpi != dpi):
                _, old_bar, old_tray = previous
                old_range = old_tray.left - self._shell_binding_width - _TRAY_GAP - old_bar.left
                fraction = 1.0 if old_range <= 0 else min(1.0, max(0.0, (x - old_bar.left) / old_range))
                x = minimum + round((maximum - minimum) * fraction)
            elif previous is None and not (y < bar.bottom and y + height > bar.top):
                x = maximum
            x = min(maximum, max(minimum, x))
            y = bar.top + (bar.height - height) // 2
            self._shell_binding, self._shell_binding_width = binding, width
            self._shell_binding_dpi = dpi
        # Keep the last VALID binding across shell loss for relative recovery.
        self._geometry = OverlayGeometry(x, y, width, height)
        self._shell_suppressed = binding is None or not taskbar_should_be_visible(self._window.hwnd)
        should_map = self.visible and not self._shell_suppressed
        try:
            if should_map:
                if dirty or force or not self._mapped or self._geometry != old_geometry:
                    rgba = compose_overlay_rgba(self._image, 0.0)
                    self._presenter.present(_with_native_hit_test_floor(rgba), x=x, y=y)
                if not self._mapped:
                    self._window.show()
                    self._mapped = True
                restore_taskbar_z_order(self._window.hwnd)
            else:
                self._window.hide()
                self._mapped = False
        except Exception:
            self._window.hide()
            self._mapped = False
            raise
        if notify and self._geometry != old_geometry:
            self._notify_state_change()

    def _refresh_shell_position(self) -> None:
        self._sync()

    def update_sensor(self, sensor: SensorSnapshot | None, ai: AIData | None = None) -> None:
        require_main_thread()
        self._ensure_open()
        _readouts(sensor, ai)  # validate before replacing the latest cache
        self._sensor, self._ai = sensor, ai
        self._sync()

    def show(self, *, notify: bool = True) -> None:
        require_main_thread()
        self._ensure_open()
        previous = self._visible
        self._visible = True
        try:
            self._sync(force=True, notify=False)
        except Exception:
            self._visible = previous
            raise
        if notify:
            self._notify_state_change()

    def hide(self, *, notify: bool = True) -> None:
        require_main_thread()
        if self._closed:
            return
        self._window.hide()
        self._visible = self._mapped = False
        if notify:
            self._notify_state_change()

    def destroy(self) -> None:
        require_main_thread()
        if not self._closed:
            self._window.destroy()
            self._closed = True
            self._visible = self._mapped = False

    def set_position(self, x: int, y: int, *, notify: bool = True) -> tuple[int, int]:
        require_main_thread()
        self._ensure_open()
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (x, y)):
            raise ValueError("taskbar position must contain integers")
        g = self._geometry
        self._geometry = OverlayGeometry(x, y, g.width, g.height)
        try:
            self._sync(force=True, notify=False)
        except Exception:
            self._geometry = g
            raise
        if notify:
            self._notify_state_change()
        return self.state.x, self.state.y

    def _drag_by(self, delta_x: int) -> None:
        self.set_position(self.state.x + delta_x, self.state.y)

    def set_options(self, items, style: str) -> None:
        require_main_thread()
        self._ensure_open()
        selected = validate_taskbar_options(items, style)
        if (selected, style) == (self.items, self.style):
            return
        previous = self.items, self.style
        self.items, self.style = selected, style
        try:
            self._sync()
        except Exception:
            self.items, self.style = previous
            self._last_readouts = None
            self._sync()
            raise
        self._notify_state_change()

    def _horizontal_limits(self) -> tuple[int, int, bool]:
        self._refresh_shell_position()
        state = self.state
        if self._shell_binding is not None and not self._shell_suppressed:
            _, bar, notify = self._shell_binding
            return bar.left, notify.left - state.width - _TRAY_GAP, True
        areas = full_monitor_areas(self._root)
        center_x = state.x + state.width / 2
        center_y = state.y + state.height / 2
        monitor = next(
            (area for area in areas if area.left <= center_x < area.right and area.top <= center_y < area.bottom),
            areas[0],
        )
        minimum = monitor.left
        maximum = max(minimum, monitor.right - state.width)
        taskbar_rects = windows_taskbar_rects()
        on_primary_taskbar = False
        if monitor == areas[0] and taskbar_rects is not None:
            bar, notify = taskbar_rects
            if (bar.width > bar.height * 3
                    and monitor.left <= bar.left < bar.right <= monitor.right
                    and bar.left <= notify.left < notify.right <= bar.right
                    and state.y < bar.bottom and state.y + state.height > bar.top):
                protected_maximum = notify.left - state.width - _TRAY_GAP
                if protected_maximum >= minimum:
                    maximum = min(maximum, protected_maximum)
                    on_primary_taskbar = True
        return minimum, maximum, on_primary_taskbar

    def horizontal_position(self) -> tuple[float, int]:
        """Return the live percentage and x coordinate on the current monitor."""

        minimum, maximum, _on_taskbar = self._horizontal_limits()
        x = self.state.x
        percent = 0.0 if maximum == minimum else 100.0 * (x - minimum) / (maximum - minimum)
        return round(min(100.0, max(0.0, percent)), 2), x

    def set_horizontal_percent(self, percent: float, *, notify: bool = True) -> tuple[float, int]:
        if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not isfinite(float(percent)) or not 0 <= percent <= 100:
            raise ValueError("horizontal percent must be a finite number from 0 to 100")
        minimum, maximum, _on_taskbar = self._horizontal_limits()
        x = minimum + round((maximum - minimum) * float(percent) / 100.0)
        self.set_position(x, self.state.y, notify=notify)
        return self.horizontal_position()

    def nudge_horizontal(self, delta: int, *, notify: bool = True) -> tuple[float, int]:
        if isinstance(delta, bool) or delta not in (-1, 1):
            raise ValueError("horizontal nudge must be -1 or 1 pixel")
        minimum, maximum, _on_taskbar = self._horizontal_limits()
        x = min(maximum, max(minimum, self.state.x + delta))
        self.set_position(x, self.state.y, notify=notify)
        return self.horizontal_position()

    def reset_horizontal_position(self, *, notify: bool = True) -> tuple[float, int]:
        minimum, maximum, on_taskbar = self._horizontal_limits()
        x = maximum if on_taskbar else max(minimum, maximum - _EDGE_MARGIN)
        self.set_position(x, self.state.y, notify=notify)
        return self.horizontal_position()
