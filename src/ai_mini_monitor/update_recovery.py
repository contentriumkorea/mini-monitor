"""Read-only next-launch guidance for interrupted portable updates."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_JOURNAL_BYTES = 4 * 1024 * 1024
MAX_CANDIDATES = 32
MAX_DIRECTORY_ENTRIES = 4096
_STAGE_NAME = re.compile(r"\.mini-monitor-update-[A-Za-z0-9_-]{6,40}\Z")
_BACKUP_NAME = re.compile(r"\.mini-monitor-backup-[0-9a-f]{32}\Z")
_UNRESOLVED = {"old_backed_up", "new_installed", "swap_failed", "needs_manual_recovery_alive", "needs_manual_recovery"}


@dataclass(frozen=True, slots=True)
class RecoveryNotice:
    state: str
    journal_path: Path
    backup_path: Path

    @property
    def message(self) -> str:
        return (
            "이전 업데이트가 정상 완료되지 않았습니다. 현재 실행 중인 파일은 자동으로 변경하지 않습니다.\n\n"
            f"이전 프로그램 백업: {self.backup_path}\n"
            f"복구 기록: {self.journal_path}\n\n"
            "Mini Monitor를 종료한 뒤 공식 릴리스를 다시 설치하거나 백업을 수동으로 복원하세요. "
            "복구가 끝날 때까지 백업 폴더를 삭제하지 마세요."
        )


def _reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _raw_regular_path(path: Path, *, directory: bool) -> Path | None:
    if not path.is_absolute() or ".." in path.parts:
        return None
    raw = Path(os.path.abspath(path))
    if any(_reparse(component) for component in (raw, *raw.parents)):
        return None
    if directory and not raw.is_dir():
        return None
    if not directory and not raw.is_file():
        return None
    return raw


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate journal field")
        result[key] = value
    return result


def _same(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _candidate(stage: Path, root: Path) -> RecoveryNotice | None:
    if not _STAGE_NAME.fullmatch(stage.name) or _raw_regular_path(stage, directory=True) is None:
        return None
    journal = stage / "update-journal.json"
    if _raw_regular_path(journal, directory=False) is None or journal.stat().st_size > MAX_JOURNAL_BYTES:
        return None
    with journal.open("rb") as stream:
        raw = stream.read(MAX_JOURNAL_BYTES + 1)
    if len(raw) > MAX_JOURNAL_BYTES:
        return None
    record = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(record, dict) or type(record.get("schema_version")) is not int or record["schema_version"] != 1:
        return None
    state = record.get("state")
    if state not in _UNRESOLVED:
        return None
    fields = ("install_root", "staged_root", "backup_root", "ack_path")
    if not all(isinstance(record.get(field), str) and len(record[field]) <= 2048 for field in fields):
        return None
    install = Path(record["install_root"])
    staged = Path(record["staged_root"])
    backup = Path(record["backup_root"])
    ack = Path(record["ack_path"])
    if (not _same(install, root) or not _same(staged, stage / "Mini-Monitor")
            or not _same(ack, stage / "startup-ack.json")
            or not _BACKUP_NAME.fullmatch(backup.name)
            or not _same(backup.parent, root.parent)
            or _raw_regular_path(backup, directory=True) is None):
        return None
    return RecoveryNotice(state, journal, backup)


def discover_update_recovery(install_root: Path) -> RecoveryNotice | None:
    """Inspect bounded update-owned journals without changing any file."""

    try:
        root = _raw_regular_path(install_root, directory=True)
        if root is None or root.name != "Mini-Monitor":
            return None
        selected: RecoveryNotice | None = None
        newest = -1.0
        candidates = 0
        with os.scandir(root.parent) as entries:
            for number, entry in enumerate(entries):
                if number >= MAX_DIRECTORY_ENTRIES or candidates >= MAX_CANDIDATES:
                    break
                if not _STAGE_NAME.fullmatch(entry.name):
                    continue
                candidates += 1
                stage = Path(entry.path)
                try:
                    notice = _candidate(stage, root)
                    if notice is not None:
                        modified = notice.journal_path.stat().st_mtime
                        if modified > newest:
                            newest = modified
                            selected = notice
                except (OSError, ValueError, UnicodeError, TypeError):
                    continue
        return selected
    except (OSError, ValueError, TypeError):
        return None
