"""Automatic cleanup must never turn a failed update into lost user data."""

import hashlib
import json
import os
import threading
import time
import zipfile
import uuid
import base64
import subprocess
from pathlib import Path

import pytest

from ai_mini_monitor import updater


def _inventory(root: Path, payload: bytes) -> list[list[str]]:
    root.mkdir()
    (root / "Mini-Monitor.exe").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (root / "SHA256SUMS.txt").write_text(f"{digest}  Mini-Monitor.exe\n", encoding="utf-8")
    return [["mini-monitor.exe", digest]]


def _update(tmp_path: Path, state="healthy_backup_retained"):
    root = tmp_path / "Mini-Monitor"
    new_files = _inventory(root, b"new application")
    backup = tmp_path / (".mini-monitor-backup-" + "a" * 32)
    old_files = _inventory(backup, b"old application")
    stage = tmp_path / ".mini-monitor-update-abcdef12"
    stage.mkdir()
    record = {
        "schema_version": 1, "state": state, "install_root": str(root),
        "staged_root": str(stage / "Mini-Monitor"), "backup_root": str(backup),
        "ack_path": str(stage / "startup-ack.json"), "nonce": "b" * 32,
        "version": "0.2.7", "old_files": old_files, "new_files": new_files,
        "old_inventory_sha256": hashlib.sha256((backup / "SHA256SUMS.txt").read_bytes()).hexdigest(),
        "config_path": None,
    }
    (stage / "update-journal.json").write_text(json.dumps(record), encoding="utf-8")
    (stage / "startup-ack.json").write_text(json.dumps({"nonce": "b" * 32, "ready": True, "version": "0.2.7"}), encoding="utf-8")
    with zipfile.ZipFile(stage / "release.zip", "w") as package:
        package.writestr("Mini-Monitor/Mini-Monitor.exe", b"new application")
        package.writestr("Mini-Monitor/SHA256SUMS.txt", (root / "SHA256SUMS.txt").read_bytes())
    (stage / "Apply-Update.ps1").write_bytes(b"bundled helper")
    record["helper_sha256"] = hashlib.sha256(b"bundled helper").hexdigest()
    record["archive_sha256"] = hashlib.sha256((stage / "release.zip").read_bytes()).hexdigest()
    record["new_files"] = [["Mini-Monitor/Mini-Monitor.exe", hashlib.sha256(b"new application").hexdigest()],
                           ["Mini-Monitor/SHA256SUMS.txt", hashlib.sha256((root / "SHA256SUMS.txt").read_bytes()).hexdigest()]]
    (stage / "update-journal.json").write_text(json.dumps(record), encoding="utf-8")
    return root, stage, backup


def _trash(monkeypatch, tmp_path):
    trash = tmp_path / "test-recycle-bin"
    trash.mkdir()

    def recycle(path):
        path.rename(trash / path.name)
        return True

    monkeypatch.setattr(updater, "_recycle_directory", recycle, raising=False)
    monkeypatch.setattr(updater, "_repair_update_references", lambda *args: True, raising=False)
    return trash


def test_success_recycles_whole_backup_and_stage_preserving_current_and_login(tmp_path, monkeypatch):
    root, stage, backup = _update(tmp_path)
    data = tmp_path / "AI-Mini-Monitor"
    data.mkdir()
    (data / "config.json").write_bytes(b"settings")
    (data / "auth.json").write_bytes(b"login data")
    trash = _trash(monkeypatch, tmp_path)
    assert updater.cleanup_healthy_update_backup(root) == 1
    assert not backup.exists() and not stage.exists()
    assert (trash / backup.name / "Mini-Monitor.exe").read_bytes() == b"old application"
    assert (trash / stage.name / "update-journal.json").is_file()
    assert (root / "Mini-Monitor.exe").read_bytes() == b"new application"
    assert (data / "config.json").read_bytes() == b"settings"
    assert (data / "auth.json").read_bytes() == b"login data"
    assert updater.cleanup_healthy_update_backup(root) == 0


