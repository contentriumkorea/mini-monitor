from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ai_mini_monitor.updater import (
    API_URL,
    UpdateManifest,
    UpdateService,
    acknowledge_update_startup,
    resolve_update_config,
    stage_update,
    verify_release_manifest,
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@pytest.fixture
def signed_release():
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    document = {
        "schema_version": 1,
        "version": "0.2.0",
        "channel": "stable",
        "archive_url": "https://github.com/contentriumkorea/mini-monitor/releases/download/v0.2.0/Mini-Monitor.zip",
        "archive_sha256": "a" * 64,
        "archive_size": 123,
        "files": [["Mini-Monitor/Mini-Monitor.exe", "b" * 64]],
    }
    payload = _canonical(document)
    return payload, key.sign(payload), public


def test_valid_signed_release_manifest_is_parsed(signed_release) -> None:
    payload, signature, public_key = signed_release
    result = verify_release_manifest(payload, signature, public_key)
    assert result.version == "0.2.0"
    assert result.files == (("Mini-Monitor/Mini-Monitor.exe", "b" * 64),)


def test_tampered_manifest_is_rejected(signed_release) -> None:
    payload, signature, public_key = signed_release
    with pytest.raises(ValueError):
        verify_release_manifest(payload + b" ", signature, public_key)


def test_duplicate_keys_and_noncanonical_json_are_rejected(signed_release) -> None:
    payload, signature, public_key = signed_release
    duplicate = payload.replace(b'"version":"0.2.0"', b'"version":"0.2.0","version":"0.3.0"')
    with pytest.raises(ValueError):
        verify_release_manifest(duplicate, signature, public_key)
    with pytest.raises(ValueError):
        verify_release_manifest(payload + b"\n", signature, public_key)


@pytest.mark.parametrize("bad", ["../x", "C:/x", "Mini-Monitor/a:stream", "Mini-Monitor/CON", "Mini-Monitor/a./x", "Mini-Monitor/a\\x", "Mini-Monitor/COM¹", "Mini-Monitor/com².txt", "Mini-Monitor/LPT³", "Mini-Monitor/lpt¹.log"])
def test_release_manifest_rejects_windows_alias_paths(signed_release, bad: str) -> None:
    _payload, _signature, public = signed_release
    key = Ed25519PrivateKey.generate()
    document = {
        "schema_version": 1,
        "version": "0.2.0",
        "channel": "stable",
        "archive_url": "https://github.com/contentriumkorea/mini-monitor/releases/download/v0.2.0/Mini-Monitor.zip",
        "archive_sha256": "a" * 64,
        "archive_size": 123,
        "files": [[bad, "b" * 64]],
    }
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    matching_public = serialization.load_pem_private_key(private, password=None).public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert matching_public != public
    payload = _canonical(document)
    with pytest.raises(ValueError):
        verify_release_manifest(payload, key.sign(payload), matching_public)


def test_release_manifest_rejects_case_aliases() -> None:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    document = {
        "schema_version": 1, "version": "0.2.0", "channel": "stable",
        "archive_url": "https://github.com/contentriumkorea/mini-monitor/releases/download/v0.2.0/Mini-Monitor.zip",
        "archive_sha256": "a" * 64, "archive_size": 123,
        "files": [["Mini-Monitor/a", "b" * 64], ["Mini-Monitor/A", "c" * 64]],
    }
    payload = _canonical(document)
    with pytest.raises(ValueError):
        verify_release_manifest(payload, key.sign(payload), public)


def _old_install(root: Path, *, extra: bool = False) -> None:
    root.mkdir()
    (root / "Mini-Monitor.exe").write_bytes(b"old exe")
    if extra:
        (root / "notes.txt").write_text("keep me", encoding="utf-8")
    (root / "SHA256SUMS.txt").write_text(
        _digest(b"old exe") + "  Mini-Monitor.exe\n", encoding="utf-8"
    )


def _new_archive(path: Path, *, member: str = "Mini-Monitor/Mini-Monitor.exe") -> tuple[bytes, UpdateManifest]:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member, b"new exe")
    data = path.read_bytes()
    manifest = UpdateManifest(
        "0.2.0", "stable",
        "https://github.com/contentriumkorea/mini-monitor/releases/download/v0.2.0/Mini-Monitor.zip",
        _digest(data), len(data), ((member, _digest(b"new exe")),),
    )
    return data, manifest


