# SPDX-License-Identifier: GPL-3.0-or-later

"""A small, draggable sensor readout that can sit on the Windows taskbar.

This is an independent layered window.  It never injects into Explorer or
modifies the shell; the existing overlay presenter owns its native HWND.
"""

from __future__ import annotations

import ctypes
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
from .overlay import OverlayGeometry, OverlayState, OverlayWindow, WorkArea, _fallback_work_areas, default_work_areas
from .preview import require_main_thread


FRAME_SIZE = (348, 32)
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


@lru_cache(maxsize=5)
def _icon(name: str) -> Image.Image:
    path = resource_path(f"assets/icons/{name}.png")
    with Image.open(path) as source:
        icon = source.convert("RGBA").resize((18, 18), Image.Resampling.LANCZOS)
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
    if re.fullmatch(r"(?:100|[1-9]?\d)%", value) is None:
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


def taskbar_frame_size(items=DEFAULT_TASKBAR_ITEMS, style: str = "icon") -> tuple[int, int]:
    selected = validate_taskbar_options(items, style)
    # The icon readout fits even "100%" in 60px. Trim only the last cell's
    # unused transparent tail so its visible content can sit closer to the tray.
    trailing_trim = _ICON_TRAILING_TRIM if style == "icon" else 0
    return len(selected) * _STYLE_CELL_WIDTHS[style] - trailing_trim, FRAME_SIZE[1]


def render_taskbar_frame(
    sensor: SensorSnapshot | None,
    ai: AIData | None = None,
    *,
    items=DEFAULT_TASKBAR_ITEMS,
    style: str = "icon",
) -> Image.Image:
    """Render one compact semantic frame for the existing layered presenter."""

    selected = validate_taskbar_options(items, style)
    width = _STYLE_CELL_WIDTHS[style]
    size = taskbar_frame_size(selected, style)
    readings = dict(zip(DEFAULT_TASKBAR_ITEMS, _readouts(sensor, ai)))
    background = Image.new("RGB", size, (8, 8, 8))
    mask = Image.new("L", size, 0)
    foreground = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(foreground)
    for index, name in enumerate(selected):
        x = index * width
        if style in ("icon", "both"):
            foreground.alpha_composite(_icon(name), (x + 4, 7))
        if style in ("text", "both"):
            label_x = x + (26 if style == "both" else 4)
            draw.text((label_x, 16), name.upper(), font=metric_mono(10), fill=(190, 190, 190, 255), anchor="lm")
        if style == "icon":
            draw.text((x + 26, 16), readings[name], font=metric_mono(14), fill=(248, 248, 248, 255), anchor="lm")
        else:
            draw.text((x + width - 4, 16), readings[name], font=metric_mono(13), fill=(248, 248, 248, 255), anchor="rm")
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
        above = bar.top - height - _EDGE_MARGIN
        if above >= primary.top:
            return x, above
        below = bar.bottom + _EDGE_MARGIN
        if below + height <= primary.bottom:
            return x, below
    return position


