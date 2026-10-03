# SPDX-License-Identifier: GPL-3.0-or-later

"""Consent-gated, local-only Codex rate-limit reader.

Only the narrow ``event_msg -> payload.token_count -> rate_limits`` shape is
accepted.  Prompt, output, tool, and arbitrary payload data is never retained
or returned.
"""

from __future__ import annotations

import ctypes
import heapq
import json
import math
import os
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from ..models import AIData, AIProviderKind, SyncStatus


MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024
MAX_SCAN_ENTRIES = 20_000
MAX_CANDIDATE_FILES = 256
MAX_SCAN_DEPTH = 8
MAX_SCAN_SECONDS = 1.5
MAX_READ_SECONDS = 2.0
MAX_TOTAL_READ_BYTES = 64 * 1024 * 1024
READ_BLOCK_BYTES = 64 * 1024
RECENT_EVENT_AGE = timedelta(minutes=15)
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_MAX_UNIX_TIMESTAMP = 253_402_300_799.0
_WINDOWS = {300: "five_hour", 10_080: "seven_day"}


class CodexUsageStatus(str, Enum):
    OK = "ok"
    STALE = "stale"
    CONSENT_REQUIRED = "consent_required"
    SESSIONS_NOT_FOUND = "sessions_not_found"
    NO_RATE_LIMITS = "no_rate_limits"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class CodexUsageSnapshot:
    """Minimal rate-limit state safe for the controller and UI to consume."""

    status: CodexUsageStatus
    source_path: Path | None = None
    five_hour_remaining_percent: float | None = None
    seven_day_remaining_percent: float | None = None
    five_hour_reset_at: datetime | None = None
    seven_day_reset_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class _ParsedEvent:
    five_hour_remaining_percent: float | None
    seven_day_remaining_percent: float | None
    five_hour_reset_at: datetime | None
    seven_day_reset_at: datetime | None
    five_hour_updated_at: datetime | None
    seven_day_updated_at: datetime | None

    @property
    def updated_at(self) -> datetime:
        """Timestamp of the value shown as the card's primary quota."""

        # The seven-day allowance is the primary user-facing value.  A newer
        # five-hour-only observation must not make an older-but-still-current
        # seven-day value look freshly updated.
        updated_at = self.seven_day_updated_at or self.five_hour_updated_at
        if updated_at is None:  # Defensive only; parsed events always have a window.
            raise ValueError("parsed event has no rate-limit window")
        return updated_at


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class _FileCandidate:
    path: Path
    identity: _FileIdentity
    modified_ns: int


@dataclass(slots=True)
class _ReadBudget:
    remaining: int
    exhausted: bool = False


@dataclass(slots=True)
class _ReadState:
    incomplete: bool = False


