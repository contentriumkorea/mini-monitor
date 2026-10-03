# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from typing import Any, Callable

import pytest

from ai_mini_monitor.single_instance import ERROR_ALREADY_EXISTS, SingleInstance


class FakeFunction:
    def __init__(self, callback: Callable[..., Any]) -> None:
        self.callback = callback
        self.argtypes: Any = None
        self.restype: Any = None

    def __call__(self, *args: Any) -> Any:
        return self.callback(*args)


class FakeKernel32:
    def __init__(self, handle: int = 41) -> None:
        self.created: list[tuple[Any, bool, str]] = []
        self.closed: list[int] = []
        self.CreateMutexW = FakeFunction(self._create_mutex)
        self.CloseHandle = FakeFunction(self._close_handle)
        self.handle = handle

    def _create_mutex(self, security: Any, initial_owner: bool, name: str) -> int:
        self.created.append((security, initial_owner, name))
        return self.handle

    def _close_handle(self, handle: int) -> bool:
        self.closed.append(handle)
        return True


def guard(kernel32: FakeKernel32, error: int = 0) -> SingleInstance:
    return SingleInstance(
        r"Local\test-ai-mini-monitor",
        _kernel32=kernel32,
        _get_last_error=lambda: error,
        _set_last_error=lambda _value: None,
    )


def test_primary_instance_keeps_mutex_until_explicit_release() -> None:
    kernel32 = FakeKernel32()
    instance = guard(kernel32)

    assert instance.acquire()
    assert instance.acquired
    assert not instance.already_running
    assert kernel32.created == [(None, False, r"Local\test-ai-mini-monitor")]
    assert kernel32.closed == []

    assert instance.acquire()  # idempotent; no second OS call
    assert len(kernel32.created) == 1
    instance.release()
    assert not instance.acquired
    assert kernel32.closed == [41]


def test_duplicate_instance_closes_only_its_mutex_handle_and_returns_false() -> None:
    kernel32 = FakeKernel32(handle=73)
    instance = guard(kernel32, ERROR_ALREADY_EXISTS)

    assert not instance.acquire()
    assert instance.already_running
    assert not instance.acquired
    assert kernel32.closed == [73]


def test_context_manager_releases_owned_handle_even_after_error() -> None:
    kernel32 = FakeKernel32(handle=99)
    with pytest.raises(RuntimeError):
        with guard(kernel32) as instance:
            assert instance.acquired
            raise RuntimeError("boom")
    assert kernel32.closed == [99]


@pytest.mark.parametrize("name", ["", "   ", "bad\x00name"])
def test_invalid_mutex_names_are_rejected_without_os_calls(name: str) -> None:
    with pytest.raises(ValueError):
        SingleInstance(name, _kernel32=FakeKernel32())