class TaskbarWindow(OverlayWindow):
    """Independent, always-on-top taskbar-sized overlay with manual dragging."""

    def __init__(
        self,
        root: tk.Misc,
        *,
        position: tuple[int, int] | None = None,
        items=DEFAULT_TASKBAR_ITEMS,
        style: str = "icon",
        on_state_change: Callable[[OverlayState], None] | None = None,
    ) -> None:
        self.items = validate_taskbar_options(items, style)
        self.style = style
        self._shell_binding: tuple[WorkArea, WorkArea, WorkArea] | None = None
        size = taskbar_frame_size(self.items, self.style)
        if position is None:
            primary = full_monitor_areas(root)[0]
            desktop = default_work_areas(root)[0]
            position = choose_initial_position(primary, desktop, windows_taskbar_rects(), size)
        super().__init__(
            root,
            title="Mini Monitor · 작업 표시줄 바",
            frame_size=size,
            opacity=_SURFACE_OPACITY,
            scale_percent=100,
            position=position,
            on_state_change=on_state_change,
            work_areas_provider=full_monitor_areas,
            presenter_factory=LayeredWindowPresenter,
        )
        self._last_readouts: tuple[tuple[str, ...], str, tuple[str, ...]] | None = None
        self._sensor: SensorSnapshot | None = None
        self._ai: AIData | None = None
        self.update_sensor(None)

    def _apply_geometry(self, *, render: bool) -> None:
        primary = full_monitor_areas(self._window)[0]
        current = self._geometry
        rects = windows_taskbar_rects()
        binding = None
        if rects is not None:
            bar, notify = rects
            if (bar.width > bar.height * 3
                    and primary.left <= bar.left < bar.right <= primary.right
                    and primary.top <= bar.top < bar.bottom <= primary.bottom
                    and bar.left <= notify.left < notify.right <= bar.right
                    and bar.top <= notify.top < notify.bottom <= bar.bottom
                    and notify.left - current.width - _TRAY_GAP >= bar.left):
                binding = primary, bar, notify
        x, y = clamp_taskbar_position(
            (current.x, current.y),
            (current.width, current.height),
            primary,
            rects,
        )
        if binding is not None:
            _, bar, notify = binding
            minimum, maximum = bar.left, notify.left - current.width - _TRAY_GAP
            previous = self._shell_binding
            if previous is not None and binding != previous:
                _, old_bar, old_notify = previous
                old_range = old_notify.left - current.width - _TRAY_GAP - old_bar.left
                fraction = 1.0 if old_range <= 0 else min(1.0, max(0.0, (current.x - old_bar.left) / old_range))
                x = minimum + round((maximum - minimum) * fraction)
            elif previous is None and not (current.y < bar.bottom and current.y + current.height > bar.top):
                # Legacy absolute coordinates may still be on-screen after a
                # resolution change, but they are not a valid shell anchor.
                x = maximum
            x = min(maximum, max(minimum, x))
            y = bar.top + (bar.height - current.height) // 2
        self._shell_binding = binding
        if (x, y) != (current.x, current.y):
            self._geometry = OverlayGeometry(x, y, current.width, current.height)
        elif not render and self._initial_work_areas is None:
            return
        super()._apply_geometry(render=render)

    def _refresh_shell_position(self) -> None:
        previous = self._geometry
        self._apply_geometry(render=False)
        if self._geometry != previous:
            self._notify_state_change()

    def update_sensor(self, sensor: SensorSnapshot | None, ai: AIData | None = None) -> None:
        require_main_thread()
        self._ensure_open()
        # Shell geometry is independent of readings. Re-read even when the
        # cached sensor frame is unchanged (monitor/DPI/Explorer changes).
        self._refresh_shell_position()
        if self.visible and self._presenter is not None:
            restore_taskbar_z_order(getattr(self._presenter, "hwnd", None))
        readouts = dict(zip(DEFAULT_TASKBAR_ITEMS, _readouts(sensor, ai)))
        selected_readouts = tuple(readouts[name] for name in self.items)
        signature = (self.items, self.style, selected_readouts)
        self._sensor, self._ai = sensor, ai
        if signature == self._last_readouts:
            return
        self.update_image(render_taskbar_frame(sensor, ai, items=self.items, style=self.style))
        self._last_readouts = signature

    def set_options(self, items, style: str) -> None:
        require_main_thread()
        self._ensure_open()
        selected = validate_taskbar_options(items, style)
        if (selected, style) == (self.items, self.style):
            return
        previous = self.items, self.style, self._last_readouts
        self.items, self.style = selected, style
        self._last_readouts = None
        try:
            self.update_sensor(self._sensor, self._ai)
        except Exception:
            self.items, self.style, self._last_readouts = previous
            raise
        self._notify_state_change()

    def _horizontal_limits(self) -> tuple[int, int, bool]:
        self._refresh_shell_position()
        state = self.state
        if self._shell_binding is not None:
            _, bar, notify = self._shell_binding
            return bar.left, notify.left - state.width - _TRAY_GAP, True
        areas = full_monitor_areas(self._window)
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
