# SPDX-License-Identifier: GPL-3.0-or-later

"""Rotating, secret-redacted application logging under LOCALAPPDATA."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

from .resources import user_data_dir
from .security.redaction import SecretRedactionFilter, redact_text


APP_LOGGER_NAME: Final[str] = "ai_mini_monitor"
LOG_FILE_NAME: Final[str] = "ai-mini-monitor.log"
DEFAULT_MAX_BYTES: Final[int] = 1_000_000
DEFAULT_BACKUP_COUNT: Final[int] = 3
_HANDLER_MARKER: Final[str] = "_ai_mini_monitor_rotating_handler"


class RedactingFormatter(logging.Formatter):
    """Redact the complete formatted line, including exception text."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def log_file_path(directory: str | Path | None = None) -> Path:
    base = Path(directory) if directory is not None else user_data_dir() / "logs"
    return base / LOG_FILE_NAME


def _coerce_level(level: str | int) -> int:
    if isinstance(level, bool):
        raise ValueError("log level must be a logging level name or integer")
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        # ``getLevelNamesMapping`` is Python 3.11+, while the application
        # supports 3.10.  ``getLevelName`` returns the numeric value for a
        # recognized upper-case name on every supported version.
        selected = logging.getLevelName(level.upper())
        if isinstance(selected, int):
            return selected
    raise ValueError(f"unknown log level: {level!r}")


def setup_logging(
    level: str | int = "INFO",
    *,
    directory: str | Path | None = None,
    logger_name: str = APP_LOGGER_NAME,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> logging.Logger:
    """Configure one package logger with one rotating UTF-8 file handler."""

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    if (
        isinstance(backup_count, bool)
        or not isinstance(backup_count, int)
        or backup_count < 1
    ):
        raise ValueError("backup_count must be at least one")

    path = log_file_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(logger_name)
    logger.setLevel(_coerce_level(level))
    logger.disabled = False
    logger.propagate = False

    # Reconfiguration is deterministic and never duplicates our file handler.
    shutdown_logging(logger)

    handler = RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=False,
        errors="backslashreplace",
    )
    setattr(handler, _HANDLER_MARKER, True)
    handler.setLevel(logger.level)
    handler.addFilter(SecretRedactionFilter())
    handler.setFormatter(
        RedactingFormatter(
            "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    logger.addHandler(handler)
    return logger


def shutdown_logging(logger: logging.Logger | None = None) -> None:
    """Flush, detach, and close handlers installed by :func:`setup_logging`."""

    selected = logger if logger is not None else logging.getLogger(APP_LOGGER_NAME)
    for handler in tuple(selected.handlers):
        if not getattr(handler, _HANDLER_MARKER, False):
            continue
        selected.removeHandler(handler)
        try:
            handler.flush()
        finally:
            handler.close()
