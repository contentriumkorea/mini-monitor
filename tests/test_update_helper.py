from __future__ import annotations

import hashlib
import base64
import ctypes
import json
import os
import subprocess
import time
import zipfile
from pathlib import Path

import pytest

from ai_mini_monitor.updater import UpdateManifest, stage_update


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.mark.skipif(os.name != "nt", reason="Windows update helper")
def test_failed_new_executable_rolls_back_without_touching_user_data(tmp_path, monkeypatch) -> None:
    import ai_mini_monitor.updater as updater

    install = tmp_path / "Mini-Monitor"
    install.mkdir()
    (install / "Mini-Monitor.exe").write_bytes(b"old desktop exe")
    (install / "SHA256SUMS.txt").write_text(_digest(b"old desktop exe") + "  Mini-Monitor.exe\n", encoding="utf-8")
    user_data = tmp_path / "AI-Mini-Monitor-user-data"
    user_data.mkdir()
    (user_data / "settings.json").write_text('{"keep":true}', encoding="utf-8")

    archive_path = tmp_path / "new.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Mini-Monitor/Mini-Monitor.exe", b"not a Windows executable")
    archive_bytes = archive_path.read_bytes()
    manifest = UpdateManifest(
        "0.2.0", "stable",
        "https://github.com/contentriumkorea/mini-monitor/releases/download/v0.2.0/Mini-Monitor.zip",
        _digest(archive_bytes), len(archive_bytes),
        (("Mini-Monitor/Mini-Monitor.exe", _digest(b"not a Windows executable")),),
    )
    monkeypatch.setattr(updater, "_download_archive", lambda _url, target, _size: target.write_bytes(archive_bytes))
    prepared = stage_update(manifest, install_root=install, config_path=None)
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
         str(prepared.helper_path), "-JournalPath", str(prepared.journal_path),
         "-JournalSha256", _digest(prepared.journal_path.read_bytes()),
         "-HelperSha256", _digest(prepared.helper_path.read_bytes()),
         "-ParentPid", "99999999"],
        cwd=prepared.helper_path.parent, capture_output=True, text=True, timeout=15,
        env={**os.environ, "MINI_MONITOR_UPDATE_TEST_NO_NOTICE": "1"},
    )
    assert result.returncode == 3, result.stderr
    assert (install / "Mini-Monitor.exe").read_bytes() == b"old desktop exe"
    assert (user_data / "settings.json").read_text(encoding="utf-8") == '{"keep":true}'
    assert json.loads(prepared.journal_path.read_text(encoding="utf-8"))["state"] == "rolled_back_start_failed"
    assert list(tmp_path.glob(".mini-monitor-failed-*"))


def _compiled_test_executable(tmp_path: Path, *, write_ack: bool, version: str, lifetime_ms: int) -> bytes:
    payload = json.dumps({"nonce": "@NONCE@", "ready": True, "version": version}).encode("utf-8")
    encoded = base64.b64encode(payload).decode("ascii")
    acknowledgement = (
        "var json = System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(\"" + encoded + "\")); "
        "File.WriteAllText(Environment.GetEnvironmentVariable(\"MINI_MONITOR_UPDATE_ACK_PATH\"), "
        "json.Replace(\"@NONCE@\", Environment.GetEnvironmentVariable(\"MINI_MONITOR_UPDATE_ACK_NONCE\"))); "
        "File.WriteAllText(Environment.GetEnvironmentVariable(\"MINI_MONITOR_UPDATE_ACK_PATH\") + \".args\", "
        "String.Join(\"|\", Environment.GetCommandLineArgs())); "
    ) if write_ack else ""
    source = tmp_path / "fixture.cs"
    source.write_text(
        "using System; using System.IO; using System.Threading; "
        "class Program { static int Main() { " + acknowledgement
        + f"Thread.Sleep({lifetime_ms}); return 0; }} }}",
        encoding="utf-8",
    )
    executable = tmp_path / "fixture.exe"
    compiler = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([str(compiler), "/nologo", f"/out:{executable}", str(source)],
                   check=True, capture_output=True, text=True)
    return executable.read_bytes()


