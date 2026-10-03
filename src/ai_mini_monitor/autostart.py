# SPDX-License-Identifier: GPL-3.0-or-later

"""Opt-in per-user Windows startup registration.

Importing this module never changes the registry.  Only :func:`enable` and
:func:`disable` mutate the current user's Run value.
"""

from __future__ import annotations

import subprocess
import sys
import winreg
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final


__all__ = [
    "AutostartState",
    "DEFAULT_ENABLED",
    "RUN_KEY",
    "VALUE_NAME",
    "MINIMIZED_ARGUMENT",
    "CONFIG_ARGUMENT",
    "GUI_EXECUTABLE_NAME",
    "CLI_EXECUTABLE_NAME",
    "resolve_gui_executable",
    "build_command",
    "build_app_command",
    "read_command",
    "read_state",
    "is_enabled",
    "enable",
    "disable",
    "migrate_known_legacy",
]

DEFAULT_ENABLED: Final[bool] = False
RUN_KEY: Final[str] = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME: Final[str] = "AI Mini Monitor"
MINIMIZED_ARGUMENT: Final[str] = "--minimized"
CONFIG_ARGUMENT: Final[str] = "--config"
GUI_EXECUTABLE_NAME: Final[str] = "Mini-Monitor.exe"
CLI_EXECUTABLE_NAME: Final[str] = "Mini-Monitor-CLI.exe"
LEGACY_GUI_EXECUTABLE_NAME: Final[str] = "AI-Mini-Monitor.exe"
LEGACY_CLI_EXECUTABLE_NAME: Final[str] = "AI-Mini-Monitor-CLI.exe"


@dataclass(frozen=True, slots=True)
class AutostartState:
    """Read-only snapshot of this app's per-user startup registration."""

    expected_command: str
    configured_command: str | None

    @property
    def configured(self) -> bool:
        """Whether this app's named Run value contains a string command."""

        return self.configured_command is not None

    @property
    def enabled(self) -> bool:
        """Whether the value exactly matches the intended GUI command."""

        return self.configured_command == self.expected_command


def resolve_gui_executable(executable: str | Path | None = None) -> Path:
    """Resolve the executable that Windows should launch at user sign-in.

    A packaged console helper must register its sibling GUI executable so a
    console window is never opened by the Run key. An explicit path is kept
    exact and is primarily useful to installers and tests.
    """

    program = str(executable) if executable is not None else str(sys.executable)
    if not program or not program.strip() or "\x00" in program or '"' in program:
        raise ValueError(
            "executable must be a non-empty path without NUL or quote characters"
        )
    program_path = Path(program)
    if (
        executable is None
        and getattr(sys, "frozen", False)
        and program_path.name.casefold() == CLI_EXECUTABLE_NAME.casefold()
    ):
        return program_path.with_name(GUI_EXECUTABLE_NAME)
    if (
        executable is None
        and getattr(sys, "frozen", False)
        and program_path.name.casefold() == LEGACY_CLI_EXECUTABLE_NAME.casefold()
    ):
        return program_path.with_name(LEGACY_GUI_EXECUTABLE_NAME)
    return program_path


def build_command(
    executable: str | Path | None = None,
    arguments: Sequence[str] | None = None,
) -> str:
    """Build the exact, safely quoted Windows Run command for this app."""

    program = str(resolve_gui_executable(executable))

    if arguments is None:
        if getattr(sys, "frozen", False):
            selected_arguments = (MINIMIZED_ARGUMENT,)
        else:
            selected_arguments = (
                "-m",
                "ai_mini_monitor.cli",
                MINIMIZED_ARGUMENT,
            )
    else:
        if isinstance(arguments, (str, bytes)):
            raise TypeError("arguments must be a sequence of individual arguments")
        selected_arguments = tuple(str(argument) for argument in arguments)
    if any("\x00" in argument for argument in selected_arguments):
        raise ValueError("startup arguments must not contain NUL characters")
    # Windows executable paths cannot contain quote characters (rejected by
    # resolve_gui_executable), so always quoting argv[0] is both unambiguous
    # and stable even when the install directory itself has no spaces.
    quoted_program = f'"{program}"'
    argument_command = subprocess.list2cmdline(selected_arguments)
    return (
        f"{quoted_program} {argument_command}"
        if argument_command
        else quoted_program
    )