def test_recycle_failure_preserves_backup_and_journal_for_retry(tmp_path, monkeypatch):
    root, stage, backup = _update(tmp_path)
    monkeypatch.setattr(updater, "_recycle_directory", lambda path: False, raising=False)
    assert updater.cleanup_healthy_update_backup(root) == 0
    assert (backup / "Mini-Monitor.exe").read_bytes() == b"old application"
    assert json.loads((stage / "update-journal.json").read_bytes())["state"] == "healthy_backup_retained"


@pytest.mark.parametrize("state", ["prepared", "new_installed", "needs_manual_recovery", "swap_failed", "rolled_back_no_ack"])
def test_incomplete_or_failed_update_is_never_recycled(tmp_path, monkeypatch, state):
    root, stage, backup = _update(tmp_path, state)
    trash = _trash(monkeypatch, tmp_path)
    assert updater.cleanup_healthy_update_backup(root) == 0
    assert backup.is_dir() and stage.is_dir() and not list(trash.iterdir())


@pytest.mark.parametrize("change", ["current_file", "backup_file", "backup_user_file", "backup_empty_dir", "stage_user_file", "stage_archive", "stage_helper", "stage_ack", "foreign_backup", "custom_config", "fake_stage", "duplicate_journal"])
def test_changed_or_unowned_tree_is_preserved(tmp_path, monkeypatch, change):
    root, stage, backup = _update(tmp_path)
    journal = stage / "update-journal.json"
    record = json.loads(journal.read_bytes())
    if change == "current_file":
        (root / "Mini-Monitor.exe").write_bytes(b"changed")
    elif change == "backup_file":
        (backup / "Mini-Monitor.exe").write_bytes(b"changed")
    elif change == "backup_user_file":
        (backup / "personal.txt").write_bytes(b"user file")
    elif change == "backup_empty_dir":
        (backup / "personal-folder").mkdir()
    elif change == "stage_user_file":
        (stage / "personal.txt").write_bytes(b"user file")
    elif change == "stage_archive":
        (stage / "release.zip").write_bytes(b"personal archive")
    elif change == "stage_helper":
        (stage / "Apply-Update.ps1").write_bytes(b"personal script")
    elif change == "stage_ack":
        (stage / "startup-ack.json").write_bytes(b"personal file")
    elif change == "foreign_backup":
        record["backup_root"] = str(root)
    elif change == "custom_config":
        record["config_path"] = str(backup / "Mini-Monitor.exe")
    elif change == "fake_stage":
        replacement = tmp_path / ".mini-monitor-update-x"
        stage.rename(replacement)
        stage = replacement
        journal = stage / "update-journal.json"
        record["staged_root"] = str(stage / "Mini-Monitor")
        record["ack_path"] = str(stage / "startup-ack.json")
    if change == "duplicate_journal":
        journal.write_text('{"state":"healthy_backup_retained",' + json.dumps(record)[1:], encoding="utf-8")
    else:
        journal.write_text(json.dumps(record), encoding="utf-8")
    trash = _trash(monkeypatch, tmp_path)
    assert updater.cleanup_healthy_update_backup(root) == 0
    assert backup.is_dir() and stage.is_dir() and root.is_dir()
    assert not list(trash.iterdir())


