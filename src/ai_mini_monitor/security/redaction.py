from __future__ import annotations

import logging
import re


SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/-]{12,}=*"),
    re.compile(r"(?i)(OPENAI_(?:ADMIN_)?KEY\s*[=:]\s*)\S+"),
)


def redact_text(value: object) -> str:
    text = str(value)
    text = SECRET_PATTERNS[0].sub("[REDACTED]", text)
    text = SECRET_PATTERNS[1].sub(r"\1[REDACTED]", text)
    text = SECRET_PATTERNS[2].sub(r"\1[REDACTED]", text)
    return text


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = ()
        return True