def build_app_command(
    config_path: str | Path | None = None,
    *,
    executable: str | Path | None = None,
) -> str:
    """Build the intended minimized GUI command, preserving a custom config.

    Relative config paths are made absolute because the Run key has no stable
    project working directory at sign-in. With no custom path this remains
    byte-for-byte equivalent to the existing :func:`build_command` default.
    """

    if config_path is None:
        return build_command(executable)
    raw_path = str(config_path)
    if (
        not raw_path
        or not raw_path.strip()
        or "\x00" in raw_path
        or '"' in raw_path
    ):
        raise ValueError(
            "config path must be a non-empty path without NUL or quote characters"
        )
    resolved_config = str(Path(raw_path).expanduser().resolve(strict=False))
    if getattr(sys, "frozen", False):
        arguments = (CONFIG_ARGUMENT, resolved_config, MINIMIZED_ARGUMENT)
    else:
        arguments = (
            "-m",
            "ai_mini_monitor.cli",
            CONFIG_ARGUMENT,
            resolved_config,
            MINIMIZED_ARGUMENT,
        )
    return build_command(executable, arguments)


def read_command(*, _registry: Any = winreg) -> str | None:
    """Read the current user's startup command without changing it."""

    try:
        with _registry.OpenKey(
            _registry.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            _registry.KEY_QUERY_VALUE,
        ) as key:
            value, value_type = _registry.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return None
    if value_type not in (_registry.REG_SZ, _registry.REG_EXPAND_SZ):
        return None
    return value if isinstance(value, str) and value else None


def read_state(
    expected_command: str | None = None,
    *,
    executable: str | Path | None = None,
    arguments: Sequence[str] | None = None,
    config_path: str | Path | None = None,
    _registry: Any = winreg,
) -> AutostartState:
    """Read configured and exact-enabled state without changing the registry."""

    if expected_command is not None and (
        executable is not None or arguments is not None or config_path is not None
    ):
        raise ValueError(
            "expected_command cannot be combined with executable or arguments"
        )
    if config_path is not None and arguments is not None:
        raise ValueError("config_path cannot be combined with arguments")
    if expected_command is not None:
        expected = _validate_command(expected_command)
    elif config_path is not None:
        expected = build_app_command(config_path, executable=executable)
    else:
        expected = build_command(executable, arguments)
    return AutostartState(
        expected_command=expected,
        configured_command=read_command(_registry=_registry),
    )


def is_enabled(
    expected_command: str | None = None,
    *,
    executable: str | Path | None = None,
    arguments: Sequence[str] | None = None,
    config_path: str | Path | None = None,
    _registry: Any = winreg,
) -> bool:
    """Return whether the Run value exactly matches the intended GUI command."""

    return read_state(
        expected_command,
        executable=executable,
        arguments=arguments,
        config_path=config_path,
        _registry=_registry,
    ).enabled


def enable(
    command: str | None = None,
    *,
    executable: str | Path | None = None,
    arguments: Sequence[str] | None = None,
    config_path: str | Path | None = None,
    _registry: Any = winreg,
) -> str:
    """Explicitly enable startup for the current Windows user only."""

    if command is not None and (
        executable is not None or arguments is not None or config_path is not None
    ):
        raise ValueError(
            "command cannot be combined with executable, arguments, or config_path"
        )
    if config_path is not None and arguments is not None:
        raise ValueError("config_path cannot be combined with arguments")
    if command is not None:
        selected = _validate_command(command)
    elif config_path is not None:
        selected = build_app_command(config_path, executable=executable)
    else:
        selected = build_command(executable, arguments)
    with _registry.CreateKeyEx(
        _registry.HKEY_CURRENT_USER,
        RUN_KEY,
        0,
        _registry.KEY_SET_VALUE,
    ) as key:
        _registry.SetValueEx(key, VALUE_NAME, 0, _registry.REG_SZ, selected)
    return selected


def disable(*, _registry: Any = winreg) -> bool:
    """Explicitly remove only this app's current-user Run value."""

    try:
        with _registry.OpenKey(
            _registry.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            _registry.KEY_SET_VALUE,
        ) as key:
            try:
                _registry.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                return False
    except FileNotFoundError:
        return False
    return True


def migrate_known_legacy(
    expected_command: str,
    *,
    config_path: str | Path | None = None,
    _registry: Any = winreg,
) -> bool:
    """Migrate only an opted-in Run value pointing at this app's known old GUI.

    An unrelated or manually customized command is left untouched.
    """

    if not getattr(sys, "frozen", False):
        return False
    current = resolve_gui_executable()
    if current.name.casefold() != GUI_EXECUTABLE_NAME.casefold():
        return False
    if expected_command != build_app_command(config_path):
        return False
    candidates = (
        current.with_name(LEGACY_GUI_EXECUTABLE_NAME),
        current.parent.parent / "AI-Mini-Monitor" / LEGACY_GUI_EXECUTABLE_NAME,
    )
    old_commands = {build_app_command(config_path, executable=path) for path in candidates}
    if read_command(_registry=_registry) not in old_commands:
        return False
    enable(command=expected_command, _registry=_registry)
    return True


def _validate_command(command: object) -> str:
    if (
        not isinstance(command, str)
        or not command.strip()
        or "\x00" in command
    ):
        raise ValueError("startup command must be a non-empty string without NULs")
    return command