class CodexUsageProvider:
    """Read Codex session rate limits without network access or content capture."""

    def __init__(
        self,
        *,
        consent_granted: bool = False,
        sessions_root: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.consent_granted = bool(consent_granted)
        self._sessions_root = Path(sessions_root) if sessions_root is not None else None
        self._environ = environ

    def set_consent(self, granted: bool) -> None:
        self.consent_granted = bool(granted)

    def refresh(self, now: datetime | None = None) -> CodexUsageSnapshot:
        if not self.consent_granted:
            return CodexUsageSnapshot(CodexUsageStatus.CONSENT_REQUIRED)

        # This public boundary deliberately suppresses arbitrary filesystem
        # exception text.  A controller can safely map the class-free status
        # without logging a user profile or session path.
        try:
            return self._refresh(_as_utc(now or datetime.now(timezone.utc)))
        except Exception:
            return CodexUsageSnapshot(CodexUsageStatus.UNAVAILABLE)

    def _refresh(self, current: datetime) -> CodexUsageSnapshot:
        configured_root = self._configured_sessions_root()
        if configured_root is None or not configured_root.exists():
            return CodexUsageSnapshot(CodexUsageStatus.SESSIONS_NOT_FOUND)

        root = _safe_root(configured_root)
        if root is None:
            return CodexUsageSnapshot(CodexUsageStatus.UNAVAILABLE)

        scan_deadline = time.monotonic() + MAX_SCAN_SECONDS
        files, scan_incomplete = _scan_jsonl_files(root, deadline=scan_deadline)
        if not files:
            status = CodexUsageStatus.UNAVAILABLE if scan_incomplete else CodexUsageStatus.NO_RATE_LIMITS
            return CodexUsageSnapshot(status)

        latest: tuple[Path, _ParsedEvent] | None = None
        read_incomplete = False
        budget = _ReadBudget(MAX_TOTAL_READ_BYTES)
        read_deadline = time.monotonic() + MAX_READ_SECONDS
        for candidate in files:
            if time.monotonic() >= read_deadline:
                read_incomplete = True
                break
            event, incomplete = _latest_event_in_file(
                candidate,
                root,
                current,
                budget,
                read_deadline,
            )
            read_incomplete = read_incomplete or incomplete
            if event is None:
                continue
            # Concurrent sessions can write the five-hour and seven-day
            # windows in separate events/files.  Merge each window by its own
            # embedded observation timestamp.  Treating an event as an atomic
            # pair can otherwise make a newer 5H 100% record erase the current
            # 7D 85% plan allowance.
            if latest is None:
                latest = (candidate.path, event)
            else:
                previous_path, previous_event = latest
                source_path = _merged_source_path(
                    previous_path,
                    previous_event,
                    candidate.path,
                    event,
                )
                latest = (source_path, _merge_events(previous_event, event))

        if latest is not None:
            latest = (
                latest[0],
                _omit_stale_secondary_window(latest[1], current),
            )
            return _snapshot(
                *latest,
                is_stale=(
                    scan_incomplete
                    or read_incomplete
                    or budget.exhausted
                    or not _is_recent(latest[1].updated_at, current)
                ),
            )
        if scan_incomplete or read_incomplete or budget.exhausted:
            return CodexUsageSnapshot(CodexUsageStatus.UNAVAILABLE)
        return CodexUsageSnapshot(CodexUsageStatus.NO_RATE_LIMITS)

    def _configured_sessions_root(self) -> Path | None:
        if self._sessions_root is not None:
            return self._sessions_root
        environment = os.environ if self._environ is None else self._environ
        codex_home = environment.get("CODEX_HOME")
        if codex_home:
            home = Path(codex_home)
        else:
            user_profile = environment.get("USERPROFILE")
            if not user_profile:
                return None
            home = Path(user_profile) / ".codex"
        return home / "sessions"


def to_ai_data(
    snapshot: CodexUsageSnapshot,
    now: datetime | None = None,
) -> AIData:
    """Convert minimal local quota data into the shared dashboard model."""

    current = _as_utc(now or datetime.now(timezone.utc))
    if snapshot.status is CodexUsageStatus.CONSENT_REQUIRED:
        return _codex_error("CONSENT", "REQUIRED", "LOCAL READ", "DISABLED")
    if snapshot.status is CodexUsageStatus.SESSIONS_NOT_FOUND:
        return _codex_error("NO DATA", "CODEX SESSIONS", "STATUS", "NOT FOUND")
    if snapshot.status is CodexUsageStatus.NO_RATE_LIMITS:
        return _codex_error("NO DATA", "USE CODEX FIRST", "STATUS", "NO LIMIT EVENT")
    if snapshot.status is CodexUsageStatus.UNAVAILABLE:
        return _codex_error("UNAVAILABLE", "LOCAL READ", "STATUS", "READ ERROR", delayed=True)

    five_remaining = _remaining_percent(snapshot.five_hour_remaining_percent)
    seven_remaining = _remaining_percent(snapshot.seven_day_remaining_percent)
    five_used = _used_percent(five_remaining)
    seven_used = _used_percent(seven_remaining)
    # The large number is the overall seven-day plan allowance users see in
    # Codex's usage panel.  A five-hour window, when present, is useful as
    # secondary context but must not replace the weekly value (for example,
    # 5H LEFT 100% alongside 7D LEFT 85%).
    if seven_remaining is not None:
        primary_remaining = seven_remaining
        primary_window = "7D"
        fields = (
            *(
                (("5H LEFT", _percent_text(five_remaining)),)
                if five_remaining is not None
                else ()
            ),
            ("7D RESET", _reset_text(snapshot.seven_day_reset_at, current)),
        )
    elif five_remaining is not None:
        primary_remaining = five_remaining
        primary_window = "5H"
        fields = (("7D RESET", _reset_text(snapshot.seven_day_reset_at, current)),)
    else:
        primary_remaining = None
        primary_window = "LIMIT"
        fields = (("7D RESET", _reset_text(snapshot.seven_day_reset_at, current)),)

    budget_used = five_used if primary_window == "5H" else seven_used
    budget_window = primary_window
    remaining_values = [value for value in (five_remaining, seven_remaining) if value is not None]
    return AIData(
        provider=AIProviderKind.CODEX_LOCAL,
        title="CODEX LIMITS",
        status=(
            SyncStatus.DELAYED
            if snapshot.status is CodexUsageStatus.STALE
            else SyncStatus.OK
        ),
        primary_value=_percent_text(primary_remaining),
        primary_label=f"{primary_window} LEFT",
        fields=fields,
        last_sync=snapshot.updated_at,
        # The bar remains a consumed-quota gauge so a full/red bar still means
        # exhausted.  Its explicit label keeps it distinct from the large
        # remaining-quota value above.
        budget_ratio=(budget_used / 100.0 if budget_used is not None else None),
        budget_label=(f"{budget_window} USED {round(budget_used)}%" if budget_used is not None else None),
        error_detail=(
            "stale local rate-limit snapshot"
            if snapshot.status is CodexUsageStatus.STALE
            else (None if remaining_values else "rate limit windows unavailable")
        ),
    )


def _codex_error(
    primary: str,
    label: str,
    field_label: str,
    field_value: str,
    *,
    delayed: bool = False,
) -> AIData:
    return AIData(
        provider=AIProviderKind.CODEX_LOCAL,
        title="CODEX LIMITS",
        status=SyncStatus.DELAYED if delayed else SyncStatus.SETUP_REQUIRED,
        primary_value=primary,
        primary_label=label,
        fields=((field_label, field_value),),
    )


def _remaining_percent(value: float | None) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return min(100.0, max(0.0, float(value)))


def _used_percent(remaining: float | None) -> float | None:
    return None if remaining is None else 100.0 - remaining


def _percent_text(value: float | None) -> str:
    return "--" if value is None else f"{round(value):d}%"


def _reset_text(reset_at: datetime | None, now: datetime) -> str:
    if reset_at is None:
        return "--"
    seconds = max(0, round((_as_utc(reset_at) - now).total_seconds()))
    if seconds == 0:
        return "NOW"
    days, remainder = divmod(seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes = remainder // 60
    if days:
        return f"{days}D {hours}H"
    if hours:
        return f"{hours}H {minutes}M"
    return f"{minutes}M"


def _snapshot(
    path: Path,
    event: _ParsedEvent,
    *,
    is_stale: bool = False,
) -> CodexUsageSnapshot:
    return CodexUsageSnapshot(
        status=CodexUsageStatus.STALE if is_stale else CodexUsageStatus.OK,
        source_path=path,
        five_hour_remaining_percent=event.five_hour_remaining_percent,
        seven_day_remaining_percent=event.seven_day_remaining_percent,
        five_hour_reset_at=event.five_hour_reset_at,
        seven_day_reset_at=event.seven_day_reset_at,
        updated_at=event.updated_at,
    )


def _merge_events(left: _ParsedEvent, right: _ParsedEvent) -> _ParsedEvent:
    """Merge independently observed quota windows by embedded timestamp."""

    if (
        right.five_hour_updated_at is not None
        and (
            left.five_hour_updated_at is None
            or right.five_hour_updated_at > left.five_hour_updated_at
        )
    ):
        five_remaining = right.five_hour_remaining_percent
        five_reset = right.five_hour_reset_at
        five_updated = right.five_hour_updated_at
    else:
        five_remaining = left.five_hour_remaining_percent
        five_reset = left.five_hour_reset_at
        five_updated = left.five_hour_updated_at

    if (
        right.seven_day_updated_at is not None
        and (
            left.seven_day_updated_at is None
            or right.seven_day_updated_at > left.seven_day_updated_at
        )
    ):
        seven_remaining = right.seven_day_remaining_percent
        seven_reset = right.seven_day_reset_at
        seven_updated = right.seven_day_updated_at
    else:
        seven_remaining = left.seven_day_remaining_percent
        seven_reset = left.seven_day_reset_at
        seven_updated = left.seven_day_updated_at

    return _ParsedEvent(
        five_hour_remaining_percent=five_remaining,
        seven_day_remaining_percent=seven_remaining,
        five_hour_reset_at=five_reset,
        seven_day_reset_at=seven_reset,
        five_hour_updated_at=five_updated,
        seven_day_updated_at=seven_updated,
    )


def _merged_source_path(
    left_path: Path,
    left: _ParsedEvent,
    right_path: Path,
    right: _ParsedEvent,
) -> Path:
    """Keep the source path that supplied the primary displayed window."""

    if right.seven_day_updated_at is not None and (
        left.seven_day_updated_at is None
        or right.seven_day_updated_at > left.seven_day_updated_at
    ):
        return right_path
    if left.seven_day_updated_at is not None:
        return left_path
    if right.five_hour_updated_at is not None and (
        left.five_hour_updated_at is None
        or right.five_hour_updated_at > left.five_hour_updated_at
    ):
        return right_path
    return left_path


def _omit_stale_secondary_window(event: _ParsedEvent, now: datetime) -> _ParsedEvent:
    """Do not pair a current weekly value with an obsolete 5H detail."""

    if (
        event.seven_day_updated_at is None
        or not _is_recent(event.seven_day_updated_at, now)
        or event.five_hour_updated_at is None
        or _is_recent(event.five_hour_updated_at, now)
    ):
        return event
    return _ParsedEvent(
        five_hour_remaining_percent=None,
        seven_day_remaining_percent=event.seven_day_remaining_percent,
        five_hour_reset_at=None,
        seven_day_reset_at=event.seven_day_reset_at,
        five_hour_updated_at=None,
        seven_day_updated_at=event.seven_day_updated_at,
    )


def _safe_root(path: Path) -> Path | None:
    if not path.is_absolute():
        return None
    try:
        if _has_reparse_component(path):
            return None
        root_stat = path.lstat()
        if not stat.S_ISDIR(root_stat.st_mode) or _is_reparse_or_symlink(path, root_stat):
            return None
        resolved = path.resolve(strict=True)
        resolved_stat = resolved.lstat()
    except (OSError, RuntimeError):
        return None
    if not stat.S_ISDIR(resolved_stat.st_mode) or _is_reparse_or_symlink(resolved, resolved_stat):
        return None
    return resolved


def _scan_jsonl_files(
    root: Path,
    *,
    deadline: float | None = None,
) -> tuple[list[_FileCandidate], bool]:
    selected: list[tuple[int, str, _FileCandidate]] = []
    pending: list[tuple[Path, int]] = [(root, 0)]
    incomplete = False
    entries_seen = 0
    eligible_files = 0
    stop_scan = False
    selected_deadline = deadline if deadline is not None else time.monotonic() + MAX_SCAN_SECONDS

    while pending and not stop_scan:
        if time.monotonic() >= selected_deadline:
            incomplete = True
            break
        directory, depth = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if time.monotonic() >= selected_deadline:
                        incomplete = True
                        stop_scan = True
                        break
                    entries_seen += 1
                    if entries_seen > MAX_SCAN_ENTRIES:
                        incomplete = True
                        stop_scan = True
                        break
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                        entry_path = Path(entry.path)
                        if _is_reparse_or_symlink(entry_path, entry_stat):
                            continue
                        resolved = entry_path.resolve(strict=True)
                        if not _is_within(root, resolved):
                            incomplete = True
                            continue
                        entry_depth = depth + 1
                        if stat.S_ISDIR(entry_stat.st_mode):
                            if entry_depth <= MAX_SCAN_DEPTH:
                                pending.append((resolved, entry_depth))
                            else:
                                incomplete = True
                            continue
                        if not stat.S_ISREG(entry_stat.st_mode) or resolved.suffix.casefold() != ".jsonl":
                            continue
                        if entry_stat.st_size > MAX_FILE_BYTES:
                            incomplete = True
                            continue
                        # On Windows, DirEntry.stat(follow_symlinks=False) can
                        # report st_ino=0 even for an ordinary file.  Resolve
                        # only after the reparse check, then take a second
                        # identity-bearing stat.  The opened handle is checked
                        # again against both this identity and the root below.
                        identity_stat = resolved.stat()
                        if (
                            not stat.S_ISREG(identity_stat.st_mode)
                            or identity_stat.st_size > MAX_FILE_BYTES
                        ):
                            incomplete = True
                            continue
                        identity = _identity_from_stat(identity_stat)
                        if identity is None:
                            incomplete = True
                            continue
                        eligible_files += 1
                        candidate = _FileCandidate(
                            path=resolved,
                            identity=identity,
                            modified_ns=int(identity_stat.st_mtime_ns),
                        )
                        path_key = os.path.normcase(str(resolved))
                        heap_item = (candidate.modified_ns, path_key, candidate)
                        if MAX_CANDIDATE_FILES <= 0:
                            incomplete = True
                        elif len(selected) < MAX_CANDIDATE_FILES:
                            heapq.heappush(selected, heap_item)
                        elif heap_item[:2] > selected[0][:2]:
                            heapq.heapreplace(selected, heap_item)
                    except (OSError, RuntimeError):
                        incomplete = True
        except OSError:
            incomplete = True

    if eligible_files > MAX_CANDIDATE_FILES:
        incomplete = True
    ordered = sorted(selected, key=lambda item: (item[0], item[1]), reverse=True)
    return [candidate for _, _, candidate in ordered], incomplete


def _latest_event_in_file(
    candidate: _FileCandidate,
    root: Path,
    now: datetime,
    budget: _ReadBudget,
    deadline: float,
) -> tuple[_ParsedEvent | None, bool]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    state = _ReadState()
    try:
        descriptor = os.open(candidate.path, flags)
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            opened_stat = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or opened_stat.st_size > MAX_FILE_BYTES
                or not _identity_matches(candidate.identity, opened_stat)
                or not _opened_path_is_within(handle.fileno(), root)
            ):
                return None, True

            merged: _ParsedEvent | None = None
            for raw_line in _iter_reverse_lines(
                handle,
                int(opened_stat.st_size),
                budget,
                deadline,
                state,
            ):
                try:
                    line = raw_line.decode("utf-8", errors="strict")
                    value = json.loads(
                        line,
                        object_pairs_hook=_unique_object,
                        parse_constant=_reject_json_constant,
                    )
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
                    continue
                event = _parse_event(value)
                if event is None:
                    continue
                # A clock-skewed/fabricated future timestamp must never outrank
                # a valid current observation.  Older observations remain a
                # DELAYED fallback when no recent data is available.
                if event.updated_at > now:
                    continue
                merged = event if merged is None else _merge_events(merged, event)
                # Session logs are append-only in observation order.  Once the
                # reverse scan has found a current seven-day value, it has the
                # primary quota needed for this file.  If the newest event was
                # five-hour-only, the scan naturally continues until it finds
                # and merges the corresponding weekly value.
                if (
                    merged.seven_day_updated_at is not None
                    and _is_recent(merged.seven_day_updated_at, now)
                ):
                    return merged, state.incomplete
            return merged, state.incomplete
    except OSError:
        return None, True


def _iter_reverse_lines(
    handle: Any,
    file_size: int,
    budget: _ReadBudget,
    deadline: float,
    state: _ReadState,
):
    """Yield bounded lines newest-first without materializing a file-wide list."""

    position = file_size
    suffix = b""
    discarding_oversized = False
    while position > 0:
        if time.monotonic() >= deadline:
            state.incomplete = True
            break
        if budget.remaining <= 0:
            budget.exhausted = True
            state.incomplete = True
            break
        read_size = min(READ_BLOCK_BYTES, position, budget.remaining)
        if read_size <= 0:
            budget.exhausted = True
            state.incomplete = True
            break
        position -= read_size
        handle.seek(position)
        chunk = handle.read(read_size)
        budget.remaining -= len(chunk)
        if len(chunk) != read_size:
            state.incomplete = True
            break

        data = chunk if discarding_oversized else chunk + suffix
        cursor = len(data)
        while True:
            newline = data.rfind(b"\n", 0, cursor)
            if newline < 0:
                break
            if discarding_oversized:
                discarding_oversized = False
            else:
                raw_line = data[newline + 1 : cursor]
                if raw_line.endswith(b"\r"):
                    raw_line = raw_line[:-1]
                if len(raw_line) > MAX_LINE_BYTES:
                    state.incomplete = True
                elif raw_line:
                    yield raw_line
            cursor = newline

        if discarding_oversized:
            suffix = b""
        else:
            suffix = data[:cursor]
            if len(suffix) > MAX_LINE_BYTES:
                suffix = b""
                discarding_oversized = True
                state.incomplete = True

    if position == 0 and not discarding_oversized and suffix:
        if suffix.endswith(b"\r"):
            suffix = suffix[:-1]
        if len(suffix) > MAX_LINE_BYTES:
            state.incomplete = True
        elif suffix:
            yield suffix


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-standard JSON numeric constant")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate JSON key")
        output[key] = value
    return output


