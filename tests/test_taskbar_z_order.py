# SPDX-License-Identifier: GPL-3.0-or-later

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk

import pytest
from PIL import Image

from ai_mini_monitor.ui import taskbar
from ai_mini_monitor.ui.layered_window import LayeredWindowPresenter


@pytest.fixture(autouse=True)
def windows_boundary(monkeypatch):
    monkeypatch.setattr(taskbar.sys, 'platform', 'win32')


class Function:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


class ShellApi:
    """Model only the native boundary: z order, focus, and window placement."""

    def __init__(self, order=(777, 111, 999), *, shell=111):
        self.order = list(order)
        self.shell = shell
        self.foreground = 111
        self.positions = {999: (1328, 1040, 348, 32), 111: (0, 1032, 1920, 48)}
        self.fail_raise = False
        self.FindWindowW = Function(lambda class_name, title: self.shell)
        self.EnumWindows = Function(self.enumerate)
        self.SetWindowPos = Function(self.position)

    def enumerate(self, callback, data):
        for hwnd in self.order:
            if not callback(hwnd, data):
                return 0
        return 1

    def position(self, hwnd, after, x, y, width, height, flags):
        assert (hwnd, x, y, width, height, flags) == (999, 0, 0, 0, 0, 0x213)
        if self.fail_raise:
            return 0
        self.order.remove(hwnd)
        self.order.insert(0 if after == -1 else self.order.index(after)+1, hwnd)
        return 1


def test_shell_cover_is_repaired_without_focus_position_or_size_changes():
    api = ShellApi()
    assert taskbar.restore_taskbar_z_order(999, _user32=api) is True
    assert api.order == [777, 999, 111]  # An existing popup stays above both.
    assert api.foreground == 111
    assert api.positions == {999: (1328, 1040, 348, 32), 111: (0, 1032, 1920, 48)}


@pytest.mark.parametrize('order,shell', [
    ((777, 999, 111), 111),  # Leave popup windows above the readout alone.
    ((777, 999), 0),        # Explorer may be restarting.
    ((777, 111), 111),      # The readout has already been destroyed.
    ((777, 999), 111),      # Shell disappeared between lookup and enumeration.
])
def test_safe_order_or_missing_window_does_not_raise(order, shell):
    api = ShellApi(order, shell=shell)
    assert taskbar.restore_taskbar_z_order(999, _user32=api) is False
    assert api.order == list(order)
    assert api.foreground == 111


def test_temporary_native_failure_can_retry_on_next_refresh():
    api = ShellApi()
    api.fail_raise = True
    assert taskbar.restore_taskbar_z_order(999, _user32=api) is False
    assert api.order == [777, 111, 999]
    api.fail_raise = False
    assert taskbar.restore_taskbar_z_order(999, _user32=api) is True
    assert api.order == [777, 999, 111]


@pytest.mark.skipif(sys.platform != 'win32', reason='requires real Win32 window ordering')
def test_real_topmost_cover_recovery_preserves_foreground_and_geometry():
    """Use three offscreen windows owned by this test, never modify Explorer."""
    if os.environ.get('MINI_MONITOR_ZORDER_SMOKE_CHILD') != '1':
        env = os.environ.copy()
        env['MINI_MONITOR_ZORDER_SMOKE_CHILD'] = '1'
        result = subprocess.run(
            [sys.executable, '-m', 'pytest', '-q',
             f'{Path(__file__).resolve()}::test_real_topmost_cover_recovery_preserves_foreground_and_geometry'],
            env=env, capture_output=True, timeout=30,
        )
        assert result.returncode == 0, (result.stdout+result.stderr).decode(errors='replace')
        return

    root = tk.Tk()
    root.withdraw()
    windows = []
    try:
        presenters = []
        for _ in range(3):
            window = tk.Toplevel(root)
            windows.append(window)
            window.withdraw()
            window.overrideredirect(True)
            window.update_idletasks()
            presenter = LayeredWindowPresenter(window)
            presenter.present(Image.new('RGBA', (2, 2), (255, 255, 255, 128)), x=-32000, y=-32000)
            presenters.append(presenter)
        readout, cover, popup = [presenter.hwnd for presenter in presenters]
        assert all((readout, cover, popup)) and len({readout, cover, popup}) == 3
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
        user.SetWindowPos.restype = wintypes.BOOL
        user.GetForegroundWindow.restype = wintypes.HWND
        user.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))

        class OwnWindowApi:
            FindWindowW = Function(lambda _class, _title: cover)
            EnumWindows = user.EnumWindows
            SetWindowPos = user.SetWindowPos

        for _ in range(4):
            assert user.SetWindowPos(readout, -1, -32000, -32000, 2, 2, 0x250)
            assert user.SetWindowPos(cover, -1, -32000, -32000, 2, 2, 0x250)
            assert user.SetWindowPos(popup, -1, -32000, -32000, 2, 2, 0x250)
            before = user.GetForegroundWindow()
            assert taskbar.restore_taskbar_z_order(readout, _user32=OwnWindowApi()) is True
            assert taskbar.restore_taskbar_z_order(readout, _user32=OwnWindowApi()) is False
            assert user.GetForegroundWindow() == before
            order = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def collect(hwnd, _data):
                if hwnd in (readout, cover, popup):
                    order.append(hwnd)
                return True

            assert user.EnumWindows(collect, 0)
            assert order == [popup, readout, cover]
            rect = wintypes.RECT()
            assert user.GetWindowRect(readout, ctypes.byref(rect))
            assert (rect.left, rect.top, rect.right, rect.bottom) == (-32000, -32000, -31998, -31998)
    finally:
        for window in windows:
            window.destroy()
        root.destroy()
