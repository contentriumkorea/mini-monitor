# SPDX-License-Identifier: GPL-3.0-or-later

"""Windows single-instance guard backed by a named kernel mutex.

The guard only detects another instance.  It deliberately does not enumerate,
signal, terminate, or otherwise manipulate the existing process.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from types import TracebackType
from typing import Any, Callable, Final


ERROR_ALREADY_EXISTS: Final[int] = 183
DEFAULT_MUTEX_NAME: Final[str] = (
    r"Local\AI-Mini-Monitor-7C39A56E-8751-48D5-8D20-4A9E65007FB1"
)


def _load_kernel32() -> Any:
    if sys.platform != "win32":
        raise OSError("the AI Mini Monitor single-instance guard requires Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    )
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


class SingleInstance:
    """Own a session-local mutex for as long as this object is acquired."""

    def __init__(
        self,
        name: str = DEFAULT_MUTEX_NAME,
        *,
        _kernel32: Any | None = None,
        _get_last_error: Callable[[], int] | None = None,
        _set_last_error: Callable[[int], None] | None = None,
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("mutex name must be a non-empty string")
        if "\x00" in name:
            raise ValueError("mutex name must not contain a NUL character")
        self.name = name
        self._kernel32 = _kernel32 if _kernel32 is not None else _load_kernel32()
        self._get_last_error = _get_last_error or ctypes.get_last_error
        self._set_last_error = _set_last_error or ctypes.set_last_error
        self._handle: int | None = None
        self._already_running = False

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    @property
    def already_running(self) -> bool:
        """Whether the most recent acquisition found an existing mutex."""

        return self._already_running

    def acquire(self) -> bool:
        """Try to own the mutex; return false when another instance owns it."""

        if self._handle is not None:
            return True

        self._already_running = False
        self._set_last_error(0)
        handle = self._kernel32.CreateMutexW(None, False, self.name)
        error = self._get_last_error()
        if not handle:
            raise ctypes.WinError(error)

        if error == ERROR_ALREADY_EXISTS:
            if not self._kernel32.CloseHandle(handle):
                raise ctypes.WinError(self._get_last_error())
            self._already_running = True
            return False

        self._handle = handle
        return True

    def release(self) -> None:
        """Close this process's mutex handle, if held."""

        handle = self._handle
        if handle is None:
            return
        if not self._kernel32.CloseHandle(handle):
            raise ctypes.WinError(self._get_last_error())
        self._handle = None

    def __enter__(self) -> "SingleInstance":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

    def __del__(self) -> None:
        # Destructors must not hide another exception during interpreter exit.
        try:
            self.release()
        except Exception:
            pass


def acquire_single_instance(
    name: str = DEFAULT_MUTEX_NAME,
) -> SingleInstance | None:
    """Return an acquired guard, or ``None`` when the app is already running."""

    guard = SingleInstance(name)
    return guard if guard.acquire() else None