def _parse_event(value: Any) -> _ParsedEvent | None:
    if not isinstance(value, dict) or value.get("type") != "event_msg":
        return None
    payload = value.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    rate_limits = payload.get("rate_limits")
    if not isinstance(rate_limits, dict):
        return None
    # Named limits are independent quota pools, such as the separate
    # GPT-5.3-Codex-Spark allowance.  The dashboard's CODEX LIMITS card shows
    # the account's general plan allowance, represented by a missing/null
    # limit_name in current session records.  Legacy records omitted this key,
    # so they remain supported.
    if rate_limits.get("limit_name") is not None:
        return None
    updated_at = _parse_iso_datetime(value.get("timestamp"))
    if updated_at is None:
        return None

    parsed: dict[str, tuple[float, datetime | None]] = {}
    for name in ("primary", "secondary"):
        if name not in rate_limits or rate_limits[name] is None:
            continue
        rate = _parse_rate(rate_limits[name])
        if rate is None:
            continue
        window_name, remaining, reset_at = rate
        if window_name in parsed:
            return None
        parsed[window_name] = (remaining, reset_at)
    if not parsed:
        return None

    five_hour = parsed.get("five_hour")
    seven_day = parsed.get("seven_day")
    return _ParsedEvent(
        five_hour_remaining_percent=five_hour[0] if five_hour else None,
        seven_day_remaining_percent=seven_day[0] if seven_day else None,
        five_hour_reset_at=five_hour[1] if five_hour else None,
        seven_day_reset_at=seven_day[1] if seven_day else None,
        five_hour_updated_at=updated_at if five_hour else None,
        seven_day_updated_at=updated_at if seven_day else None,
    )


