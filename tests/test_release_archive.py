"""Release ZIP is one verified onedir tree with a functional asset alias."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from package_release import package_release  # noqa: E402


def _artifact(root: Path) -> Path:
    artifact = root / "Mini-Monitor"
    files = {
        "Mini-Monitor.exe": b"desktop",
        "Mini-Monitor-CLI.exe": b"console",
        "BUILD-INFO.json": json.dumps({"schema": 1, "input_fingerprint": "a" * 64}).encode(),
        "_internal/assets/update-public-key.pem": b"public-key",
        "_internal/scripts/Apply-Update.ps1": b"helper",
        "source/assets/update-public-key.pem": b"public-key",
        "source/scripts/Apply-Update.ps1": b"helper",
    }
    for relative, contents in files.items():
        path = artifact / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    lines = [f"{hashlib.sha256(data).hexdigest()}  {name}" for name, data in sorted(files.items())]
    (artifact / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return artifact


def test_package_release_makes_identical_alias_and_dated_delivery(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    published, delivery = package_release(artifact, tmp_path / "out", "2026-10-04 12'34")

    assert published.name == "Mini-Monitor.zip"
    assert delivery.name == "2026-10-04 12'34 Mini Monitor.zip"
    assert published.read_bytes() == delivery.read_bytes()
    with zipfile.ZipFile(published) as archive:
        names = set(archive.namelist())
        assert names == {f"Mini-Monitor/{path.relative_to(artifact).as_posix()}" for path in artifact.rglob("*") if path.is_file()}
        assert archive.read("Mini-Monitor/Mini-Monitor.exe") == b"desktop"


def test_package_release_rejects_changed_artifact_file(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    (artifact / "Mini-Monitor.exe").write_bytes(b"tampered")

    with pytest.raises(ValueError, match="inventory"):
        package_release(artifact, tmp_path / "out", "2026-10-04 12'34")


def test_package_release_rejects_unlisted_or_sensitive_file(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    (artifact / "auth.json").write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError, match="inventory|sensitive"):
        package_release(artifact, tmp_path / "out", "2026-10-04 12'34")


def test_release_cli_help_needs_no_iana_timezone_database() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/package_release.py"), "--help"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--output-dir" in result.stdout


def test_archive_failure_never_removes_racing_delivery_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = _artifact(tmp_path)
    delivery = tmp_path / "out" / "2026-10-04 12'34 Mini Monitor.zip"
    original_open = Path.open

    def racing_open(path: Path, mode: str = "r", *args: object, **kwargs: object):
        if path == delivery and mode == "xb":
            with original_open(path, "xb") as other_writer:
                other_writer.write(b"other user's file")
            raise FileExistsError("another writer created delivery file")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", racing_open)
    with pytest.raises(FileExistsError):
        package_release(artifact, tmp_path / "out", "2026-10-04 12'34")
    assert delivery.read_bytes() == b"other user's file"
