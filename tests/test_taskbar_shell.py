# SPDX-License-Identifier: GPL-3.0-or-later
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk

import pytest

from ai_mini_monitor.ui import taskbar


class Function:
    def __init__(self, call): self.call = call
    def __call__(self, *args): return self.call(*args)


class Shell:
    def __init__(self, dpi=192):
        self.dpi = dpi
        self.shell_visible = True
        self.bar = (0, 2064, 3840, 2160)
        self.monitor = (0, 0, 3840, 2160)
        self.client = (0, 32, 3840, 2064)
        self.foreground = 22
        self.class_name = 'Chrome_WidgetWin_1'
        self.FindWindowW = Function(lambda *_: 11)
        self.GetDpiForWindow = Function(lambda hwnd: self.dpi)
        self.IsWindowVisible = Function(lambda hwnd: self.shell_visible)
        self.GetWindowRect = Function(self.rect)
        self.MonitorFromWindow = Function(lambda *_: 33)
        self.GetMonitorInfoW = Function(self.info)
        self.GetForegroundWindow = Function(lambda: self.foreground)
        self.GetClassNameW = Function(self.class_name_to)
        self.GetClientRect = Function(self.client_rect)
        self.ClientToScreen = Function(self.client_to_screen)

    def rect(self, hwnd, pointer):
        target = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
        target.left, target.top, target.right, target.bottom = self.bar
        return 1

    def info(self, monitor, pointer):
        # MONITORINFO: DWORD cbSize, then RECT rcMonitor.
        target = wintypes.RECT.from_address(ctypes.cast(pointer, ctypes.c_void_p).value + 4)
        target.left, target.top, target.right, target.bottom = self.monitor
        return 1

    def class_name_to(self, hwnd, buffer, length):
        buffer.value = self.class_name
        return len(self.class_name)

    def client_rect(self, hwnd, pointer):
        target = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
        target.left, target.top = 0, 0
        target.right, target.bottom = self.client[2] - self.client[0], self.client[3] - self.client[1]
        return 1

    def client_to_screen(self, hwnd, pointer):
        point = ctypes.cast(pointer, ctypes.POINTER(wintypes.POINT)).contents
        point.x += self.client[0]
        point.y += self.client[1]
        return 1


@pytest.mark.parametrize('dpi,want', [(96, 100), (144, 150), (192, 200), (240, 250), (0, 100)])
def test_taskbar_reads_shell_monitor_dpi(dpi, want):
    assert taskbar.taskbar_dpi_percent(_user32=Shell(dpi)) == want


@pytest.mark.parametrize('client,want', [
    ((0, 0, 3840, 2160), False),  # Fullscreen video/game on the bar's monitor.
    ((0, 32, 3840, 2064), True),  # Maximized but not fullscreen.
    ((3840, 0, 7680, 2160), True),  # Fullscreen on another monitor.
    ((-1920, -1080, 0, 0), True),
])
def test_fullscreen_uses_client_screen_bounds_not_resize_borders(client, want):
    api = Shell()
    api.client = client
    assert taskbar.taskbar_should_be_visible(999, _user32=api) is want


@pytest.mark.parametrize('visible,bar', [
    (False, (0, 2064, 3840, 2160)),
    (True, (0, 2158, 3840, 2254)),  # Auto-hidden, only two pixels exposed.
    (True, (0, 2160, 3840, 2256)),
])
def test_hidden_or_offscreen_shell_does_not_leave_detached_readout(visible, bar):
    api = Shell()
    api.shell_visible, api.bar = visible, bar
    assert not taskbar.taskbar_should_be_visible(999, _user32=api)


@pytest.mark.parametrize('name', ['Progman', 'WorkerW', 'Shell_TrayWnd'])
def test_desktop_is_not_mistaken_for_fullscreen_video(name):
    api = Shell()
    api.client = api.monitor
    api.class_name = name
    assert taskbar.taskbar_should_be_visible(999, _user32=api)


@pytest.mark.skipif(sys.platform != 'win32', reason='native Win32 presentation')
def test_native_taskbar_suspend_resume_does_not_activate_readout(monkeypatch):
    if os.environ.get('MINI_MONITOR_TASKBAR_VISIBILITY_CHILD') != '1':
        env = dict(os.environ, MINI_MONITOR_TASKBAR_VISIBILITY_CHILD='1')
        result = subprocess.run([sys.executable, '-m', 'pytest', '-q',
                                 f'{Path(__file__).resolve()}::test_native_taskbar_suspend_resume_does_not_activate_readout'],
                                env=env, capture_output=True, timeout=30)
        assert result.returncode == 0, (result.stdout + result.stderr).decode(errors='replace')
        return
    from ai_mini_monitor.ui.overlay import WorkArea
    native = ctypes.WinDLL('user32', use_last_error=True)
    native.GetForegroundWindow.restype = wintypes.HWND
    native.IsWindowVisible.argtypes = (wintypes.HWND,)
    native.IsWindowVisible.restype = wintypes.BOOL
    allowed = [True]
    monkeypatch.setattr(taskbar, 'full_monitor_areas', lambda _root: (WorkArea(-32000, -32000, -30000, -31000),))
    monkeypatch.setattr(taskbar, 'windows_taskbar_rects', lambda: (WorkArea(-32000, -31048, -30000, -31000), WorkArea(-30200, -31048, -30000, -31000)))
    monkeypatch.setattr(taskbar, 'taskbar_dpi_percent', lambda: 100)
    monkeypatch.setattr(taskbar, 'taskbar_should_be_visible', lambda _hwnd: allowed[0])
    root = tk.Tk()
    root.withdraw()
    root.update()  # finish fixture-only Tk wrapper creation before measuring
    window = taskbar.TaskbarWindow(root)
    before = native.GetForegroundWindow()
    try:
        window.show(notify=False)
        root.update()
        native.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        native.GetAncestor.restype = wintypes.HWND
        native.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
        native.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        actual_root = native.GetAncestor(window.window.winfo_id(), 2)
        assert actual_root == window._window.hwnd
        assert native.GetForegroundWindow() == before, (window._presenter.hwnd, actual_root, hex(native.GetWindowLongPtrW(actual_root, -20)), native.GetAncestor(root.winfo_id(), 2))
        allowed[0] = False
        window.update_sensor(None)
        root.update()
        assert not native.IsWindowVisible(window._presenter.hwnd)
        assert window.state.visible
        allowed[0] = True
        window.update_sensor(None)
        root.update()
        assert native.IsWindowVisible(window._presenter.hwnd)
        assert native.GetForegroundWindow() == before
    finally:
        window.destroy()
        root.destroy()
