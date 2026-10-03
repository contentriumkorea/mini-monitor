# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from ai_mini_monitor import autostart


class FakeKey:
    def __init__(self, registry: "FakeRegistry", access: int) -> None:
        self.registry = registry
        self.access = access

    def __enter__(self) -> "FakeKey":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


class FakeRegistry:
    HKEY_CURRENT_USER = object()
    KEY_QUERY_VALUE = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1
    REG_EXPAND_SZ = 2
    REG_BINARY = 3

    def __init__(self) -> None:
        self.key_exists = False
        self.values: dict[str, tuple[Any, int]] = {}
        self.created: list[tuple[Any, str, int]] = []
        self.opened: list[tuple[Any, str, int]] = []
        self.deleted: list[str] = []

    def CreateKeyEx(self, root: Any, path: str, _reserved: int, access: int) -> FakeKey:
        self.created.append((root, path, access))
        self.key_exists = True
        return FakeKey(self, access)

    def OpenKey(self, root: Any, path: str, _reserved: int, access: int) -> FakeKey:
        self.opened.append((root, path, access))
        if not self.key_exists:
            raise FileNotFoundError
        return FakeKey(self, access)

    def SetValueEx(
        self,
        _key: FakeKey,
        name: str,
        _reserved: int,
        kind: int,
        value: str,
    ) -> None:
        self.values[name] = (value, kind)

    def QueryValueEx(self, _key: FakeKey, name: str) -> tuple[Any, int]:
        try:
            return self.values[name]
        except KeyError as error:
            raise FileNotFoundError from error

    def DeleteValue(self, _key: FakeKey, name: str) -> None:
        try:
            del self.values[name]
        except KeyError as error:
            raise FileNotFoundError from error
        self.deleted.append(name)


def quoted_command(executable: str, *arguments: str) -> str:
    tail = subprocess.list2cmdline(arguments)
    return f'"{executable}" {tail}' if tail else f'"{executable}"'


def test_autostart_is_disabled_by_default_and_reads_without_mutating() -> None:
    registry = FakeRegistry()
    expected = quoted_command(r"C:\Apps\AI-Mini-Monitor.exe", "--minimized")

    state = autostart.read_state(expected, _registry=registry)

    assert autostart.DEFAULT_ENABLED is False
    assert state.expected_command == expected
    assert state.configured_command is None
    assert state.configured is False
    assert state.enabled is False
    assert not autostart.is_enabled(expected, _registry=registry)
    assert registry.values == {}
    assert registry.created == []


def test_enable_writes_only_hkcu_run_with_an_always_quoted_gui_path() -> None:
    registry = FakeRegistry()
    registry.key_exists = True
    registry.values["Unrelated App"] = (r'"C:\Other\other.exe"', registry.REG_SZ)
    executable = r"C:\Apps\AI-Mini-Monitor.exe"
    expected = quoted_command(executable, "--minimized")

    command = autostart.enable(
        executable=executable,
        arguments=("--minimized",),
        _registry=registry,
    )

    assert command == expected
    assert registry.created == [
        (registry.HKEY_CURRENT_USER, autostart.RUN_KEY, registry.KEY_SET_VALUE)
    ]
    assert registry.values[autostart.VALUE_NAME] == (expected, registry.REG_SZ)
    assert registry.values["Unrelated App"] == (
        r'"C:\Other\other.exe"',
        registry.REG_SZ,
    )
    assert autostart.is_enabled(expected, _registry=registry)


def test_configured_wrong_command_is_not_reported_as_enabled() -> None:
    registry = FakeRegistry()
    registry.key_exists = True
    registry.values[autostart.VALUE_NAME] = (
        quoted_command(r"C:\Old\AI-Mini-Monitor.exe", "--minimized"),
        registry.REG_SZ,
    )
    executable = r"C:\Apps\AI-Mini-Monitor.exe"

    state = autostart.read_state(
        executable=executable,
        arguments=("--minimized",),
        _registry=registry,
    )

    assert state.configured is True
    assert state.enabled is False
    assert state.configured_command != state.expected_command
    assert not autostart.is_enabled(
        executable=executable,
        arguments=("--minimized",),
        _registry=registry,
    )