def _parse_rate(value: Any) -> tuple[str, float, datetime | None] | None:
    if not isinstance(value, dict):
        return None
    window = _finite_number(value.get("window_minutes"))
    if window is None or not window.is_integer() or int(window) not in _WINDOWS:
        return None

    has_used = "used_percent" in value
    has_remaining = "remaining_percent" in value
    if has_used == has_remaining:
        return None
    percent = _finite_number(value.get("used_percent" if has_used else "remaining_percent"))
    if percent is None or not 0.0 <= percent <= 100.0:
        return None
    remaining = 100.0 - percent if has_used else percent

    reset_keys = [key for key in ("reset_at", "resets_at") if key in value]
    if len(reset_keys) > 1:
        left = _finite_number(value.get(reset_keys[0]))
        right = _finite_number(value.get(reset_keys[1]))
        if left is None or right is None or left != right:
            return None
        reset_number = left
    elif reset_keys:
        reset_number = _finite_number(value.get(reset_keys[0]))
    else:
        reset_number = None
    reset_at = None
    if reset_keys:
        if reset_number is None or not 0.0 <= reset_number <= _MAX_UNIX_TIMESTAMP:
            return None
        try:
            reset_at = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=reset_number)
        except OverflowError:
            return None
    return _WINDOWS[int(window)], remaining, reset_at


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.astimezone().astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def _is_recent(updated_at: datetime, now: datetime) -> bool:
    return now - RECENT_EVENT_AGE <= updated_at <= now


