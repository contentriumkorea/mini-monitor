# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from ai_mini_monitor.power_events import (
    PBT_APMRESUMEAUTOMATIC,
    PBT_APMRESUMECRITICAL,
    PBT_APMRESUMESUSPEND,
    PBT_APMSUSPEND,
    WM_POWERBROADCAST,
    WindowsPowerEventHook,
)


class FakeWindow:
    def __init__(self) -> None:
        self.updated = 0

    def update_idletasks(self) -> None:
        self.updated += 1

    def winfo_id(self) -> int:
        return 111


class FakeWindowApi:
    def __init__(self) -> None:
        self.current: Any = 9001
        self.replacements: list[tuple[int, Any]] = []
        self.calls: list[tuple[int, int, int, int, int]] = []

    def parent_of(self, hwnd: int) -> int:
        assert hwnd == 111
        return 222

    def wrap_wndproc(self, callback: Callable[[int, int, int, int], int]) -> Any:
        return callback

    def replace_wndproc(self, hwnd: int, replacement: Any) -> int:
        previous = self.current
        self.current = replacement
        self.replacements.append((hwnd, replacement))
        return previous

    def call_wndproc(
        self,
        original: int,
        hwnd: int,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        self.calls.append((original, hwnd, message, wparam, lparam))
        return 73


def test_dispatches_suspend_and_all_resume_notifications_then_restores_wndproc() -> None:
    window = FakeWindow()
    api = FakeWindowApi()
    events: list[str] = []
    hook = WindowsPowerEventHook(
        window,
        on_suspend=lambda: events.append("suspend") or True,
        on_resume=lambda: events.append("resume"),
        api=api,
    )

    hook.install()
    assert hook.installed
    assert hook.hwnd == 222
    assert window.updated == 1
    callback = api.current

    assert callback(222, WM_POWERBROADCAST, PBT_APMSUSPEND, 0) == 73
    for notification in (
        PBT_APMRESUMEAUTOMATIC,
        PBT_APMRESUMESUSPEND,
        PBT_APMRESUMECRITICAL,
    ):
        assert callback(222, WM_POWERBROADCAST, notification, 0) == 73
    assert callback(222, 0x1234, 0, 0) == 73
    assert events == ["suspend", "resume", "resume", "resume"]
    assert all(call[0] == 9001 for call in api.calls)

    hook.close()
    assert not hook.installed
    assert api.replacements[-1] == (222, 9001)
    hook.close()  # idempotent
    assert len(api.replacements) == 2


def test_callback_exception_is_contained_and_original_wndproc_still_runs() -> None:
    api = FakeWindowApi()

    def fail() -> None:
        raise RuntimeError("synthetic callback failure")

    hook = WindowsPowerEventHook(
        FakeWindow(),
        on_suspend=fail,
        on_resume=lambda: None,
        api=api,
    )
    hook.install()
    callback = api.current
    assert callback(222, WM_POWERBROADCAST, PBT_APMSUSPEND, 5) == 73
    assert api.calls == [(9001, 222, WM_POWERBROADCAST, PBT_APMSUSPEND, 5)]
    hook.close()


def test_install_requires_main_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ai_mini_monitor.power_events.threading.current_thread",
        lambda: object(),
    )
    hook = WindowsPowerEventHook(
        FakeWindow(),
        on_suspend=lambda: None,
        on_resume=lambda: None,
        api=FakeWindowApi(),
    )
    with pytest.raises(RuntimeError, match="main thread"):
        hook.install()


def test_failed_restore_keeps_native_callback_alive_for_retry() -> None:
    class FailingRestoreApi(FakeWindowApi):
        fail_restore = True

        def replace_wndproc(self, hwnd: int, replacement: Any) -> int:
            if replacement == 9001 and self.fail_restore:
                raise OSError("synthetic restore failure")
            return super().replace_wndproc(hwnd, replacement)

    api = FailingRestoreApi()
    hook = WindowsPowerEventHook(
        FakeWindow(),
        on_suspend=lambda: None,
        on_resume=lambda: None,
        api=api,
    )
    hook.install()
    callback = api.current
    with pytest.raises(OSError, match="restore failure"):
        hook.close()
    assert hook.installed
    assert api.current is callback

    api.fail_restore = False
    hook.close()
    assert not hook.installed