def _run_helper(
    prepared, *, timeout_seconds: int = 60, culture: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    journal = json.loads(prepared.journal_path.read_text(encoding="utf-8"))
    journal["ack_timeout_seconds"] = timeout_seconds
    prepared.journal_path.write_text(json.dumps(journal), encoding="utf-8")
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    script = prepared.helper_path
    prefix: list[str] = []
    if culture is not None:
        wrapper = prepared.helper_path.parent / "culture-wrapper.ps1"
        wrapper.write_text(
            "param([string]$HelperPath,[string]$JournalPath,[string]$JournalSha256,"
            "[string]$HelperSha256,[int]$ParentPid)\n"
            f"[Threading.Thread]::CurrentThread.CurrentCulture = [Globalization.CultureInfo]::GetCultureInfo('{culture}')\n"
            "& $HelperPath -JournalPath $JournalPath -JournalSha256 $JournalSha256 "
            "-HelperSha256 $HelperSha256 -ParentPid $ParentPid\n"
            "exit $LASTEXITCODE\n",
            encoding="utf-8",
        )
        script = wrapper
        prefix = ["-HelperPath", str(prepared.helper_path)]
    return subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
         str(script), *prefix, "-JournalPath", str(prepared.journal_path),
         "-JournalSha256", _digest(prepared.journal_path.read_bytes()),
         "-HelperSha256", _digest(prepared.helper_path.read_bytes()), "-ParentPid", "99999999"],
        cwd=prepared.helper_path.parent, capture_output=True, text=True, timeout=15,
        env={**os.environ, "MINI_MONITOR_UPDATE_TEST_NO_NOTICE": "1", **(extra_env or {})},
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows directory rename and sharing semantics")
def test_locked_nested_file_aborts_swap_without_moving_any_old_file(tmp_path, monkeypatch) -> None:
    """A live secondary process's DLL must not leave a half-moved install."""
    import ai_mini_monitor.updater as updater

    install = tmp_path / "Mini-Monitor"
    nested = install / "_internal"
    nested.mkdir(parents=True)
    old_exe = b"old desktop exe"
    old_dll = b"old nested dll"
    (install / "Mini-Monitor.exe").write_bytes(old_exe)
    locked_file = nested / "runtime.dll"
    locked_file.write_bytes(old_dll)
    (install / "SHA256SUMS.txt").write_text(
        f"{_digest(old_exe)}  Mini-Monitor.exe\n{_digest(old_dll)}  _internal/runtime.dll\n",
        encoding="utf-8",
    )
    archive = tmp_path / "new.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("Mini-Monitor/Mini-Monitor.exe", b"new desktop exe")
    archive_data = archive.read_bytes()
    manifest = UpdateManifest(
        "0.2.0", "stable",
        "https://github.com/contentriumkorea/mini-monitor/releases/download/v0.2.0/Mini-Monitor.zip",
        _digest(archive_data), len(archive_data),
        (("Mini-Monitor/Mini-Monitor.exe", _digest(b"new desktop exe")),),
    )
    monkeypatch.setattr(updater, "_download_archive", lambda _url, target, _size: target.write_bytes(archive_data))
    prepared = stage_update(manifest, install_root=install, config_path=None)

    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    create_file.restype = ctypes.c_void_p
    handle = create_file(str(locked_file), 0x80000000, 0x1, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    try:
        result = _run_helper(prepared)
        assert result.returncode == 3, result.stderr
        assert (install / "Mini-Monitor.exe").read_bytes() == old_exe
        assert locked_file.read_bytes() == old_dll
        assert json.loads(prepared.journal_path.read_text(encoding="utf-8"))["state"] == "swap_failed"
        assert not list(tmp_path.glob(".mini-monitor-backup-*"))
        assert not list(tmp_path.glob(".mini-monitor-failed-*"))
    finally:
        assert ctypes.windll.kernel32.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="Windows helper lock semantics")
def test_independent_stage_cannot_swap_while_install_lock_is_held(tmp_path, monkeypatch) -> None:
    import ai_mini_monitor.updater as updater

    install = tmp_path / "Mini-Monitor"
    install.mkdir()
    old_exe = b"old desktop exe"
    (install / "Mini-Monitor.exe").write_bytes(old_exe)
    (install / "SHA256SUMS.txt").write_text(
        f"{_digest(old_exe)}  Mini-Monitor.exe\n", encoding="utf-8",
    )
    archive = tmp_path / "new.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("Mini-Monitor/Mini-Monitor.exe", b"new desktop exe")
    archive_data = archive.read_bytes()
    manifest = UpdateManifest(
        "0.2.0", "stable",
        "https://github.com/contentriumkorea/mini-monitor/releases/download/v0.2.0/Mini-Monitor.zip",
        _digest(archive_data), len(archive_data),
        (("Mini-Monitor/Mini-Monitor.exe", _digest(b"new desktop exe")),),
    )
    monkeypatch.setattr(updater, "_download_archive", lambda _url, target, _size: target.write_bytes(archive_data))
    first = stage_update(manifest, install_root=install, config_path=None)
    prepared = stage_update(manifest, install_root=install, config_path=None)
    assert first.helper_path.parent != prepared.helper_path.parent
    lock_path = install.parent / ".mini-monitor-update.lock"
    lock_path.touch()
    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    create_file.restype = ctypes.c_void_p
    handle = create_file(str(lock_path), 0x80000000, 0, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    try:
        result = _run_helper(prepared)
        assert result.returncode != 0
        assert (install / "Mini-Monitor.exe").read_bytes() == old_exe
        assert json.loads(prepared.journal_path.read_text(encoding="utf-8"))["state"] == "prepared"
        assert not list(tmp_path.glob(".mini-monitor-backup-*"))
    finally:
        assert ctypes.windll.kernel32.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="Windows update helper")
