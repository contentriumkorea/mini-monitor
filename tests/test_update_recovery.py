from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from ai_mini_monitor.update_recovery import discover_update_recovery


def _journal(tmp_path: Path, *, state: str, backup: bool = True) -> tuple[Path, Path, Path]:
    install = tmp_path / "Mini-Monitor"
    install.mkdir()
    stage = tmp_path / ".mini-monitor-update-abc12345"
    stage.mkdir()
    backup_path = tmp_path / (".mini-monitor-backup-" + "a" * 32)
    if backup:
        backup_path.mkdir()
    record = {
        "schema_version": 1,
        "state": state,
        "install_root": str(install),
        "staged_root": str(stage / "Mini-Monitor"),
        "backup_root": str(backup_path),
        "ack_path": str(stage / "startup-ack.json"),
        "nonce": "b" * 32,
        "version": "0.2.0",
        "config_path": "C:/secret/account.json",
    }
    (stage / "update-journal.json").write_text(json.dumps(record), encoding="utf-8")
    return install, stage, backup_path


@pytest.mark.parametrize("state", ["old_backed_up", "new_installed", "swap_failed", "needs_manual_recovery_alive"])
def test_interrupted_owned_update_surfaces_verified_backup_path(tmp_path, state) -> None:
    install, stage, backup = _journal(tmp_path, state=state)

    notice = discover_update_recovery(install)

    assert notice is not None
    assert notice.state == state
    assert notice.journal_path == stage / "update-journal.json"
    assert notice.backup_path == backup
    assert str(backup) in notice.message
    assert "C:/secret/account.json" not in notice.message
    assert "수동" in notice.message


def test_release_sized_journal_is_not_silently_ignored(tmp_path) -> None:
    install, stage, _ = _journal(tmp_path, state="new_installed")
    path = stage / "update-journal.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["old_files"] = [[f"_internal/module-{number:04d}.pyc", "a" * 64] for number in range(1500)]
    record["new_files"] = [[f"Mini-Monitor/_internal/module-{number:04d}.pyc", "b" * 64]
                           for number in range(1500)]
    path.write_text(json.dumps(record), encoding="utf-8")
    assert path.stat().st_size > 64 * 1024
    assert discover_update_recovery(install) is not None


@pytest.mark.parametrize(
    ("state", "backup"),
    [("prepared", False), ("parent_exit_timeout", False),
     ("healthy_backup_retained", True), ("rolled_back_start_failed", False),
     ("rolled_back_no_ack", False), ("needs_manual_recovery_alive", False)],
)
def test_resolved_or_unowned_update_does_not_repeat_warning(tmp_path, state, backup) -> None:
    install, _, _ = _journal(tmp_path, state=state, backup=backup)
    assert discover_update_recovery(install) is None


@pytest.mark.parametrize("forgery", ["foreign_install", "foreign_stage", "foreign_backup", "oversize"])
def test_foreign_or_oversize_journal_is_ignored(tmp_path, forgery) -> None:
    install, stage, _ = _journal(tmp_path, state="needs_manual_recovery_alive")
    path = stage / "update-journal.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    if forgery == "foreign_install":
        record["install_root"] = str(tmp_path / "Other-App")
    elif forgery == "foreign_stage":
        record["staged_root"] = str(tmp_path / "other" / "Mini-Monitor")
    elif forgery == "foreign_backup":
        record["backup_root"] = str(tmp_path / "other" / (".mini-monitor-backup-" + "a" * 32))
    else:
        record["padding"] = "x" * (4 * 1024 * 1024)
    path.write_text(json.dumps(record), encoding="utf-8")
    assert discover_update_recovery(install) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows junction fixture")
def test_reparse_owned_stage_is_ignored_without_following_target(tmp_path) -> None:
    install, stage, _ = _journal(tmp_path, state="needs_manual_recovery_alive")
    target = tmp_path / "actual-stage"
    stage.rename(target)
    subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(stage), str(target)],
                   check=True, capture_output=True, text=True)
    assert discover_update_recovery(install) is None
    assert (target / "update-journal.json").is_file()
