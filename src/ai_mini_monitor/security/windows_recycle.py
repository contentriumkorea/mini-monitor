"""Recycle one validated local directory, never fall back to permanent deletion."""

from __future__ import annotations

import ctypes
import os
import uuid
from pathlib import Path


def recycle_directory(path: Path) -> bool:
    if os.name != "nt" or not path.is_absolute() or not path.is_dir():
        return False
    # Shell recycling is not available on network shares. Do not silently
    # substitute a permanent delete for a recoverable local operation.
    if len(path.drive) != 2 or path.drive[1] != ":":
        return False
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("bytes", ctypes.c_ubyte * 16)]

        def __init__(self, value):
            super().__init__((ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le))

    def call(pointer, index, types, *args):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        result = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[index])(pointer, *args)
        if result < 0:
            raise OSError(f"Windows recycle operation failed: {result & 0xffffffff:08x}")

    ole = ctypes.OleDLL("ole32")
    shell = ctypes.WinDLL("shell32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                   ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    shell.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p,
                                                ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    shell.SHCreateItemFromParsingName.restype = ctypes.c_long
    operation, item = ctypes.c_void_p(), ctypes.c_void_p()
    initialized = False
    try:
        ole.CoInitializeEx(None, 2)  # Shell file operations use an STA worker.
        initialized = True
        ole.CoCreateInstance(ctypes.byref(GUID("3ad05575-8857-4850-9277-11b85bdb8e09")), None, 1,
                             ctypes.byref(GUID("947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8")), ctypes.byref(operation))
        # FOFX_RECYCLEONDELETE, EARLYFAILURE, NOCOPYHOOKS, NO_CONNECTED_ELEMENTS,
        # and no UI. Unlike FOF_ALLOWUNDO, this explicitly requires recycling.
        call(operation, 5, [wintypes.DWORD], 0x00080000 | 0x00100000 | 0x00800000 | 0x2000 | 0x614)
        result = shell.SHCreateItemFromParsingName(str(path), None,
                   ctypes.byref(GUID("43826d1e-e718-42ee-bc55-a1e261c37bfe")), ctypes.byref(item))
        if result < 0:
            return False
        call(operation, 18, [ctypes.c_void_p, ctypes.c_void_p], item, None)  # DeleteItem
        call(operation, 21, [])  # PerformOperations
        aborted = wintypes.BOOL()
        call(operation, 22, [ctypes.POINTER(wintypes.BOOL)], ctypes.byref(aborted))
        return not aborted.value and not path.exists()
    except (OSError, ValueError):
        return False
    finally:
        for pointer in (item, operation):
            if pointer.value:
                call(pointer, 2, [])  # IUnknown::Release
        if initialized:
            ole.CoUninitialize()