def test_non_string_or_unsupported_registry_value_is_not_configured() -> None:
    registry = FakeRegistry()
    registry.key_exists = True
    registry.values[autostart.VALUE_NAME] = (b"binary", registry.REG_BINARY)

    assert autostart.read_command(_registry=registry) is None


def test_frozen_cli_registers_the_sibling_gui_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        sys,
        "executable",
        r"C:\Apps\AI-Mini-Monitor\AI-Mini-Monitor-CLI.exe",
    )

    assert autostart.resolve_gui_executable() == Path(
        r"C:\Apps\AI-Mini-Monitor\AI-Mini-Monitor.exe"
    )
    assert autostart.build_app_command() == quoted_command(
        r"C:\Apps\AI-Mini-Monitor\AI-Mini-Monitor.exe",
        "--minimized",
    )


def test_frozen_gui_keeps_itself_as_the_startup_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = r"C:\Apps\AI-Mini-Monitor\AI-Mini-Monitor.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", executable)

    assert autostart.resolve_gui_executable() == Path(executable)
    assert autostart.build_app_command() == quoted_command(
        executable,
        "--minimized",
    )


def test_source_app_command_preserves_an_absolute_custom_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Python\python.exe")
    config_path = tmp_path / "custom settings.json"

    command = autostart.build_app_command(config_path)

    assert command == quoted_command(
        r"C:\Python\python.exe",
        "-m",
        "ai_mini_monitor.cli",
        "--config",
        str(config_path.resolve()),
        "--minimized",
    )


def test_frozen_cli_custom_config_uses_sibling_gui_and_exact_readback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        sys,
        "executable",
        r"C:\Apps\AI-Mini-Monitor\AI-Mini-Monitor-CLI.exe",
    )
    registry = FakeRegistry()
    config_path = tmp_path / "profile.json"

    enabled = autostart.enable(config_path=config_path, _registry=registry)
    state = autostart.read_state(config_path=config_path, _registry=registry)

    assert enabled == quoted_command(
        r"C:\Apps\AI-Mini-Monitor\AI-Mini-Monitor.exe",
        "--config",
        str(config_path.resolve()),
        "--minimized",
    )
    assert state.configured_command == enabled
    assert state.enabled is True


def test_disable_removes_only_this_app_value_and_is_idempotent() -> None:
    registry = FakeRegistry()
    registry.key_exists = True
    unrelated = (r'"C:\other.exe"', registry.REG_SZ)
    registry.values["Unrelated App"] = unrelated
    autostart.enable(
        executable=r"C:\Apps\AI-Mini-Monitor.exe",
        arguments=("--minimized",),
        _registry=registry,
    )

    assert autostart.disable(_registry=registry)
    assert autostart.VALUE_NAME not in registry.values
    assert registry.values["Unrelated App"] == unrelated
    assert registry.deleted == [autostart.VALUE_NAME]
    assert not autostart.disable(_registry=registry)
    assert registry.values["Unrelated App"] == unrelated
    assert registry.opened[-1] == (
        registry.HKEY_CURRENT_USER,
        autostart.RUN_KEY,
        registry.KEY_SET_VALUE,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"executable": ""},
        {"executable": 'C:\\bad"path.exe'},
        {"arguments": "--minimized"},
        {"arguments": ("bad\x00argument",)},
    ],
)
def test_command_builder_rejects_ambiguous_or_invalid_input(
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        autostart.build_command(**kwargs)


def test_command_and_expected_command_overrides_reject_conflicting_inputs() -> None:
    registry = FakeRegistry()
    with pytest.raises(ValueError, match="cannot be combined"):
        autostart.enable(
            command='"C:\\app.exe" --minimized',
            config_path=r"C:\config.json",
            _registry=registry,
        )
    with pytest.raises(ValueError, match="cannot be combined"):
        autostart.read_state(
            '"C:\\app.exe" --minimized',
            executable=r"C:\app.exe",
            _registry=registry,
        )
