# SPDX-License-Identifier: GPL-3.0-or-later

"""Safe WM_POWERBROADCAST dispatch for the Tk desktop window."""

from __future__ import annotations

import ctypes
import logging
import os
import threading
from collections.abc import Callable
from typing import Any, Protocol


LOGGER = logging.getLogger(__name__)

WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMECRITICAL = 0x0006
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012
GWL_WNDPROC = -4

_RESUME_EVENTS = frozenset(
    {
        PBT_APMRESUMECRITICAL,
        PBT_APMRESUMESUSPEND,
        PBT_APMRESUMEAUTOMATIC,
    }
)

_LONG_PTR = ctypes.c_ssize_t
_HWND = ctypes.c_void_p
_UINT = ctypes.c_uint
_WPARAM = ctypes.c_size_t
_LPARAM = ctypes.c_ssize_t
_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_WNDPROC = _WINFUNCTYPE(_LONG_PTR, _HWND, _UINT, _WPARAM, _LPARAM)


class PowerWindow(Protocol):
    def winfo_id(self) -> int: ...

    def update_idletasks(self) -> None: ...


class WindowProcedureApi(Protocol):
    def parent_of(self, hwnd: int) -> int: ...

    def wrap_wndproc(self, callback: Callable[[int, int, int, int], int]) -> Any: ...

    def replace_wndproc(self, hwnd: int, replacement: Any) -> int: ...

    def call_wndproc(
        self,
        original: int,
        hwnd: int,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int: ...


class _CtypesWindowProcedureApi:
    """Pointer-width-correct user32 wrapper used only on Windows."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows power notifications require Windows")

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._get_parent = user32.GetParent
        self._get_parent.argtypes = [_HWND]
        self._get_parent.restype = _HWND

        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self._set_wndproc = user32.SetWindowLongPtrW
            self._set_wndproc.argtypes = [_HWND, ctypes.c_int, _LONG_PTR]
            self._set_wndproc.restype = _LONG_PTR
        else:
            # SetWindowLongPtrW is a macro to SetWindowLongW on 32-bit Windows.
            self._set_wndproc = user32.SetWindowLongW
            self._set_wndproc.argtypes = [_HWND, ctypes.c_int, ctypes.c_long]
            self._set_wndproc.restype = ctypes.c_long

        self._call_wndproc = user32.CallWindowProcW
        self._call_wndproc.argtypes = [
            ctypes.c_void_p,
            _HWND,
            _UINT,
            _WPARAM,
            _LPARAM,
        ]
        self._call_wndproc.restype = _LONG_PTR

    def parent_of(self, hwnd: int) -> int:
        return int(self._get_parent(_HWND(hwnd)) or 0)

    def wrap_wndproc(self, callback: Callable[[int, int, int, int], int]) -> Any:
        return _WNDPROC(callback)

    def replace_wndproc(self, hwnd: int, replacement: Any) -> int:
        if isinstance(replacement, int):
            pointer = replacement
        else:
            pointer = int(ctypes.cast(replacement, ctypes.c_void_p).value or 0)
        ctypes.set_last_error(0)
        previous = self._set_wndproc(_HWND(hwnd), GWL_WNDPROC, pointer)
        error = ctypes.get_last_error()
        if not previous and error:
            raise ctypes.WinError(error)
        return int(previous)

    def call_wndproc(
        self,
        original: int,
        hwnd: int,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        return int(
            self._call_wndproc(
                ctypes.c_void_p(original),
                _HWND(hwnd),
                message,
                wparam,
                lparam,
            )
        )


class WindowsPowerEventHook:
    """Subclass a Tk top-level HWND and forward Windows power notifications.

    ``close`` restores the original WndProc and must be called before Tk
    destroys the corresponding native window.  Callback exceptions never cross
    the native WndProc boundary.
    """

    def __init__(
        self,
        window: PowerWindow,
        *,
        on_suspend: Callable[[], object],
        on_resume: Callable[[], object],
        api: WindowProcedureApi | None = None,
    ) -> None:
        self._window = window
        self._on_suspend = on_suspend
        self._on_resume = on_resume
        self._api = api
        self._hwnd: int | None = None
        self._original_wndproc: int | None = None
        self._callback: Any = None

    @property
    def installed(self) -> bool:
        return self._original_wndproc is not None

    @property
    def hwnd(self) -> int | None:
        return self._hwnd

    def install(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("the Tk power-event hook must be installed on the main thread")
        if self.installed:
            return
        if self._api is None:
            self._api = _CtypesWindowProcedureApi()

        # Ensure Tk has materialized its native window before querying the ID.
        self._window.update_idletasks()
        content_hwnd = int(self._window.winfo_id())
        if content_hwnd <= 0:
            raise RuntimeError("Tk did not provide a valid native window handle")
        parent = self._api.parent_of(content_hwnd)
        hwnd = parent or content_hwnd
        callback = self._api.wrap_wndproc(self._wndproc)
        try:
            original = self._api.replace_wndproc(hwnd, callback)
        except BaseException:
            self._callback = None
            self._hwnd = None
            raise
        self._hwnd = hwnd
        self._callback = callback  # Keep the native callback alive until close.
        self._original_wndproc = original

    def close(self) -> None:
        if self._original_wndproc is None:
            return
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("the Tk power-event hook must be closed on the main thread")

        original = self._original_wndproc
        hwnd = self._hwnd
        if self._api is not None and hwnd is not None:
            # Keep the Python callback strongly referenced if restoration
            # fails; releasing it while Windows still points at it could crash
            # the process. A caller may safely retry close before destroying Tk.
            self._api.replace_wndproc(hwnd, original)
        self._original_wndproc = None
        self._hwnd = None
        self._callback = None

    def _wndproc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        original = self._original_wndproc
        try:
            if message == WM_POWERBROADCAST:
                if wparam == PBT_APMSUSPEND:
                    completed = self._on_suspend()
                    if completed is False:
                        LOGGER.error(
                            "serial suspension did not complete before the bounded timeout"
                        )
                elif wparam in _RESUME_EVENTS:
                    self._on_resume()
        except BaseException:
            # A Python exception must never unwind through a Windows callback.
            LOGGER.exception("Windows power-event callback failed")

        if self._api is None or original is None:
            return 0
        return self._api.call_wndproc(
            original,
            int(hwnd),
            int(message),
            int(wparam),
            int(lparam),
        )


__all__ = [
    "PBT_APMRESUMEAUTOMATIC",
    "PBT_APMRESUMECRITICAL",
    "PBT_APMRESUMESUSPEND",
    "PBT_APMSUSPEND",
    "WM_POWERBROADCAST",
    "WindowsPowerEventHook",
]
