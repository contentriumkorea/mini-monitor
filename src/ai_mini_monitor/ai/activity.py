from __future__ import annotations

import ctypes
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import psutil

from ..models import AIData, AIProviderKind, SyncStatus
from ..resources import user_data_dir


class ForegroundProcessProbe:
    """Reads only the foreground process executable name; never reads window text."""

    def process_name(self) -> str | None:
        if os.name != "nt":
            return None
        user32 = ctypes.windll.user32
        window = user32.GetForegroundWindow()
        if not window:
            return None
        process_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
        if not process_id.value:
            return None
        try:
            return psutil.Process(process_id.value).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return None


@dataclass(slots=True)
class ActivityRecord:
    day: str
    active_seconds: float = 0.0
    sessions: int = 0
    last_used: str | None = None


class ChatGPTActivityProvider:
    def __init__(
        self,
        process_names: Iterable[str],
        *,
        probe: ForegroundProcessProbe | None = None,
        path: Path | None = None,
        max_tick_seconds: float = 5.0,
    ) -> None:
        self.process_names = {name.casefold() for name in process_names}
        self.probe = probe or ForegroundProcessProbe()
        self.path = path or (user_data_dir() / "activity.json")
        self.max_tick_seconds = max_tick_seconds
        self.record = self._load(datetime.now().astimezone().date())
        self._last_tick: datetime | None = None
        self._was_active = False

    def tick(self, now: datetime | None = None) -> AIData:
        current = (now or datetime.now().astimezone()).astimezone()
        self._roll_day(current.date())
        name = self.probe.process_name()
        active = bool(name and name.casefold() in self.process_names)
        if self._last_tick is not None and self._was_active:
            elapsed = max(0.0, min(self.max_tick_seconds, (current - self._last_tick).total_seconds()))
            self.record.active_seconds += elapsed
        if active and not self._was_active:
            self.record.sessions += 1
        if active:
            self.record.last_used = current.isoformat(timespec="seconds")
        self._last_tick = current
        self._was_active = active
        self._save()
        return self.to_display()

    def to_display(self) -> AIData:
        last = "--"
        if self.record.last_used:
            try:
                last = datetime.fromisoformat(self.record.last_used).astimezone().strftime("%H:%M")
            except ValueError:
                last = "--"
        return AIData(
            provider=AIProviderKind.CHATGPT_ACTIVITY,
            title="CHATGPT ACTIVITY",
            status=SyncStatus.OK,
            primary_value=_format_duration(self.record.active_seconds),
            primary_label="ACTIVE TODAY",
            fields=(("SESSIONS", str(self.record.sessions)), ("LAST USED", last)),
            last_sync=datetime.now().astimezone(),
        )

    def _roll_day(self, current: date) -> None:
        if self.record.day != current.isoformat():
            self.record = ActivityRecord(day=current.isoformat())
            self._last_tick = None
            self._was_active = False

    def _load(self, current: date) -> ActivityRecord:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            record = ActivityRecord(**payload)
            return record if record.day == current.isoformat() else ActivityRecord(current.isoformat())
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return ActivityRecord(current.isoformat())

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(asdict(self.record), handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)


def _format_duration(seconds: float) -> str:
    total_minutes = max(0, int(seconds // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"

