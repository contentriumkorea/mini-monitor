# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ai_mini_monitor.ai.activity import ChatGPTActivityProvider, _format_duration
from ai_mini_monitor.models import AIProviderKind


class FakeProbe:
    def __init__(self, names: list[str | None]) -> None:
        self.names = list(names)

    def process_name(self) -> str | None:
        return self.names.pop(0) if self.names else None


def test_activity_counts_capped_elapsed_time_and_sessions(tmp_path) -> None:
    path = tmp_path / "activity.json"
    probe = FakeProbe(["ChatGPT.EXE", "chatgpt.exe", None, None, "Codex.exe", "codex.EXE"])
    provider = ChatGPTActivityProvider(
        ["ChatGPT.exe", "Codex.exe"], probe=probe, path=path, max_tick_seconds=5.0
    )
    start = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    for offset in (0, 3, 20, 21, 22, 24):
        display = provider.tick(start + timedelta(seconds=offset))

    assert provider.record.sessions == 2
    assert provider.record.active_seconds == 10.0
    assert display.provider is AIProviderKind.CHATGPT_ACTIVITY
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"day", "active_seconds", "sessions", "last_used"}
    assert "title" not in payload and "content" not in payload


def test_same_day_record_is_loaded_and_corrupt_record_falls_back(tmp_path) -> None:
    path = tmp_path / "activity.json"
    today = datetime.now().astimezone().date().isoformat()
    path.write_text(
        json.dumps({"day": today, "active_seconds": 65.0, "sessions": 3, "last_used": None}),
        encoding="utf-8",
    )
    loaded = ChatGPTActivityProvider(["ChatGPT.exe"], probe=FakeProbe([None]), path=path)
    assert loaded.record.active_seconds == 65.0
    assert loaded.record.sessions == 3

    path.write_text("not json", encoding="utf-8")
    fallback = ChatGPTActivityProvider(["ChatGPT.exe"], probe=FakeProbe([None]), path=path)
    assert fallback.record.active_seconds == 0.0
    assert fallback.record.sessions == 0


def test_day_rollover_resets_previous_day_state(tmp_path) -> None:
    path = tmp_path / "activity.json"
    provider = ChatGPTActivityProvider(
        ["ChatGPT.exe"], probe=FakeProbe(["ChatGPT.exe", "ChatGPT.exe"]), path=path
    )
    local_timezone = datetime.now().astimezone().tzinfo
    first = datetime(2026, 8, 10, 23, 59, 59, tzinfo=local_timezone)
    provider.tick(first)
    provider.tick(first + timedelta(seconds=2))
    assert provider.record.day == "2026-08-11"
    assert provider.record.sessions == 1
    assert provider.record.active_seconds == 0.0


def test_duration_formatting_is_stable() -> None:
    assert _format_duration(0) == "0m"
    assert _format_duration(59.9) == "0m"
    assert _format_duration(60) == "1m"
    assert _format_duration(3_660) == "1h 01m"
