# SPDX-License-Identifier: GPL-3.0-or-later
"""Taskbar-only Win32 surface, driven by the existing Tk UI message loop."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import sys
from typing import Callable
from uuid import uuid4

from .preview import require_main_thread

LOGGER = logging.getLogger(__name__)
LRESULT = ctypes.c_ssize_t
WNDPROC = getattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE)(
    LRESULT, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.UINT), ('style', wintypes.UINT),
                ('lpfnWndProc', WNDPROC), ('cbClsExtra', ctypes.c_int),
                ('cbWndExtra', ctypes.c_int), ('hInstance', wintypes.HINSTANCE),
                ('hIcon', wintypes.HANDLE), ('hCursor', wintypes.HANDLE),
                ('hbrBackground', wintypes.HANDLE), ('lpszMenuName', wintypes.LPCWSTR),
                ('lpszClassName', wintypes.LPCWSTR), ('hIconSm', wintypes.HANDLE)]


# Windows holds raw callback pointers. Keep callbacks alive until their class
# has been successfully unregistered, even if a cleanup call fails.
_LIVE_SURFACES: dict[str, NativeTaskbarSurface] = {}


def _user32():
    return ctypes.WinDLL('user32', use_last_error=True)


class NativeTaskbarSurface:
    def __init__(self, *, on_drag: Callable[[int], None]) -> None:
        require_main_thread()
        if sys.platform != 'win32':
            raise OSError('Native taskbar requires Windows')
        self.hwnd = 0
        self._registered = False
        self._drag_x: int | None = None
        self._on_drag = on_drag
        self._class_name = 'MiniMonitorTaskbar_' + uuid4().hex
        self._user = _user32()
        self._configure_api()
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        kernel.GetModuleHandleW.restype = wintypes.HMODULE
        self._instance = kernel.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self._dispatch)
        info = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW),
                          lpfnWndProc=self._wndproc, hInstance=self._instance,
                          lpszClassName=self._class_name)
        if not self._user.RegisterClassExW(ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        self._registered = True
        _LIVE_SURFACES[self._class_name] = self
        try:
            self.hwnd = int(self._user.CreateWindowExW(
                0x08080080, self._class_name, 'Mini Monitor · 작업 표시줄 바',
                0x80000000, -32000, -32000, 1, 1, None, None, self._instance, None) or 0)
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.destroy()
            raise

    def _configure_api(self) -> None:
        for name, args, result in (
            ('RegisterClassExW', (ctypes.POINTER(WNDCLASSEXW),), wintypes.ATOM),
            ('UnregisterClassW', (wintypes.LPCWSTR, wintypes.HINSTANCE), wintypes.BOOL),
            ('CreateWindowExW', (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                wintypes.HINSTANCE, ctypes.c_void_p), wintypes.HWND),
            ('DefWindowProcW', (wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t), LRESULT),
            ('DestroyWindow', (wintypes.HWND,), wintypes.BOOL),
            ('ShowWindow', (wintypes.HWND, ctypes.c_int), wintypes.BOOL),
            ('SetWindowPos', (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT), wintypes.BOOL),
            ('SetCapture', (wintypes.HWND,), wintypes.HWND),
            ('GetCapture', (), wintypes.HWND),
            ('ReleaseCapture', (), wintypes.BOOL),
            ('ClientToScreen', (wintypes.HWND, ctypes.POINTER(wintypes.POINT)), wintypes.BOOL),
        ):
            function = getattr(self._user, name)
            function.argtypes, function.restype = args, result

    def winfo_id(self) -> int:
        if not self.hwnd:
            raise RuntimeError('taskbar surface is closed')
        return self.hwnd

    def show(self) -> None:
        require_main_thread()
        hwnd = self.winfo_id()
        # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE. Explorer is never changed.
        if not self._user.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x13):
            raise ctypes.WinError(ctypes.get_last_error())
        self._user.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE

    def hide(self) -> None:
        require_main_thread()
        self._cancel_drag()
        if self.hwnd:
            self._user.ShowWindow(self.hwnd, 0)

    def destroy(self) -> None:
        require_main_thread()
        self._cancel_drag()
        if self.hwnd:
            if not self._user.DestroyWindow(self.hwnd):
                raise ctypes.WinError(ctypes.get_last_error())
            self.hwnd = 0
        if self._registered:
            if not self._user.UnregisterClassW(self._class_name, self._instance):
                raise ctypes.WinError(ctypes.get_last_error())
            self._registered = False
            _LIVE_SURFACES.pop(self._class_name, None)

    def _cancel_drag(self) -> None:
        self._drag_x = None
        if self.hwnd and self._user.GetCapture() == self.hwnd:
            self._user.ReleaseCapture()

    def _screen_x(self, hwnd: int, lparam: int) -> int:
        point = wintypes.POINT(ctypes.c_short(lparam & 0xFFFF).value,
                               ctypes.c_short((lparam >> 16) & 0xFFFF).value)
        if not self._user.ClientToScreen(hwnd, ctypes.byref(point)):
            raise ctypes.WinError(ctypes.get_last_error())
        return point.x

    def _dispatch(self, hwnd, message, wparam, lparam):
        try:
            if message == 0x21:  # WM_MOUSEACTIVATE: keep click but never activate
                return 3
            if message == 0x201:  # WM_LBUTTONDOWN
                self._drag_x = self._screen_x(hwnd, lparam)
                self._user.SetCapture(hwnd)
                return 0
            if message == 0x200 and self._drag_x is not None:
                if not wparam & 1 or self._user.GetCapture() != hwnd:
                    self._cancel_drag()
                else:
                    x = self._screen_x(hwnd, lparam)
                    delta, self._drag_x = x - self._drag_x, x
                    if delta:
                        self._on_drag(delta)
                return 0
            if message in (0x202, 0x1F, 0x215):  # up/cancel/capture changed
                self._cancel_drag()
                return 0
            if message == 0x82:  # WM_NCDESTROY
                self._cancel_drag()
                self.hwnd = 0
        except BaseException as error:
            self._cancel_drag()
            LOGGER.warning('native taskbar input failed (%s)', type(error).__name__)
            return 0
        return self._user.DefWindowProcW(hwnd, message, wparam, lparam)
