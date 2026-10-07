# SPDX-License-Identifier: GPL-3.0-or-later

"""Load the same bundled application mark for Tk and the notification area."""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk

from PIL import Image, ImageTk

from ..resources import resource_path


APP_USER_MODEL_ID = "contentriumkorea.MiniMonitor"


def set_process_app_id() -> None:
    """Give the taskbar a stable product identity before any UI appears."""

    if sys.platform != "win32":
        return
    set_id = ctypes.WinDLL("shell32", use_last_error=True).SetCurrentProcessExplicitAppUserModelID
    set_id.argtypes = (ctypes.c_wchar_p,)
    set_id.restype = ctypes.c_long
    result = set_id(APP_USER_MODEL_ID)
    if result != 0:
        raise OSError(f"could not set taskbar application ID (HRESULT {result & 0xFFFFFFFF:#010x})")


def load_icon_image() -> Image.Image:
    with Image.open(resource_path("assets/app-icon.png")) as source:
        return source.convert("RGBA")


def set_window_icon(window: tk.Misc) -> ImageTk.PhotoImage:
    photo = ImageTk.PhotoImage(load_icon_image(), master=window)
    if sys.platform == "win32":
        window.iconbitmap(str(resource_path("assets/app-icon.ico")))
    window.iconphoto(True, photo)
    # Tcl keeps the image name, not the Python object that backs it.
    window._app_icon_photo = photo
    return photo
