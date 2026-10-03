# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import io
import logging

import pytest

from ai_mini_monitor.security.redaction import SecretRedactionFilter, redact_text


FAKE_SK_SECRET = "sk-" + "abcdefghijklmnopqrstuvwxyz012345"
FAKE_BEARER_SECRET = "abcdefghijklmnopqrstuvwxyz" + ".123456"
FAKE_ADMIN_SECRET = "super-secret-" + "admin-token"
FAKE_OPENAI_SECRET = "another-long-" + "secret-value"


@pytest.mark.parametrize(
    "secret_text",
    [
        f"request key {FAKE_SK_SECRET}",
        f"Authorization: Bearer {FAKE_BEARER_SECRET}",
        f"OPENAI_ADMIN_KEY={FAKE_ADMIN_SECRET}",
        f"openai_key: {FAKE_OPENAI_SECRET}",
    ],
)
def test_supported_secret_forms_are_redacted(secret_text: str) -> None:
    redacted = redact_text(secret_text)
    assert "[REDACTED]" in redacted
    assert "abcdefghijklmnopqrstuvwxyz012345" not in redacted
    assert "super-secret-admin-token" not in redacted
    assert "another-long-secret-value" not in redacted


def test_logging_filter_redacts_after_percent_formatting() -> None:
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.addFilter(SecretRedactionFilter())
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("ai-mini-monitor-redaction-test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        logger.info("token=%s port=%s", FAKE_SK_SECRET, "COM3")
    finally:
        logger.handlers = []

    rendered = output.getvalue()
    assert "[REDACTED]" in rendered
    assert "abcdefghijklmnopqrstuvwxyz012345" not in rendered
    assert "COM3" in rendered


def test_non_secret_device_identity_is_preserved() -> None:
    text = "USB VID_1A86 PID_5722 serial USB35INCHIPSV2 on COM3"
    assert redact_text(text) == text
