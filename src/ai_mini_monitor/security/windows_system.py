"""Resolve trusted Windows system executables without inherited environment paths."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path


def windows_powershell_paths() -> tuple[Path, Path] | None:
    """Return the OS system PowerShell executable and its system module path."""

    if os.name != "nt":
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
        if length <= 0 or length >= len(buffer):
            return None
        directory = Path(buffer.value)
        drive = directory.drive
        if not directory.is_absolute() or len(drive) != 2 or drive[1] != ":" or not drive[0].isalpha():
            return None
        base = directory / "WindowsPowerShell" / "v1.0"
        executable = base / "powershell.exe"
        if not executable.is_file():
            return None
        return executable, base / "Modules"
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def pin_powershell_modules(environment: dict[str, str], modules: Path) -> None:
    """Keep one trusted module path despite Windows' case-insensitive env keys."""

    for key in tuple(environment):
        if key.casefold() == "psmodulepath":
            environment.pop(key)
    environment["PSModulePath"] = str(modules)
