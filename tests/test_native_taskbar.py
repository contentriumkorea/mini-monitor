"""Exercise our own Win32 windows; never alter Explorer or user windows."""
import ctypes
from ctypes import wintypes
from functools import wraps
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PIL import Image


def isolated_windows(test):
    @wraps(test)
    def run(*args, **kwargs):
        if sys.platform != 'win32':
            pytest.skip('Win32 native surface')
        if os.environ.get('MINI_MONITOR_NATIVE_CHILD') == '1':
            return test(*args, **kwargs)
        result = subprocess.run(
            [sys.executable, '-m', 'pytest', '-q', f'{Path(__file__).resolve()}::{test.__name__}'],
            env=dict(os.environ, MINI_MONITOR_NATIVE_CHILD='1'), capture_output=True, timeout=30)
        assert result.returncode == 0, (result.stdout + result.stderr).decode(errors='replace')
    return run


def api():
    user = ctypes.WinDLL('user32', use_last_error=True)
    for name, args, result in (
        ('GetForegroundWindow', (), wintypes.HWND),
        ('IsWindowVisible', (wintypes.HWND,), wintypes.BOOL),
        ('IsWindow', (wintypes.HWND,), wintypes.BOOL),
        ('GetCapture', (), wintypes.HWND),
        ('SendMessageW', (wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t), ctypes.c_ssize_t),
        ('GetGuiResources', (wintypes.HANDLE, wintypes.DWORD), wintypes.DWORD),
    ):
        call = getattr(user, name)
        call.argtypes, call.restype = args, result
    return user


def surface(on_drag=lambda _dx: None):
    from ai_mini_monitor.ui.native_taskbar import NativeTaskbarSurface
    from ai_mini_monitor.ui.layered_window import LayeredWindowPresenter
    window = NativeTaskbarSurface(on_drag=on_drag)
    presenter = LayeredWindowPresenter(window)
    presenter.present(Image.new('RGBA', (80, 32), (255, 255, 255, 255)), x=-30000, y=-30000)
    return window, presenter


@isolated_windows
def test_surface_is_stable_and_never_activates():
    user = api()
    before = user.GetForegroundWindow()
    window, presenter = surface()
    handle = window.hwnd
    try:
        for _ in range(5):
            window.show()
            assert user.IsWindowVisible(handle)
            assert user.GetForegroundWindow() == before
            assert user.SendMessageW(handle, 0x21, 0, 0) == 3
            window.hide()
            assert not user.IsWindowVisible(handle)
            assert user.GetForegroundWindow() == before
            assert window.winfo_id() == handle == presenter.hwnd
    finally:
        window.destroy()
        window.destroy()
    assert not user.IsWindow(handle)


@isolated_windows
def test_partial_creation_failure_releases_resources(monkeypatch):
    from ai_mini_monitor.ui import native_taskbar
    user = api()
    created_classes = []
    def fail_create(_style, name, *_args):
        created_classes.append(name)
        return 0
    monkeypatch.setattr(native_taskbar, '_user32', lambda: user)
    monkeypatch.setattr(user, 'CreateWindowExW', fail_create)
    with pytest.raises(OSError):
        native_taskbar.NativeTaskbarSurface(on_drag=lambda _dx: None)
    assert len(created_classes) == 1
    user.GetClassInfoExW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR, ctypes.c_void_p)
    user.GetClassInfoExW.restype = wintypes.BOOL
    kernel = ctypes.WinDLL('kernel32')
    kernel.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    kernel.GetModuleHandleW.restype = wintypes.HMODULE
    info = native_taskbar.WNDCLASSEXW()
    info.cbSize = ctypes.sizeof(info)
    assert not user.GetClassInfoExW(kernel.GetModuleHandleW(None), created_classes[0], ctypes.byref(info))


@isolated_windows
def test_drag_capture_cancel_and_destroy():
    user = api()
    deltas = []
    window, _presenter = surface(deltas.append)
    handle = window.hwnd
    try:
        window.show()
        user.SendMessageW(handle, 0x201, 1, 10)  # down at client x=10
        assert user.GetCapture() == handle
        user.SendMessageW(handle, 0x200, 1, 20)
        assert deltas == [10]
        user.SendMessageW(handle, 0x1F, 0, 0)  # cancel mode
        assert user.GetCapture() != handle
        user.SendMessageW(handle, 0x200, 1, 30)
        assert deltas == [10]
        user.SendMessageW(handle, 0x201, 1, 10)
    finally:
        window.destroy()
    assert user.GetCapture() != handle


@isolated_windows
def test_callback_exception_does_not_escape_wndproc():
    user = api()
    def fail_drag(_dx):
        raise RuntimeError('deliberate callback failure')
    window, _presenter = surface(fail_drag)
    try:
        window.show()
        user.SendMessageW(window.hwnd, 0x201, 1, 10)
        user.SendMessageW(window.hwnd, 0x200, 1, 20)
        assert user.GetCapture() != window.hwnd
        assert user.IsWindow(window.hwnd)
    finally:
        window.destroy()


@isolated_windows
def test_repeated_render_and_destroy_does_not_accumulate_gdi():
    user = api()
    kernel = ctypes.WinDLL('kernel32')
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    process = kernel.GetCurrentProcess()
    warm, _presenter = surface()
    warm.destroy()
    before = user.GetGuiResources(process, 0)
    for _ in range(20):
        window, presenter = surface()
        window.show()
        presenter.present(Image.new('RGBA', (80, 32)), x=-30000, y=-30000)
        window.hide()
        window.destroy()
    assert user.GetGuiResources(process, 0) == before


@isolated_windows
def test_taskbar_drag_through_tcl_message_loop_defers_tk_callback(monkeypatch):
    import tkinter as tk
    from ai_mini_monitor.ui import taskbar
    from ai_mini_monitor.ui.overlay import WorkArea
    root = tk.Tk()
    root.withdraw()
    label = tk.Label(root)
    saved = []
    def persist(state):
        label.configure(text=str(state.x))
        saved.append(state.x)
    monkeypatch.setattr(taskbar, 'full_monitor_areas', lambda _: (WorkArea(-32000, -32000, -28000, -28000),))
    monkeypatch.setattr(taskbar, 'windows_taskbar_rects', lambda: (
        WorkArea(-32000, -28048, -28000, -28000), WorkArea(-28400, -28048, -28000, -28000)))
    monkeypatch.setattr(taskbar, 'taskbar_should_be_visible', lambda _: True)
    monkeypatch.setattr(taskbar, 'restore_taskbar_z_order', lambda _: False)
    monkeypatch.setattr(taskbar, 'taskbar_dpi_percent', lambda: 100)
    window = taskbar.TaskbarWindow(root, position=(-31000, -28040), on_state_change=persist)
    user = api()
    user.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t)
    user.PostMessageW.restype = wintypes.BOOL
    try:
        window.show(notify=False)
        for _ in range(20):
            saved.clear()
            user.PostMessageW(window.window.hwnd, 0x201, 1, 10)
            user.PostMessageW(window.window.hwnd, 0x200, 1, 20)
            user.PostMessageW(window.window.hwnd, 0x202, 0, 20)
            root.update()
            assert not saved
            window.update_sensor(None)
            assert len(saved) == 1
            assert label.cget('text') == str(window.state.x)
    finally:
        window.destroy()
        root.destroy()