def test_locked_update_helper_prevents_cleanup(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows install-parent lock")
    import msvcrt
    root, stage, backup = _update(tmp_path)
    trash = _trash(monkeypatch, tmp_path)
    with (tmp_path / ".mini-monitor-update.lock").open("a+b") as lock:
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            assert updater.cleanup_healthy_update_backup(root) == 0
            assert backup.is_dir() and stage.is_dir() and not list(trash.iterdir())
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def test_native_recycle_moves_only_the_named_test_directory(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows Recycle Bin")
    recycle = getattr(updater, "_recycle_directory", None)
    assert callable(recycle), "cleanup needs a recoverable Windows recycle operation"
    retired = tmp_path / ("retired-fixture-" + uuid.uuid4().hex)
    retired.mkdir()
    (retired / "program.txt").write_bytes(b"synthetic test program")
    sibling = tmp_path / "keep.txt"
    sibling.write_bytes(b"keep")
    assert recycle(retired)
    assert not retired.exists() and sibling.read_bytes() == b"keep"
    # Verify the actual recoverable payload, not merely disappearance.
    code = r"""
    $bin = (New-Object -ComObject Shell.Application).Namespace(10)
    foreach ($item in $bin.Items()) {
        if ($item.Name -eq $env:MONITOR_RECYCLE_FIXTURE -and
            $item.ExtendedProperty('System.Recycle.DeletedFrom') -eq $env:MONITOR_RECYCLE_PARENT) {
            [Console]::Write($item.Path)
        }
    }
    """
    from ai_mini_monitor.security.windows_system import windows_powershell_paths, pin_powershell_modules
    powershell, modules = windows_powershell_paths()
    environment = dict(os.environ)
    pin_powershell_modules(environment, modules)
    environment.update(MONITOR_RECYCLE_FIXTURE=retired.name, MONITOR_RECYCLE_PARENT=str(tmp_path))
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand",
                             base64.b64encode(code.encode("utf-16le")).decode()],
                            capture_output=True, text=True, timeout=20, env=environment,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0 and result.stdout, result.stderr
    assert (Path(result.stdout) / "program.txt").read_bytes() == b"synthetic test program"


def test_post_update_cleanup_waits_for_helper_then_recycles_without_another_launch(tmp_path, monkeypatch):
    root, stage, backup = _update(tmp_path, "new_installed")
    trash = _trash(monkeypatch, tmp_path)
    finish = getattr(updater, "finish_update_cleanup", None)
    assert callable(finish), "healthy relaunch must automatically finish cleanup"
    worker = threading.Thread(target=finish, args=(root,), kwargs={"wait_for_update": True})
    worker.start()
    time.sleep(0.1)
    assert backup.exists()  # A startup ack alone is not the helper's success.
    journal = stage / "update-journal.json"
    record = json.loads(journal.read_bytes())
    record["state"] = "healthy_backup_retained"
    journal.write_text(json.dumps(record), encoding="utf-8")
    worker.join(5)
    assert not worker.is_alive()
    assert not backup.exists() and not stage.exists()
    assert (trash / backup.name / "Mini-Monitor.exe").read_bytes() == b"old application"


def test_stage_recycle_failure_can_be_retried_after_backup_was_recycled(tmp_path, monkeypatch):
    root, stage, backup = _update(tmp_path)
    trash = tmp_path / "test-recycle-bin"
    trash.mkdir()

    def recycle(path):
        if path == stage:
            return False
        path.rename(trash / path.name)
        return True

    monkeypatch.setattr(updater, "_recycle_directory", recycle, raising=False)
    assert updater.cleanup_healthy_update_backup(root) == 1
    assert stage.exists() and not backup.exists()
    assert json.loads((stage / "update-journal.json").read_bytes())["state"] == "backup_recycled"
    monkeypatch.setattr(updater, "_recycle_directory", lambda path: (path.rename(trash / path.name) or True))
    assert updater.cleanup_healthy_update_backup(root) == 0
    assert not stage.exists()


def test_failed_shortcut_repair_keeps_its_old_target_available(tmp_path, monkeypatch):
    root, stage, backup = _update(tmp_path)
    _trash(monkeypatch, tmp_path)
    monkeypatch.setattr(updater, "_repair_update_references", lambda *args: False, raising=False)
    assert updater.cleanup_healthy_update_backup(root) == 0
    assert backup.exists() and stage.exists()


def test_file_added_during_link_repair_is_not_swept_up(tmp_path, monkeypatch):
    root, stage, backup = _update(tmp_path)
    _trash(monkeypatch, tmp_path)

    def repair(*args):
        (backup / "personal.txt").write_bytes(b"added during repair")
        return True

    monkeypatch.setattr(updater, "_repair_update_references", repair)
    assert updater.cleanup_healthy_update_backup(root) == 0
    assert (backup / "personal.txt").read_bytes() == b"added during repair"


def test_file_added_to_stage_during_backup_recycle_is_preserved(tmp_path, monkeypatch):
    root, stage, backup = _update(tmp_path)
    trash = _trash(monkeypatch, tmp_path)

    def recycle(path):
        if path == backup:
            (stage / "personal.txt").write_bytes(b"added during backup recycle")
        path.rename(trash / path.name)
        return True

    monkeypatch.setattr(updater, "_recycle_directory", recycle)
    assert updater.cleanup_healthy_update_backup(root) == 1
    assert not backup.exists()
    assert (stage / "personal.txt").read_bytes() == b"added during backup recycle"


def test_previous_completed_backup_does_not_skip_current_pending_update(tmp_path, monkeypatch):
    root, stage, backup = _update(tmp_path)
    trash = _trash(monkeypatch, tmp_path)
    current = tmp_path / ".mini-monitor-update-current1"
    current.mkdir()
    current_backup = tmp_path / (".mini-monitor-backup-" + "c" * 32)
    _inventory(current_backup, b"old application")
    record = json.loads((stage / "update-journal.json").read_bytes())
    record.update(state="new_installed", backup_root=str(current_backup), staged_root=str(current / "Mini-Monitor"), ack_path=str(current / "startup-ack.json"))
    for name in ("Apply-Update.ps1", "release.zip", "startup-ack.json"):
        (current / name).write_bytes((stage / name).read_bytes())
    (current / "update-journal.json").write_text(json.dumps(record), encoding="utf-8")
    worker = threading.Thread(target=updater.finish_update_cleanup, args=(root,), kwargs={"wait_for_update": True})
    worker.start()
    deadline = time.monotonic() + 3
    while backup.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert not backup.exists()
    record["state"] = "healthy_backup_retained"
    (current / "update-journal.json").write_text(json.dumps(record), encoding="utf-8")
    worker.join(5)
    assert not worker.is_alive()
    assert not current_backup.exists() and not current.exists()
    assert (trash / current_backup.name).is_dir()


def test_previous_027_journal_without_archive_digest_is_still_cleaned(tmp_path, monkeypatch):
    root, stage, backup = _update(tmp_path)
    _trash(monkeypatch, tmp_path)
    record = json.loads((stage / "update-journal.json").read_bytes())
    del record["archive_sha256"]
    (stage / "update-journal.json").write_text(json.dumps(record), encoding="utf-8")
    assert updater.cleanup_healthy_update_backup(root) == 1
    assert not backup.exists() and not stage.exists()


def test_new_update_is_not_launched_while_cleanup_holds_the_install(tmp_path, monkeypatch):
    from test_updater import _new_archive
    root, stage, backup = _update(tmp_path)
    _trash(monkeypatch, tmp_path)
    archive_data, manifest = _new_archive(tmp_path / "next.zip")
    monkeypatch.setattr(updater, "_download_archive", lambda url, path, size: path.write_bytes(archive_data))
    prepared = updater.stage_update(manifest, install_root=root, config_path=None)
    entered, release = threading.Event(), threading.Event()

    def repair(*args):
        entered.set()
        release.wait(5)
        return True

    monkeypatch.setattr(updater, "_repair_update_references", repair)
    launches = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *args, **kwargs: launches.append(args))
    worker = threading.Thread(target=updater.cleanup_healthy_update_backup, args=(root,))
    worker.start()
    try:
        assert entered.wait(3)
        assert not updater.launch_update_helper(prepared, parent_pid=123)
        assert not launches
        assert (root / "Mini-Monitor.exe").read_bytes() == b"new application"
    finally:
        release.set()
        worker.join(5)