@pytest.mark.parametrize(
    ("runtime_version", "lifetime_ms", "expected_code", "expected_state"),
    [("0.2.0", 2000, 0, "healthy_backup_retained"),
     ("0.1.0", 1000, 5, "rolled_back_no_ack"),
     ("", 8000, 4, "needs_manual_recovery_alive")],
)
def test_helper_accepts_only_correct_version_ack_or_preserves_backup(
    tmp_path, monkeypatch, runtime_version, lifetime_ms, expected_code, expected_state,
) -> None:
    import ai_mini_monitor.updater as updater

    install = tmp_path / "Mini-Monitor"
    install.mkdir()
    (install / "Mini-Monitor.exe").write_bytes(b"old desktop exe")
    (install / "SHA256SUMS.txt").write_text(_digest(b"old desktop exe") + "  Mini-Monitor.exe\n", encoding="utf-8")
    new_exe = _compiled_test_executable(
        tmp_path, write_ack=bool(runtime_version), version=runtime_version, lifetime_ms=lifetime_ms,
    )
    archive = tmp_path / "new.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("Mini-Monitor/Mini-Monitor.exe", new_exe)
    archive_bytes = archive.read_bytes()
    manifest = UpdateManifest(
        "0.2.0", "stable",
        "https://github.com/contentriumkorea/mini-monitor/releases/download/v0.2.0/Mini-Monitor.zip",
        _digest(archive_bytes), len(archive_bytes),
        (("Mini-Monitor/Mini-Monitor.exe", _digest(new_exe)),),
    )
    monkeypatch.setattr(updater, "_download_archive", lambda _url, target, _size: target.write_bytes(archive_bytes))
    prepared = stage_update(manifest, install_root=install, config_path=None)
    result = _run_helper(prepared, timeout_seconds=1)
    assert result.returncode == expected_code, result.stderr
    state = json.loads(prepared.journal_path.read_text(encoding="utf-8"))
    assert state["state"] == expected_state
    backup = Path(state["backup_root"])
    if expected_state == "rolled_back_no_ack":
        assert (install / "Mini-Monitor.exe").read_bytes() == b"old desktop exe"
        assert not backup.exists()
    else:
        assert backup.is_dir()
        assert (backup / "Mini-Monitor.exe").read_bytes() == b"old desktop exe"
    if expected_state == "healthy_backup_retained":
        assert updater.cleanup_healthy_update_backup(install) == 1
        assert not backup.exists()
    elif expected_state == "needs_manual_recovery_alive":
        assert updater.cleanup_healthy_update_backup(install) == 0
        assert backup.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows update helper")
def test_fault_after_both_renames_restores_old_program(tmp_path, monkeypatch) -> None:
    import ai_mini_monitor.updater as updater

    install = tmp_path / "Mini-Monitor"
    install.mkdir()
    (install / "Mini-Monitor.exe").write_bytes(b"old desktop exe")
    (install / "SHA256SUMS.txt").write_text(_digest(b"old desktop exe") + "  Mini-Monitor.exe\n", encoding="utf-8")
    new_exe = _compiled_test_executable(tmp_path, write_ack=True, version="0.2.0", lifetime_ms=1000)
    archive = tmp_path / "new.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("Mini-Monitor/Mini-Monitor.exe", new_exe)
    archive_data = archive.read_bytes()
    manifest = UpdateManifest(
        "0.2.0", "stable",
        "https://github.com/contentriumkorea/mini-monitor/releases/download/v0.2.0/Mini-Monitor.zip",
        _digest(archive_data), len(archive_data),
        (("Mini-Monitor/Mini-Monitor.exe", _digest(new_exe)),),
    )
    monkeypatch.setattr(updater, "_download_archive", lambda _url, target, _size: target.write_bytes(archive_data))
    prepared = stage_update(manifest, install_root=install, config_path=None)
    result = _run_helper(prepared, timeout_seconds=1,
                         extra_env={"MINI_MONITOR_UPDATE_TEST_FAIL_AFTER_SWAP": "1"})
    assert result.returncode == 3, result.stderr
    assert (install / "Mini-Monitor.exe").read_bytes() == b"old desktop exe"
    journal = json.loads(prepared.journal_path.read_text(encoding="utf-8"))
    assert journal["state"] == "rolled_back_start_failed"
    assert not Path(journal["backup_root"]).exists()
    assert list(tmp_path.glob(".mini-monitor-failed-*"))