def test_stage_rejects_unknown_install_file_without_changing_old_tree(tmp_path, monkeypatch) -> None:
    import ai_mini_monitor.updater as updater
    root = tmp_path / "Mini-Monitor"
    _old_install(root, extra=True)
    archive_data, manifest = _new_archive(tmp_path / "new.zip")
    monkeypatch.setattr(updater, "_download_archive", lambda _url, target, _size: target.write_bytes(archive_data))
    with pytest.raises(ValueError, match="unknown|changed"):
        stage_update(manifest, install_root=root, config_path=None)
    assert (root / "Mini-Monitor.exe").read_bytes() == b"old exe"
    assert (root / "notes.txt").read_text(encoding="utf-8") == "keep me"


def test_stage_valid_archive_prepares_sibling_without_changing_install(tmp_path, monkeypatch) -> None:
    import ai_mini_monitor.updater as updater
    root = tmp_path / "Mini-Monitor"
    _old_install(root)
    archive_data, manifest = _new_archive(tmp_path / "new.zip")
    monkeypatch.setattr(updater, "_download_archive", lambda _url, target, _size: target.write_bytes(archive_data))
    prepared = stage_update(manifest, install_root=root, config_path=None)
    assert prepared.install_root == root
    assert prepared.staged_root.parent.parent == root.parent
    assert (prepared.staged_root / "Mini-Monitor.exe").read_bytes() == b"new exe"
    assert prepared.helper_path.is_file()
    assert prepared.journal_path.is_file()
    assert (root / "Mini-Monitor.exe").read_bytes() == b"old exe"


def test_stage_rejects_config_inside_install_tree(tmp_path, monkeypatch) -> None:
    import ai_mini_monitor.updater as updater
    root = tmp_path / "Mini-Monitor"
    _old_install(root)
    archive_data, manifest = _new_archive(tmp_path / "new.zip")
    monkeypatch.setattr(updater, "_download_archive", lambda _url, target, _size: target.write_bytes(archive_data))
    with pytest.raises(ValueError, match="config"):
        stage_update(manifest, install_root=root, config_path=root / "config.json")
    assert (root / "Mini-Monitor.exe").read_bytes() == b"old exe"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction fixture")
def test_stage_rejects_install_through_parent_junction_before_resolution(tmp_path, monkeypatch) -> None:
    import ai_mini_monitor.updater as updater
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    root = real_parent / "Mini-Monitor"
    _old_install(root)
    junction = tmp_path / "linked-parent"
    subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(junction), str(real_parent)],
                   check=True, capture_output=True, text=True)
    archive_data, manifest = _new_archive(tmp_path / "new.zip")
    monkeypatch.setattr(updater, "_download_archive", lambda _url, target, _size: target.write_bytes(archive_data))
    with pytest.raises(ValueError, match="reparse|junction"):
        stage_update(manifest, install_root=junction / "Mini-Monitor", config_path=None)
    assert (root / "Mini-Monitor.exe").read_bytes() == b"old exe"
    assert not list(real_parent.glob(".mini-monitor-update-*"))


@pytest.mark.parametrize("member", ["Mini-Monitor/../evil", "Mini-Monitor/CON", "Mini-Monitor/a:stream"])
def test_stage_rejects_malicious_archive_members(tmp_path, monkeypatch, member: str) -> None:
    import ai_mini_monitor.updater as updater
    root = tmp_path / "Mini-Monitor"
    _old_install(root)
    archive_data, manifest = _new_archive(tmp_path / "new.zip", member=member)
    monkeypatch.setattr(updater, "_download_archive", lambda _url, target, _size: target.write_bytes(archive_data))
    with pytest.raises(ValueError):
        stage_update(manifest, install_root=root, config_path=None)
    assert (root / "Mini-Monitor.exe").read_bytes() == b"old exe"


