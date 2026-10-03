from __future__ import annotations

import hashlib
import base64
import json
import os
import subprocess
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