def _identity_from_stat(path_stat: os.stat_result) -> _FileIdentity | None:
    device = int(getattr(path_stat, "st_dev", 0))
    inode = int(getattr(path_stat, "st_ino", 0))
    # CPython exposes the Windows volume serial/file index through st_dev and
    # st_ino.  If the filesystem cannot provide an identity, fail closed rather
    # than accepting a path-only check that can be swapped before open().
    if os.name == "nt" and inode == 0:
        return None
    return _FileIdentity(device=device, inode=inode)


def _identity_matches(expected: _FileIdentity, opened_stat: os.stat_result) -> bool:
    actual = _identity_from_stat(opened_stat)
    return actual is not None and actual == expected


def _opened_path_is_within(descriptor: int, root: Path) -> bool:
    if os.name != "nt":
        return True
    final_path = _windows_final_path(descriptor)
    return final_path is not None and _is_within(root, final_path)


def _windows_final_path(descriptor: int) -> Path | None:
    """Resolve a Windows file descriptor through its already-open handle."""

    if os.name != "nt":
        return None
    try:
        import msvcrt

        handle = msvcrt.get_osfhandle(descriptor)
        if handle == -1:
            return None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = kernel32.GetFinalPathNameByHandleW
        function.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        function.restype = ctypes.c_uint32
        capacity = 32_768
        buffer = ctypes.create_unicode_buffer(capacity)
        length = function(ctypes.c_void_p(handle), buffer, capacity, 0)
        if length == 0 or length >= capacity:
            return None
        value = buffer.value
    except (ImportError, OSError, OverflowError, ValueError, AttributeError):
        return None

    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    path = Path(value)
    return path if path.is_absolute() else None


def _is_reparse_or_symlink(path: Path, path_stat: os.stat_result) -> bool:
    attributes = int(getattr(path_stat, "st_file_attributes", 0))
    return path.is_symlink() or bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def _has_reparse_component(path: Path) -> bool:
    current = Path(path.anchor)
    parts = path.parts[1:] if path.anchor else path.parts
    for part in parts:
        current /= part
        current_stat = current.lstat()
        if _is_reparse_or_symlink(current, current_stat):
            return True
    return False


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        root_text = os.path.normcase(str(root))
        candidate_text = os.path.normcase(str(candidate))
        return os.path.commonpath((root_text, candidate_text)) == root_text
    except ValueError:
        return False
