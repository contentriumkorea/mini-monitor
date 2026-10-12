# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only Explorer DPI and fullscreen visibility queries.

Coordinates are screen pixels in the caller's PerMonitorV2 context. Only the
readout is hidden/resized by callers; these queries never manipulate Explorer.
"""
import ctypes
from ctypes import wintypes
import sys


class MonitorInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]


def taskbar_dpi_percent(*, _user32=None) -> int:
    if sys.platform != 'win32' and _user32 is None:
        return 100
    try:
        user = _user32 if _user32 is not None else ctypes.WinDLL('user32', use_last_error=True)
        user.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        user.FindWindowW.restype = wintypes.HWND
        user.GetDpiForWindow.argtypes = (wintypes.HWND,)
        user.GetDpiForWindow.restype = wintypes.UINT
        shell = user.FindWindowW('Shell_TrayWnd', None)
        dpi = user.GetDpiForWindow(shell) if shell else 0
        return round(dpi * 100 / 96) if 96 <= dpi <= 768 else 100
    except (AttributeError, OSError, TypeError, ValueError):
        return 100


def taskbar_should_be_visible(hwnd: int | None = None, *, _user32=None) -> bool:
    if sys.platform != 'win32' and _user32 is None:
        return False
    try:
        user = _user32 if _user32 is not None else ctypes.WinDLL('user32', use_last_error=True)
        user.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        user.FindWindowW.restype = wintypes.HWND
        user.IsWindowVisible.argtypes = (wintypes.HWND,)
        user.IsWindowVisible.restype = wintypes.BOOL
        user.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        user.GetWindowRect.restype = wintypes.BOOL
        user.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
        user.MonitorFromWindow.restype = wintypes.HANDLE
        user.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MonitorInfo))
        user.GetMonitorInfoW.restype = wintypes.BOOL
        shell = user.FindWindowW('Shell_TrayWnd', None)
        if not shell or not user.IsWindowVisible(shell):
            return False
        bar = wintypes.RECT()
        info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
        monitor = user.MonitorFromWindow(shell, 2)
        if not monitor or not user.GetMonitorInfoW(monitor, ctypes.byref(info)) or not user.GetWindowRect(shell, ctypes.byref(bar)):
            return False
        screen = info.rcMonitor
        width, height = bar.right - bar.left, bar.bottom - bar.top
        visible_width = max(0, min(bar.right, screen.right) - max(bar.left, screen.left))
        visible_height = max(0, min(bar.bottom, screen.bottom) - max(bar.top, screen.top))
        # Auto-hide may leave a 1-2px strip while IsWindowVisible remains true.
        if width <= 0 or height <= 0 or visible_width < max(4, min(width, height) // 2) or visible_height < max(4, min(width, height) // 2):
            return False
        user.GetForegroundWindow.restype = wintypes.HWND
        foreground = user.GetForegroundWindow()
        if not foreground or foreground in (shell, hwnd):
            return True
        user.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        user.GetClassNameW.restype = ctypes.c_int
        name = ctypes.create_unicode_buffer(256)
        user.GetClassNameW(foreground, name, len(name))
        if name.value in ('Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd'):
            return True
        user.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        user.GetClientRect.restype = wintypes.BOOL
        user.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
        user.ClientToScreen.restype = wintypes.BOOL
        client = wintypes.RECT()
        if not user.GetClientRect(foreground, ctypes.byref(client)):
            return False
        start = wintypes.POINT(client.left, client.top)
        end = wintypes.POINT(client.right, client.bottom)
        if not user.ClientToScreen(foreground, ctypes.byref(start)) or not user.ClientToScreen(foreground, ctypes.byref(end)):
            return False
        # Client bounds distinguish a maximized bordered window from F11/video
        # fullscreen. Fullscreen on a different monitor does not cover this bar.
        fullscreen = (start.x <= screen.left and start.y <= screen.top
                      and end.x >= screen.right and end.y >= screen.bottom)
        return not fullscreen
    except (AttributeError, OSError, TypeError, ValueError):
        return False