@pytest.mark.skipif(os.name != "nt", reason="Windows update helper and file-sharing semantics")
def test_exited_new_app_with_live_child_lock_reports_failed_restore(tmp_path, monkeypatch) -> None:
    """A failed rollback must preserve both trees and request manual recovery."""
    import ai_mini_monitor.updater as updater

    install = tmp_path / "Mini-Monitor"
    install.mkdir()
    old_exe = b"old desktop exe"
    (install / "Mini-Monitor.exe").write_bytes(old_exe)
    (install / "SHA256SUMS.txt").write_text(
        f"{_digest(old_exe)}  Mini-Monitor.exe\n", encoding="utf-8",
    )
    source = tmp_path / "orphan-lock.cs"
    source.write_text(
        """using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
class Program {
    static int Main(string[] args) {
        var ack = Environment.GetEnvironmentVariable("MINI_MONITOR_UPDATE_ACK_PATH");
        var ready = ack + ".child-ready";
        var stop = ack + ".child-stop";
        if (args.Length == 1 && args[0] == "--hold-file") {
            var dll = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "_internal", "runtime.dll");
            using (File.Open(dll, FileMode.Open, FileAccess.Read, FileShare.Read)) {
                File.WriteAllText(ready, "locked");
                var deadline = DateTime.UtcNow.AddSeconds(8);
                while (!File.Exists(stop) && DateTime.UtcNow < deadline) Thread.Sleep(50);
            }
            File.WriteAllText(ack + ".child-done", "released");
            return 0;
        }
        var child = new ProcessStartInfo(Process.GetCurrentProcess().MainModule.FileName, "--hold-file");
        child.UseShellExecute = false;
        child.CreateNoWindow = true;
        Process.Start(child);
        var readyDeadline = DateTime.UtcNow.AddSeconds(5);
        while (!File.Exists(ready) && DateTime.UtcNow < readyDeadline) Thread.Sleep(20);
        return File.Exists(ready) ? 0 : 1;
    }
}
""",
        encoding="utf-8",
    )
    compiled = tmp_path / "orphan-lock.exe"
    compiler = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([str(compiler), "/nologo", f"/out:{compiled}", str(source)],
                   check=True, capture_output=True, text=True)
    new_exe = compiled.read_bytes()
    new_dll = b"new nested dll"
    archive = tmp_path / "new.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("Mini-Monitor/Mini-Monitor.exe", new_exe)
        package.writestr("Mini-Monitor/_internal/runtime.dll", new_dll)
    archive_data = archive.read_bytes()
    manifest = UpdateManifest(
        "0.2.0", "stable",
        "https://github.com/contentriumkorea/mini-monitor/releases/download/v0.2.0/Mini-Monitor.zip",
        _digest(archive_data), len(archive_data),
        (("Mini-Monitor/Mini-Monitor.exe", _digest(new_exe)),
         ("Mini-Monitor/_internal/runtime.dll", _digest(new_dll))),
    )
    monkeypatch.setattr(updater, "_download_archive", lambda _url, target, _size: target.write_bytes(archive_data))
    prepared = stage_update(manifest, install_root=install, config_path=None)
    ready = Path(str(prepared.helper_path.parent / "startup-ack.json") + ".child-ready")
    stop = Path(str(prepared.helper_path.parent / "startup-ack.json") + ".child-stop")
    done = Path(str(prepared.helper_path.parent / "startup-ack.json") + ".child-done")
    try:
        result = _run_helper(prepared, timeout_seconds=1)
        assert result.returncode == 5, result.stderr
        assert ready.is_file(), "child did not hold the installed file before parent exit"
        journal = json.loads(prepared.journal_path.read_text(encoding="utf-8"))
        assert journal["state"] == "needs_manual_recovery"
        backup = Path(journal["backup_root"])
        assert (backup / "Mini-Monitor.exe").read_bytes() == old_exe
        assert (install / "Mini-Monitor.exe").read_bytes() == new_exe
        assert (install / "_internal" / "runtime.dll").read_bytes() == new_dll
        assert not list(tmp_path.glob(".mini-monitor-failed-*"))
    finally:
        if ready.is_file():
            stop.write_text("release", encoding="utf-8")
            deadline = time.monotonic() + 5
            while not done.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert done.exists(), "fixture child did not release its file lock"


