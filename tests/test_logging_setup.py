# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ai_mini_monitor.logging_setup import (
    LOG_FILE_NAME,
    log_file_path,
    setup_logging,
    shutdown_logging,
)


FAKE_SECRET = "sk-" + "abcdefghijklmnopqrstuvwxyz012345"


def test_logs_live_under_localappdata_and_redact_secrets(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))  # type: ignore[attr-defined]
    logger = setup_logging(logger_name="ai_mini_monitor.test.redaction")
    path = log_file_path()
    try:
        logger.info("authorization Bearer %s", FAKE_SECRET)
        for handler in logger.handlers:
            handler.flush()
        contents = path.read_text(encoding="utf-8")
    finally:
        shutdown_logging(logger)

    assert path == tmp_path / "AI-Mini-Monitor" / "logs" / LOG_FILE_NAME
    assert FAKE_SECRET not in contents
    assert "Bearer [REDACTED]" in contents


def test_reconfiguration_keeps_exactly_one_owned_rotating_handler(
    tmp_path: Path,
) -> None:
    name = "ai_mini_monitor.test.reconfigure"
    first = setup_logging(directory=tmp_path, logger_name=name)
    second = setup_logging(directory=tmp_path, logger_name=name)
    try:
        handlers = [
            handler
            for handler in second.handlers
            if isinstance(handler, RotatingFileHandler)
        ]
        assert first is second
        assert len(handlers) == 1
        assert second.propagate is False
    finally:
        shutdown_logging(second)


def test_small_log_file_rotates_without_losing_redaction(tmp_path: Path) -> None:
    logger = setup_logging(
        directory=tmp_path,
        logger_name="ai_mini_monitor.test.rotation",
        max_bytes=220,
        backup_count=2,
    )
    try:
        for index in range(20):
            logger.warning("event %02d %s %s", index, FAKE_SECRET, "x" * 40)
        for handler in logger.handlers:
            handler.flush()
    finally:
        shutdown_logging(logger)

    files = list(tmp_path.glob(f"{LOG_FILE_NAME}*"))
    assert any(path.name.endswith(".1") for path in files)
    combined = "".join(path.read_text(encoding="utf-8") for path in files)
    assert FAKE_SECRET not in combined
    assert "[REDACTED]" in combined
