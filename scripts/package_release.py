"""Assemble one verified onedir ZIP and a byte-identical dated delivery copy."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "Mini-Monitor.exe",
    "Mini-Monitor-CLI.exe",
    "BUILD-INFO.json",
    "_internal/assets/update-public-key.pem",
    "_internal/scripts/Apply-Update.ps1",
    "source/assets/update-public-key.pem",
    "source/scripts/Apply-Update.ps1",
}
FORBIDDEN_PARTS = {
    ".venv", "__pycache__", "codex-home", "diagnostics", "baseline",
    "logs", "secrets", "auth.json", "config.json", ".env", "update-signing",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inventory(artifact: Path) -> dict[str, str]:
    manifest = artifact / "SHA256SUMS.txt"
    if not manifest.is_file():
        raise ValueError("artifact inventory is missing")
    expected: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise ValueError("artifact inventory is malformed")
        digest, relative = match.groups()
        if relative in expected:
            raise ValueError("artifact inventory has duplicates")
        expected[relative] = digest
    return expected


def _files(artifact: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in artifact.rglob("*"):
        relative = path.relative_to(artifact).as_posix()
        if any(part.casefold() in FORBIDDEN_PARTS for part in path.relative_to(artifact).parts):
            raise ValueError(f"sensitive artifact path: {relative}")
        if path.suffix.casefold() in {".dpapi", ".key", ".pfx", ".p12", ".pyc"}:
            raise ValueError(f"sensitive artifact file: {relative}")
        details = path.lstat()
        if path.is_symlink() or getattr(details, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"artifact contains reparse path: {relative}")
        if path.is_dir():
            continue
        if not stat.S_ISREG(details.st_mode):
            raise ValueError(f"artifact contains non-file: {relative}")
        found[relative] = path
    return found


def package_release(artifact: Path, output_dir: Path, timestamp: str) -> tuple[Path, Path]:
    if artifact.name != "Mini-Monitor" or not artifact.is_dir():
        raise ValueError("release artifact must be a Mini-Monitor onedir folder")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}'\d{2}", timestamp):
        raise ValueError("delivery timestamp must be YYYY-MM-DD HH'mm")
    datetime.strptime(timestamp, "%Y-%m-%d %H'%M")
    files = _files(artifact)
    expected = _inventory(artifact)
    if REQUIRED - set(files):
        raise ValueError(f"required release files missing: {sorted(REQUIRED - set(files))}")
    if set(files) - {"SHA256SUMS.txt"} != set(expected):
        raise ValueError("artifact file set differs from inventory")
    for relative, digest in expected.items():
        if _sha256(files[relative]) != digest:
            raise ValueError(f"artifact inventory hash mismatch: {relative}")
    build_info = json.loads(files["BUILD-INFO.json"].read_text(encoding="utf-8"))
    if build_info.get("schema") != 1 or not re.fullmatch(
        r"[0-9a-f]{64}", str(build_info.get("input_fingerprint", ""))
    ):
        raise ValueError("build information is invalid")

    output_dir.mkdir(parents=True, exist_ok=True)
    published = output_dir / "Mini-Monitor.zip"
    delivery = output_dir / f"{timestamp} Mini Monitor.zip"
    if published.exists() or delivery.exists():
        raise FileExistsError("release archive already exists; use a fresh output directory")
    created_published = False
    created_delivery = False
    try:
        with zipfile.ZipFile(published, mode="x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            created_published = True
            for relative, path in sorted(files.items()):
                archive.write(path, f"Mini-Monitor/{relative}")
        with published.open("rb") as source:
            with delivery.open("xb") as target:
                created_delivery = True
                shutil.copyfileobj(source, target, length=1024 * 1024)
        if _sha256(published) != _sha256(delivery):
            raise ValueError("functional asset and dated delivery differ")
    except Exception:
        if created_published:
            published.unlink(missing_ok=True)
        if created_delivery:
            delivery.unlink(missing_ok=True)
        raise
    return published, delivery


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, default=ROOT / "dist/Mini-Monitor")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build/release")
    kst = timezone(timedelta(hours=9))
    parser.add_argument("--timestamp", default=datetime.now(kst).strftime("%Y-%m-%d %H'%M"))
    arguments = parser.parse_args()
    published, delivery = package_release(arguments.artifact, arguments.output_dir, arguments.timestamp)
    digest = _sha256(published)
    print(f"Published asset: {published}")
    print(f"Dated delivery: {delivery}")
    print(f"Matching SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