def _eventually(predicate, *, timeout: float = 2.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("updater state did not arrive")


def test_service_checks_stable_release_once_per_version_and_persists_notice(tmp_path, monkeypatch, signed_release) -> None:
    import ai_mini_monitor.updater as updater
    payload, signature, public_key = signed_release
    key_path = tmp_path / "public.pem"
    key_path.write_bytes(public_key)
    monkeypatch.setattr(updater, "resource_path", lambda _name: key_path)
    asset_base = "https://github.com/contentriumkorea/mini-monitor/releases/download/v0.2.0/"
    release = {
        "tag_name": "v0.2.0", "draft": False, "prerelease": False,
        "assets": [
            {"name": "update-manifest.json", "browser_download_url": asset_base + "update-manifest.json"},
            {"name": "update-manifest.sig", "browser_download_url": asset_base + "update-manifest.sig"},
        ],
    }
    responses = {API_URL: json.dumps(release).encode(), asset_base + "update-manifest.json": payload,
                 asset_base + "update-manifest.sig": signature}
    fetcher = lambda url, _limit: responses[url]
    state_path = tmp_path / "state.json"
    service = UpdateService(current_version="0.1.0", install_root=tmp_path / "Mini-Monitor",
                            config_path=None, _fetcher=fetcher, _state_path=state_path, _clock=lambda: 1000.0)
    try:
        assert service.check()
        _eventually(lambda: service.snapshot().state == "available")
        assert service.snapshot().notification_pending
        service.dismiss()
        assert not service.snapshot().notification_pending
        assert not service.check()
        assert service.check(force=True)
        _eventually(lambda: service.snapshot().state == "available")
        assert not service.snapshot().notification_pending
    finally:
        service.close()
    second = UpdateService(current_version="0.1.0", install_root=tmp_path / "Mini-Monitor",
                           config_path=None, _fetcher=fetcher, _state_path=state_path, _clock=lambda: 1001.0)
    try:
        assert not second.check()
        assert second.check(force=True)
        _eventually(lambda: second.snapshot().state == "available")
        assert not second.snapshot().notification_pending
    finally:
        second.close()


def test_discovery_without_delivery_retries_after_restart(tmp_path, monkeypatch, signed_release) -> None:
    import ai_mini_monitor.updater as updater
    payload, signature, public_key = signed_release
    key_path = tmp_path / "public.pem"
    key_path.write_bytes(public_key)
    monkeypatch.setattr(updater, "resource_path", lambda _name: key_path)
    asset_base = "https://github.com/contentriumkorea/mini-monitor/releases/download/v0.2.0/"
    release = {
        "tag_name": "v0.2.0", "draft": False, "prerelease": False,
        "assets": [
            {"name": "update-manifest.json", "browser_download_url": asset_base + "update-manifest.json"},
            {"name": "update-manifest.sig", "browser_download_url": asset_base + "update-manifest.sig"},
        ],
    }
    responses = {API_URL: json.dumps(release).encode(), asset_base + "update-manifest.json": payload,
                 asset_base + "update-manifest.sig": signature}
    state_path = tmp_path / "state.json"
    make_service = lambda: UpdateService(
        current_version="0.1.0", install_root=tmp_path / "Mini-Monitor", config_path=None,
        _fetcher=lambda url, _limit: responses[url], _state_path=state_path, _clock=lambda: 1000.0,
    )
    first = make_service()
    try:
        assert first.check()
        _eventually(lambda: first.snapshot().state == "available")
        assert first.snapshot().notification_pending
    finally:
        first.close()
    second = make_service()
    try:
        assert second.check()  # Undelivered discovery gets one restart recheck.
        _eventually(lambda: second.snapshot().state == "available")
        assert second.snapshot().notification_pending
        assert not second.check()  # The startup bypass is consumed.
        assert not second.mark_notification_delivered("0.3.0")
        assert second.snapshot().notification_pending
        assert second.mark_notification_delivered("0.2.0")
        assert not second.snapshot().notification_pending
    finally:
        second.close()
    third = make_service()
    try:
        assert not third.check()  # Delivered notices keep the normal cooldown.
    finally:
        third.close()


def test_failed_pending_notice_recheck_does_not_retry_until_next_restart(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "last_check": 1000.0, "delivered_versions": [], "pending_version": "0.2.0",
    }), encoding="utf-8")
    calls = []

    def failing_fetcher(url, _limit):
        calls.append(url)
        raise OSError("offline")

    service = UpdateService(current_version="0.1.0", install_root=None, config_path=None,
                            _fetcher=failing_fetcher, _state_path=state_path, _clock=lambda: 1000.0)
    try:
        assert service.check()
        _eventually(lambda: service.snapshot().state == "error")
        assert not service.check()
        assert calls == [API_URL]
        assert json.loads(state_path.read_text(encoding="utf-8"))["pending_version"] == "0.2.0"
    finally:
        service.close()


@pytest.mark.parametrize("pending, delivered", [
    ("not-a-version", []), ("0.1.0", []), ("0.0.9", []), ("0.2.0", ["0.2.0"]),
])
def test_invalid_or_delivered_persisted_pending_notice_keeps_cooldown(tmp_path, pending, delivered) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "last_check": 1000.0, "delivered_versions": delivered, "pending_version": pending,
    }), encoding="utf-8")
    calls = []
    service = UpdateService(current_version="0.1.0", install_root=None, config_path=None,
                            _fetcher=lambda url, _limit: calls.append(url),
                            _state_path=state_path, _clock=lambda: 1000.0)
    try:
        assert not service.check()
        assert calls == []
    finally:
        service.close()


def test_pending_notice_is_cleared_when_latest_is_no_longer_newer(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "last_check": 1000.0, "delivered_versions": [], "pending_version": "0.2.0",
    }), encoding="utf-8")
    release = {"tag_name": "v0.1.0", "draft": False, "prerelease": False}
    service = UpdateService(current_version="0.1.0", install_root=None, config_path=None,
                            _fetcher=lambda _url, _limit: json.dumps(release).encode(),
                            _state_path=state_path, _clock=lambda: 1000.0)
    try:
        assert service.check()
        _eventually(lambda: service.snapshot().message == "최신 버전")
        _eventually(lambda: json.loads(state_path.read_text(encoding="utf-8"))["pending_version"] is None)
        assert not service.check()
    finally:
        service.close()