@pytest.mark.skipif(os.name != "nt", reason="Windows update helper")
@pytest.mark.parametrize("duration", ["0.5", "3600.0"])
def test_helper_parses_restart_duration_invariantly_under_german_locale(tmp_path, monkeypatch, duration) -> None:
    import ai_mini_monitor.updater as updater

    install = tmp_path / "Mini-Monitor"
    install.mkdir()
    (install / "Mini-Monitor.exe").write_bytes(b"old desktop exe")
    (install / "SHA256SUMS.txt").write_text(_digest(b"old desktop exe") + "  Mini-Monitor.exe\n", encoding="utf-8")
    new_exe = _compiled_test_executable(tmp_path, write_ack=True, version="0.2.0", lifetime_ms=1000)
    archive = tmp_path / "new.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("Mini-Monitor/Mini-Monitor.exe", new_exe)
    archive_data = archive.read_bytes()
    manifest = UpdateManifest(
        "0.2.0", "stable",
        "https://github.com/contentriumkorea/mini-monitor/releases/download/v0.2.0/Mini-Monitor.zip",
        _digest(archive_data), len(archive_data),
        (("Mini-Monitor/Mini-Monitor.exe", _digest(new_exe)),),
    )
    monkeypatch.setattr(updater, "_download_archive", lambda _url, target, _size: target.write_bytes(archive_data))
    prepared = stage_update(manifest, install_root=install, config_path=None)
    journal = json.loads(prepared.journal_path.read_text(encoding="utf-8"))
    journal["restart_args"] = ["--desktop-smoke", duration]
    prepared.journal_path.write_text(json.dumps(journal), encoding="utf-8")
    result = _run_helper(prepared, timeout_seconds=1, culture="de-DE")
    assert result.returncode == 0, result.stderr
    assert json.loads(prepared.journal_path.read_text(encoding="utf-8"))["state"] == "healthy_backup_retained"
    observed_args = (prepared.helper_path.parent / "startup-ack.json.args").read_text(encoding="utf-8")
    assert observed_args.endswith("|--desktop-smoke|" + ("0.5" if duration == "0.5" else "3600"))


@pytest.mark.skipif(os.name != "nt", reason="Windows update helper")
@pytest.mark.parametrize("bad", ["COM¹", "com².txt", "LPT³", "lpt¹.log"])
def test_helper_rejects_superscript_device_alias_before_swap(tmp_path, monkeypatch, bad) -> None:
    import ai_mini_monitor.updater as updater

    install = tmp_path / "Mini-Monitor"
    install.mkdir()
    (install / "Mini-Monitor.exe").write_bytes(b"old desktop exe")
    (install / "SHA256SUMS.txt").write_text(_digest(b"old desktop exe") + "  Mini-Monitor.exe\n", encoding="utf-8")
    new_exe = b"not a Windows executable"
    archive = tmp_path / "new.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("Mini-Monitor/Mini-Monitor.exe", new_exe)
    archive_data = archive.read_bytes()
    manifest = UpdateManifest(
        "0.2.0", "stable",
        "https://github.com/contentriumkorea/mini-monitor/releases/download/v0.2.0/Mini-Monitor.zip",
        _digest(archive_data), len(archive_data),
        (("Mini-Monitor/Mini-Monitor.exe", _digest(new_exe)),),
    )
    monkeypatch.setattr(updater, "_download_archive", lambda _url, target, _size: target.write_bytes(archive_data))
    prepared = stage_update(manifest, install_root=install, config_path=None)
    journal = json.loads(prepared.journal_path.read_text(encoding="utf-8"))
    journal["new_files"].append(["Mini-Monitor/" + bad, "0" * 64])
    prepared.journal_path.write_text(json.dumps(journal), encoding="utf-8")
    result = _run_helper(prepared, timeout_seconds=1)
    assert result.returncode != 0
    assert "Unsafe update path component" in result.stderr
    assert (install / "Mini-Monitor.exe").read_bytes() == b"old desktop exe"
    assert not Path(journal["backup_root"]).exists()
