# SPDX-License-Identifier: GPL-3.0-or-later

"""The desktop, tray and frozen executables share one recognizable mark."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from PIL import Image

from ai_mini_monitor import cli
from ai_mini_monitor.config import AppConfig
from ai_mini_monitor.ui.tray import TrayController


ROOT = Path(__file__).resolve().parents[1]
PNG = ROOT / "assets" / "app-icon.png"
ICO = ROOT / "assets" / "app-icon.ico"


def test_icon_assets_have_transparent_exterior_and_small_ico_frames() -> None:
    with Image.open(PNG) as source:
        image = source.convert("RGBA")
    assert image.size == (256, 256)
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((128, 64))[3] == 255
    assert image.getpixel((128, 128))[0] > image.getpixel((128, 64))[0]
    with Image.open(ICO) as icon:
        assert {16, 24, 32, 48, 64, 128, 256}.issubset(
            {size[0] for size in icon.info["sizes"]}
        )


def test_tray_uses_the_shared_app_icon_pixels() -> None:
    controller = TrayController()
    with Image.open(PNG) as source:
        expected = source.convert("RGBA")
    assert controller._icon_image.convert("RGBA").tobytes() == expected.tobytes()


def test_tk_registers_shared_icon_on_a_real_window() -> None:
    code = """
import ctypes
import tkinter as tk
from ctypes import wintypes
from ai_mini_monitor.ui.app_icon import set_window_icon
window = tk.Tk(className="AIMiniMonitorIconTest")
window.withdraw()
photo = set_window_icon(window)
assert (photo.width(), photo.height()) == (256, 256)
assert str(photo) in window.tk.call("image", "names")
assert window._app_icon_photo is photo
if __import__("sys").platform == "win32":
    user32 = ctypes.WinDLL("user32")
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.SendMessageW.restype = ctypes.c_void_p
    hwnd = user32.GetAncestor(window.winfo_id(), 2)
    assert hwnd
    assert user32.SendMessageW(hwnd, 0x7F, 0, 0)
    assert user32.SendMessageW(hwnd, 0x7F, 1, 0)
window.destroy()
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_windows_process_identity_is_registered_with_the_shell() -> None:
    code = """
import ctypes
from ai_mini_monitor.ui.app_icon import APP_USER_MODEL_ID, set_process_app_id
set_process_app_id()
value = ctypes.c_wchar_p()
shell = ctypes.WinDLL("shell32")
shell.GetCurrentProcessExplicitAppUserModelID.argtypes = (ctypes.POINTER(ctypes.c_wchar_p),)
shell.GetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
assert shell.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(value)) == 0
try:
    assert value.value == APP_USER_MODEL_ID
finally:
    ctypes.WinDLL("ole32").CoTaskMemFree(value)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_desktop_entry_sets_process_identity_before_window(monkeypatch, tmp_path) -> None:
    events: list[str] = []
    monkeypatch.setattr(cli, "resolve_update_config", lambda: tmp_path / "config.json")
    monkeypatch.setattr(cli, "load_config", lambda _path: AppConfig())
    monkeypatch.setattr(cli, "setup_logging", lambda _level: None)
    monkeypatch.setattr(cli, "shutdown_logging", lambda _logger: None)
    monkeypatch.setattr(cli, "set_process_app_id", lambda: events.append("identity"))
    monkeypatch.setattr(cli, "run_desktop", lambda *_args, **_kwargs: events.append("desktop") or 0)

    assert cli.main(["--no-serial"]) == 0
    assert events == ["identity", "desktop"]