def test_update_ack_rejects_wrong_runtime_version_and_unowned_environment(tmp_path, monkeypatch) -> None:
    import ai_mini_monitor.updater as updater
    root = tmp_path / "Mini-Monitor"
    root.mkdir()
    executable = root / "Mini-Monitor.exe"
    executable.write_bytes(b"test exe")
    stage = tmp_path / ".mini-monitor-update-test"
    stage.mkdir()
    backup = tmp_path / ".mini-monitor-backup-test"
    backup.mkdir()
    ack = stage / "startup-ack.json"
    config = tmp_path / "custom.json"
    config.write_text("{}", encoding="utf-8")
    record = {
        "state": "new_installed", "nonce": "a" * 32, "version": "0.2.0",
        "install_root": str(root), "backup_root": str(backup), "ack_path": str(ack),
        "config_path": str(config),
    }
    (stage / "update-journal.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater.sys, "executable", str(executable))
    monkeypatch.setenv("MINI_MONITOR_UPDATE_ACK_PATH", str(ack))
    monkeypatch.setenv("MINI_MONITOR_UPDATE_ACK_NONCE", "a" * 32)
    monkeypatch.setenv("MINI_MONITOR_UPDATE_CONFIG_PATH", str(config))
    assert resolve_update_config() == config
    assert not acknowledge_update_startup(current_version="0.1.0")
    assert not ack.exists()
    assert acknowledge_update_startup(current_version="0.2.0")
    assert json.loads(ack.read_text(encoding="utf-8")) == {"nonce": "a" * 32, "ready": True, "version": "0.2.0"}
    monkeypatch.setenv("MINI_MONITOR_UPDATE_ACK_NONCE", "b" * 32)
    assert resolve_update_config() is None
    assert not acknowledge_update_startup(current_version="0.2.0")


def test_signer_creates_verifiable_manifest_from_archive(tmp_path, monkeypatch) -> None:
    from scripts import sign_release

    key_path = tmp_path / "signing" / "private.dpapi"
    public_path = tmp_path / "public.pem"
    monkeypatch.setattr(sign_release, "_protect", lambda value: b"protected:" + value)
    monkeypatch.setattr(sign_release, "_unprotect", lambda value: value.removeprefix(b"protected:"))
    sign_release.generate_key(key_path, public_path)
    old_public = public_path.read_bytes()
    with pytest.raises(FileExistsError):
        sign_release.generate_key(key_path, public_path)
    assert public_path.read_bytes() == old_public
    archive = tmp_path / "Mini-Monitor.zip"
    exe = b"test frozen exe"
    inventory = f"{_digest(exe)}  Mini-Monitor.exe\n".encode()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("Mini-Monitor/Mini-Monitor.exe", exe)
        package.writestr("Mini-Monitor/SHA256SUMS.txt", inventory)
    output = tmp_path / "release"
    manifest_path, signature_path = sign_release.sign_release(
        archive, "0.2.0",
        "https://github.com/contentriumkorea/mini-monitor/releases/download/v0.2.0/Mini-Monitor.zip",
        key_path, output, public_path,
    )
    parsed = verify_release_manifest(manifest_path.read_bytes(), signature_path.read_bytes(), old_public)
    assert parsed.version == "0.2.0"
    assert dict(parsed.files)["Mini-Monitor/Mini-Monitor.exe"] == _digest(exe)


def test_signer_rejects_modified_inventory(tmp_path, monkeypatch) -> None:
    from scripts import sign_release

    key_path = tmp_path / "private.dpapi"
    public_path = tmp_path / "public.pem"
    monkeypatch.setattr(sign_release, "_protect", lambda value: b"protected:" + value)
    monkeypatch.setattr(sign_release, "_unprotect", lambda value: value.removeprefix(b"protected:"))
    sign_release.generate_key(key_path, public_path)
    archive = tmp_path / "Mini-Monitor.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("Mini-Monitor/Mini-Monitor.exe", b"exe")
        package.writestr("Mini-Monitor/SHA256SUMS.txt", b"0" * 64 + b"  Mini-Monitor.exe\n")
    with pytest.raises(ValueError, match="inventory"):
        sign_release.sign_release(
            archive, "0.2.0",
            "https://github.com/contentriumkorea/mini-monitor/releases/download/v0.2.0/Mini-Monitor.zip",
            key_path, tmp_path / "release", public_path,
        )
