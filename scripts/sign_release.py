"""Create a canonical, Ed25519-signed Mini Monitor release manifest.

The private key is DPAPI-protected for the current Windows user and must live
outside this repository. Never print or distribute the private key file.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import re
import stat
import sys
import zipfile
from ctypes import wintypes
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ai_mini_monitor.updater import (
    MANIFEST_ASSET, SIGNATURE_ASSET, MAX_FILES, MAX_UNPACKED, _canonical, _sha256_file,
    _validate_member, _validate_release_asset_url, _version_tuple,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_KEY_PATH = PROJECT_ROOT / "assets/update-public-key.pem"
DEFAULT_KEY = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AI-Mini-Monitor/update-signing/ed25519-private.dpapi"
_ENTROPY = b"Mini-Monitor:release-signing:v1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _crypt(data: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise OSError("release signing key requires Windows current-user DPAPI")
    source, source_buffer = _blob(data)
    entropy, entropy_buffer = _blob(_ENTROPY)
    output = _Blob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if decrypt:
        description = wintypes.LPWSTR()
        success = crypt32.CryptUnprotectData(
            ctypes.byref(source), ctypes.byref(description), ctypes.byref(entropy),
            None, None, 1, ctypes.byref(output),
        )
    else:
        description = None
        success = crypt32.CryptProtectData(
            ctypes.byref(source), "Mini Monitor release signing key", ctypes.byref(entropy),
            None, None, 1, ctypes.byref(output),
        )
    if not success:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        if decrypt and description:
            kernel32.LocalFree(description)
        _ = (source_buffer, entropy_buffer)


def _protect(data: bytes) -> bytes:
    return _crypt(data, decrypt=False)


def _unprotect(data: bytes) -> bytes:
    return _crypt(data, decrypt=True)


def _outside_project(path: Path) -> None:
    try:
        path.resolve(strict=False).relative_to(PROJECT_ROOT)
    except ValueError:
        return
    raise ValueError("signing private key must be stored outside the repository")


def generate_key(key_path: Path, public_path: Path = PUBLIC_KEY_PATH) -> str:
    """Generate one non-overwriting current-user key and return public fingerprint."""

    _outside_project(key_path)
    if key_path.exists() or public_path.exists():
        raise FileExistsError("signing key or pinned public key already exists")
    private = Ed25519PrivateKey.generate()
    raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    protected = _protect(raw)
    public = private.public_key().public_bytes(serialization.Encoding.PEM,
                                                serialization.PublicFormat.SubjectPublicKeyInfo)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    with key_path.open("xb") as stream:
        stream.write(protected)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.chmod(key_path, 0o600)
        with public_path.open("xb") as stream:
            stream.write(public)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        key_path.unlink(missing_ok=True)
        raise
    return hashlib.sha256(public).hexdigest()


def _scan_archive(path: Path) -> tuple[tuple[str, str], ...]:
    found: dict[str, tuple[str, str]] = {}
    unpacked = 0
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES * 2:
            raise ValueError("archive has too many entries")
        for info in infos:
            name = info.filename.rstrip("/")
            key = _validate_member(name)
            if key in found:
                raise ValueError("archive has Windows path aliases")
            kind = (info.external_attr >> 16) & 0o170000
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or info.external_attr & 0x400:
                raise ValueError("archive has link or reparse point")
            if info.is_dir():
                found[key] = (name, "")
                continue
            unpacked += info.file_size
            if unpacked > MAX_UNPACKED:
                raise ValueError("archive is too large when unpacked")
            digest = hashlib.sha256()
            count = 0
            with archive.open(info) as stream:
                while chunk := stream.read(1024 * 1024):
                    count += len(chunk)
                    if count > info.file_size:
                        raise ValueError("archive member exceeded declared size")
                    digest.update(chunk)
            if count != info.file_size:
                raise ValueError("archive member size mismatch")
            found[key] = (name, digest.hexdigest())
        files = {key: value for key, value in found.items() if value[1]}
        inventory_key = "mini-monitor/sha256sums.txt"
        executable_key = "mini-monitor/mini-monitor.exe"
        if inventory_key not in files or executable_key not in files:
            raise ValueError("archive is missing executable or inventory")
        inventory = archive.read(files[inventory_key][0]).decode("utf-8")
        expected: dict[str, str] = {}
        for line in inventory.splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
            if match is None:
                raise ValueError("archive inventory is malformed")
            digest, relative = match.groups()
            key = _validate_member("Mini-Monitor/" + relative)
            if key in expected:
                raise ValueError("archive inventory has duplicate aliases")
            expected[key] = digest
        actual = {key: value[1] for key, value in files.items() if key != inventory_key}
        if expected != actual:
            raise ValueError("archive inventory differs from archive files")
    return tuple(sorted((value for value in files.values()), key=lambda row: row[0]))


def sign_release(
    archive_path: Path, version: str, asset_url: str, key_path: Path,
    output_dir: Path, public_path: Path = PUBLIC_KEY_PATH,
) -> tuple[Path, Path]:
    _version_tuple(version)
    _validate_release_asset_url(asset_url, version=version)
    if not asset_url.endswith("/Mini-Monitor.zip"):
        raise ValueError("release archive asset must be Mini-Monitor.zip")
    _outside_project(key_path)
    if not archive_path.is_file() or archive_path.stat().st_size > 1024**3:
        raise ValueError("release archive is missing or too large")
    files = _scan_archive(archive_path)
    raw = _unprotect(key_path.read_bytes())
    private = Ed25519PrivateKey.from_private_bytes(raw)
    public = private.public_key().public_bytes(serialization.Encoding.PEM,
                                                serialization.PublicFormat.SubjectPublicKeyInfo)
    if public != public_path.read_bytes():
        raise ValueError("signing key does not match pinned public key")
    document = {
        "schema_version": 1, "version": version, "channel": "stable",
        "archive_url": asset_url, "archive_sha256": _sha256_file(archive_path),
        "archive_size": archive_path.stat().st_size,
        "files": [list(row) for row in files],
    }
    payload = _canonical(document)
    signature = private.sign(payload)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = output_dir / MANIFEST_ASSET
    signature_file = output_dir / SIGNATURE_ASSET
    if manifest.exists() or signature_file.exists():
        raise FileExistsError("signed release metadata already exists")
    with manifest.open("xb") as stream:
        stream.write(payload)
    with signature_file.open("xb") as stream:
        stream.write(signature)
    return manifest, signature_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate-key", action="store_true", help="generate one DPAPI-protected key and pinned public PEM")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--asset-url")
    parser.add_argument("--key", type=Path, default=DEFAULT_KEY)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    if args.generate_key:
        if any((args.archive, args.version, args.asset_url, args.output_dir)):
            parser.error("--generate-key cannot be combined with release signing arguments")
        print("Public key SHA-256:", generate_key(args.key))
        return 0
    if not all((args.archive, args.version, args.asset_url, args.output_dir)):
        parser.error("--archive, --version, --asset-url, and --output-dir are required")
    sign_release(args.archive, args.version, args.asset_url, args.key, args.output_dir)
    print("Signed release metadata created; private key was not exported.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
