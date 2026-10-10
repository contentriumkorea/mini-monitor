"""Signed portable release updates for Mini Monitor."""

from __future__ import annotations

import hashlib
import base64
import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .resources import resource_path, user_data_dir
from .security.windows_system import pin_powershell_modules, windows_powershell_paths
from .security.windows_recycle import recycle_directory as _recycle_directory


REPO = "contentriumkorea/mini-monitor"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
# A separate asset namespace keeps pre-0.2.7 clients on manual download.
# They would otherwise use their unsafe bundled helper, even for a new ZIP.
# Never publish legacy-name aliases for v2 releases.
MANIFEST_ASSET = "update-v2-manifest.json"
SIGNATURE_ASSET = "update-v2-manifest.sig"
PUBLIC_KEY_RESOURCE = "assets/update-public-key.pem"
HELPER_RESOURCE = "scripts/Apply-Update.ps1"
MAX_METADATA = 1024 * 1024
MAX_ARCHIVE = 1024**3
MAX_UNPACKED = 3 * 1024**3
MAX_FILES = 20_000
REQUEST_TIMEOUT = 15
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
_BAD_COMPONENT = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|(?:COM|LPT)(?:[1-9]|[¹²³]))(?:\..*)?\Z", re.I)
_REDIRECT_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
_CHECK_INTERVAL = 86_400.0
_UPDATE_MAINTENANCE_LOCK = threading.Lock()
_UPDATE_HANDOFF = threading.Event()


@dataclass(frozen=True, slots=True)
class UpdateManifest:
    version: str
    channel: str
    archive_url: str
    archive_sha256: str
    archive_size: int
    files: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class PreparedUpdate:
    install_root: Path
    staged_root: Path
    journal_path: Path
    helper_path: Path


@dataclass(frozen=True, slots=True)
class UpdateSnapshot:
    state: str = "idle"
    version: str | None = None
    release_url: str | None = None
    message: str = ""
    prepared: PreparedUpdate | None = None
    notification_pending: bool = False


def verify_release_manifest(payload: bytes, signature: bytes, public_key: bytes) -> UpdateManifest:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_METADATA:
        raise ValueError("manifest size is invalid")
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise ValueError("Ed25519 signature must be 64 bytes")
    try:
        key = serialization.load_pem_public_key(public_key)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("public key is not Ed25519")
        key.verify(signature, payload)
    except (InvalidSignature, TypeError, ValueError) as error:
        raise ValueError("release manifest signature is invalid") from error
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("release manifest is not valid UTF-8 JSON") from error
    if _canonical(document) != payload:
        raise ValueError("release manifest is not canonical JSON")
    return _parse_manifest(document)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError("stable release version must be major.minor.patch")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _validate_release_asset_url(url: str, *, version: str | None = None) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment:
        raise ValueError("release asset URL must be HTTPS on github.com")
    prefix = f"/{REPO}/releases/download/"
    if not parsed.path.startswith(prefix):
        raise ValueError("release asset is outside approved repository")
    suffix = parsed.path[len(prefix):].split("/")
    if len(suffix) != 2 or not suffix[0] or not suffix[1] or "/" in suffix[1]:
        raise ValueError("release asset URL shape is invalid")
    if version is not None and suffix[0] != f"v{version}":
        raise ValueError("release asset tag does not match signed version")


def _validate_member(path: str) -> str:
    if not isinstance(path, str) or not path or "\\" in path or path.startswith("/"):
        raise ValueError("invalid archive path")
    parts = path.split("/")
    if parts[0] != "Mini-Monitor" or len(parts) < 2:
        raise ValueError("unexpected archive root")
    for part in parts:
        if (not part or part in (".", "..") or part[-1] in (" ", ".") or "~" in part
                or _BAD_COMPONENT.search(part) or _DEVICE.fullmatch(part)
                or unicodedata.normalize("NFC", part) != part):
            raise ValueError(f"unsafe Windows archive path: {path}")
    return "/".join(part.casefold() for part in parts)


def _parse_manifest(document: object) -> UpdateManifest:
    if not isinstance(document, dict) or set(document) != {
        "schema_version", "version", "channel", "archive_url", "archive_sha256", "archive_size", "files"
    } or type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("release manifest schema is invalid")
    version = document["version"]
    _version_tuple(version)
    if document["channel"] != "stable":
        raise ValueError("only stable releases are supported")
    url = document["archive_url"]
    if not isinstance(url, str):
        raise ValueError("archive URL must be a string")
    _validate_release_asset_url(url, version=version)
    digest = document["archive_sha256"]
    size = document["archive_size"]
    if not isinstance(digest, str) or not _HEX.fullmatch(digest):
        raise ValueError("archive SHA-256 is invalid")
    if type(size) is not int or not 0 < size <= MAX_ARCHIVE:
        raise ValueError("archive size is invalid")
    files = document["files"]
    if not isinstance(files, list) or not 0 < len(files) <= MAX_FILES:
        raise ValueError("file list is invalid")
    result: list[tuple[str, str]] = []
    keys: set[str] = set()
    for entry in files:
        if not isinstance(entry, list) or len(entry) != 2 or not all(isinstance(item, str) for item in entry):
            raise ValueError("file record is invalid")
        path, file_hash = entry
        key = _validate_member(path)
        if key in keys or not _HEX.fullmatch(file_hash):
            raise ValueError("duplicate file alias or invalid hash")
        keys.add(key)
        result.append((path, file_hash))
    if result != sorted(result, key=lambda item: item[0]):
        raise ValueError("file records must be sorted")
    return UpdateManifest(version, "stable", url, digest, size, tuple(result))


def _validate_manifest_instance(manifest: UpdateManifest) -> None:
    _parse_manifest({
        "schema_version": 1, "version": manifest.version, "channel": manifest.channel,
        "archive_url": manifest.archive_url, "archive_sha256": manifest.archive_sha256,
        "archive_size": manifest.archive_size, "files": [list(item) for item in manifest.files],
    })


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _regular_unresolved_root(path: Path) -> Path:
    """Validate each lexical component before resolving any junction target."""

    if ".." in path.parts:
        raise ValueError("install path contains a parent traversal")
    raw = Path(os.path.abspath(path))
    for component in (raw, *raw.parents):
        if _reparse(component):
            raise ValueError("install path contains a reparse point or junction")
    if not raw.is_dir():
        raise ValueError("install root is missing")
    return raw


def _install_inventory(root: Path) -> tuple[tuple[str, str], ...]:
    root = _regular_unresolved_root(root)
    manifest_path = root / "SHA256SUMS.txt"
    if not manifest_path.is_file() or _reparse(manifest_path):
        raise ValueError("install inventory is missing")
    expected: dict[str, str] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise ValueError("install inventory is malformed")
        digest, relative = match.groups()
        key = _validate_member("Mini-Monitor/" + relative)
        if key in expected:
            raise ValueError("install inventory has duplicate aliases")
        expected[key] = digest
    actual: dict[str, str] = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if _reparse(path):
                raise ValueError("install tree contains a reparse point")
        for name in files:
            path = Path(directory) / name
            if path == manifest_path:
                continue
            relative = path.relative_to(root).as_posix()
            key = _validate_member("Mini-Monitor/" + relative)
            if key in actual:
                raise ValueError("install tree has duplicate aliases")
            actual[key] = _sha256_file(path)
    if actual != expected:
        raise ValueError("unknown or changed install files; manual update required")
    return tuple(sorted((key.removeprefix("mini-monitor/"), digest) for key, digest in expected.items()))


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in _REDIRECT_HOSTS or parsed.username or parsed.password:
            raise ValueError("release asset redirected to unapproved host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect)


def _fetch_bytes(url: str, limit: int = MAX_METADATA) -> bytes:
    accept = "application/vnd.github+json" if url == API_URL else "application/octet-stream"
    request = urllib.request.Request(url, headers={"User-Agent": "Mini-Monitor-Updater/1", "Accept": accept})
    with _OPENER.open(request, timeout=REQUEST_TIMEOUT) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("download exceeds size limit")
    return data


def _download_archive(url: str, target: Path, expected_size: int) -> None:
    _validate_release_asset_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "Mini-Monitor-Updater/1"})
    count = 0
    with _OPENER.open(request, timeout=REQUEST_TIMEOUT) as response, target.open("xb") as stream:
        while block := response.read(1024 * 1024):
            count += len(block)
            if count > min(expected_size, MAX_ARCHIVE):
                raise ValueError("archive exceeds signed size")
            stream.write(block)
    if count != expected_size:
        raise ValueError("archive size differs from signed manifest")


def _extract_verified_archive(archive_path: Path, staged_root: Path, manifest: UpdateManifest) -> None:
    expected = {path.casefold(): digest for path, digest in manifest.files}
    seen: set[str] = set()
    total = 0
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES * 2:
            raise ValueError("archive contains too many entries")
        for info in infos:
            name = info.filename.rstrip("/")
            key = _validate_member(name)
            if key in seen:
                raise ValueError("archive contains duplicate Windows aliases")
            seen.add(key)
            unix_kind = (info.external_attr >> 16) & 0o170000
            if unix_kind not in (0, stat.S_IFREG, stat.S_IFDIR) or (info.external_attr & 0x400):
                raise ValueError("archive contains link or reparse point")
            if info.is_dir():
                continue
            if key not in expected:
                raise ValueError("archive contains unsigned file")
            total += info.file_size
            if total > MAX_UNPACKED or info.file_size > MAX_UNPACKED:
                raise ValueError("archive uncompressed size exceeds limit")
            relative = Path(*name.split("/")[1:])
            destination = staged_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            written = 0
            with archive.open(info) as source, destination.open("xb") as target:
                while block := source.read(1024 * 1024):
                    written += len(block)
                    if written > info.file_size or written > MAX_UNPACKED:
                        raise ValueError("archive member exceeds declared size")
                    digest.update(block)
                    target.write(block)
            if written != info.file_size or digest.hexdigest() != expected[key]:
                raise ValueError("archive member hash mismatch")
    actual = {"Mini-Monitor/" + path.relative_to(staged_root).as_posix(): _sha256_file(path)
              for path in staged_root.rglob("*") if path.is_file()}
    if {path.casefold(): digest for path, digest in actual.items()} != expected:
        raise ValueError("archive file set differs from signed manifest")
    if not (staged_root / "Mini-Monitor.exe").is_file():
        raise ValueError("release desktop executable is missing")


def _within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def _restart_args(arguments: list[str]) -> list[str]:
    """Carry only supported run-mode flags into the replacement process."""

    kept: list[str] = []
    position = 0
    while position < len(arguments):
        flag = arguments[position]
        if flag in ("--minimized", "--no-serial") and flag not in kept:
            kept.append(flag)
        elif flag in ("--desktop-smoke", "--headless-run") and position + 1 < len(arguments):
            try:
                raw_duration = arguments[position + 1]
                duration = float(raw_duration) if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw_duration) else 0
            except ValueError:
                duration = 0
            if 0 < duration <= 3600:
                kept.extend((flag, arguments[position + 1]))
                position += 1
        position += 1
    return kept


def stage_update(manifest: UpdateManifest, *, install_root: Path, config_path: Path | None) -> PreparedUpdate:
    _validate_manifest_instance(manifest)
    root = _regular_unresolved_root(install_root)
    if root.name != "Mini-Monitor":
        raise ValueError("automatic update requires a regular Mini-Monitor install")
    if config_path is not None and _within(config_path, root):
        raise ValueError("custom config inside install tree requires manual update")
    old_files = _install_inventory(root)
    stage_dir = Path(tempfile.mkdtemp(prefix=".mini-monitor-update-", dir=root.parent))
    if stage_dir.parent.resolve() != root.parent.resolve():
        raise ValueError("staging must share the install volume")
    archive_path = stage_dir / "release.zip"
    staged_root = stage_dir / "Mini-Monitor"
    staged_root.mkdir()
    try:
        _download_archive(manifest.archive_url, archive_path, manifest.archive_size)
        if archive_path.stat().st_size != manifest.archive_size or _sha256_file(archive_path) != manifest.archive_sha256:
            raise ValueError("archive hash or size differs from signed manifest")
        _extract_verified_archive(archive_path, staged_root, manifest)
        helper_source = resource_path(HELPER_RESOURCE)
        if not helper_source.is_file():
            raise ValueError("trusted update helper is not bundled")
        helper_path = stage_dir / "Apply-Update.ps1"
        shutil.copyfile(helper_source, helper_path)
        if _sha256_file(helper_path) != _sha256_file(helper_source):
            raise ValueError("update helper copy is not identical to bundle")
        journal = stage_dir / "update-journal.json"
        record = {
            "schema_version": 1,
            "state": "prepared",
            "install_root": str(root),
            "staged_root": str(staged_root),
            "backup_root": str(root.parent / f".mini-monitor-backup-{uuid.uuid4().hex}"),
            "ack_path": str(stage_dir / "startup-ack.json"),
            "nonce": uuid.uuid4().hex,
            "old_files": [list(item) for item in old_files],
            "new_files": [list(item) for item in manifest.files],
            "helper_sha256": _sha256_file(helper_path),
            "archive_sha256": manifest.archive_sha256,
            "version": manifest.version,
            "config_path": str(config_path.resolve(strict=False)) if config_path else None,
            "restart_args": _restart_args(sys.argv[1:]),
            "old_inventory_sha256": _sha256_file(root / "SHA256SUMS.txt"),
        }
        journal.write_bytes(_canonical(record))
        return PreparedUpdate(root, staged_root, journal, helper_path)
    except Exception:
        if stage_dir.is_dir() and stage_dir.parent.resolve() == root.parent.resolve():
            shutil.rmtree(stage_dir)
        raise


def launch_update_helper(prepared: PreparedUpdate, *, parent_pid: int) -> bool:
    # Do not tell the GUI to exit while cleanup still owns the install lock.
    if not _UPDATE_MAINTENANCE_LOCK.acquire(blocking=False):
        return False
    try:
        launched = _launch_update_helper(prepared, parent_pid=parent_pid)
        if launched:
            _UPDATE_HANDOFF.set()
        return launched
    finally:
        _UPDATE_MAINTENANCE_LOCK.release()


def update_cleanup_busy() -> bool:
    return _UPDATE_MAINTENANCE_LOCK.locked()


def _launch_update_helper(prepared: PreparedUpdate, *, parent_pid: int) -> bool:
    if os.name != "nt" or not isinstance(parent_pid, int) or parent_pid <= 0:
        return False
    journal = prepared.journal_path.resolve(strict=True)
    helper = prepared.helper_path.resolve(strict=True)
    stage_dir = journal.parent
    if (
        helper.parent != stage_dir
        or prepared.staged_root.resolve(strict=True).parent != stage_dir
        or prepared.install_root.resolve(strict=True).parent != stage_dir.parent
        or _reparse(helper)
        or _reparse(stage_dir)
    ):
        return False
    try:
        record = json.loads(journal.read_bytes(), object_pairs_hook=_unique_object)
        if (
            record.get("state") != "prepared"
            or Path(record["install_root"]).resolve() != prepared.install_root.resolve()
            or Path(record["staged_root"]).resolve() != prepared.staged_root.resolve()
            or record["helper_sha256"] != _sha256_file(helper)
            or _sha256_file(resource_path(HELPER_RESOURCE)) != _sha256_file(helper)
        ):
            return False
        _install_inventory(prepared.install_root)
        if _sha256_file(prepared.install_root / "SHA256SUMS.txt") != record["old_inventory_sha256"]:
            return False
        expected = {path.casefold(): digest for path, digest in record["new_files"]}
        actual = {
            "Mini-Monitor/" + path.relative_to(prepared.staged_root).as_posix(): _sha256_file(path)
            for path in prepared.staged_root.rglob("*") if path.is_file()
        }
        if {path.casefold(): digest for path, digest in actual.items()} != expected:
            return False
    except (OSError, ValueError, KeyError, TypeError):
        return False
    powershell_paths = windows_powershell_paths()
    if powershell_paths is None:
        return False
    powershell, modules = powershell_paths
    args = [
        str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(helper), "-JournalPath", str(journal),
        "-JournalSha256", _sha256_file(journal),
        "-HelperSha256", _sha256_file(helper), "-ParentPid", str(parent_pid),
    ]
    try:
        environment = os.environ.copy()
        pin_powershell_modules(environment, modules)
        subprocess.Popen(args, cwd=stage_dir, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                         close_fds=True, env=environment)
        return True
    except OSError:
        return False


def _read_update_context() -> tuple[dict[str, Any], Path, Path] | None:
    """Accept relaunch environment only for this frozen update-owned install."""

    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    ack_raw = os.environ.get("MINI_MONITOR_UPDATE_ACK_PATH")
    nonce = os.environ.get("MINI_MONITOR_UPDATE_ACK_NONCE")
    if not ack_raw or not nonce or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        return None
    try:
        executable = Path(sys.executable).resolve(strict=True)
        install = executable.parent
        ack = Path(ack_raw).resolve(strict=False)
        stage_dir = ack.parent
        journal = stage_dir / "update-journal.json"
        if (
            executable.name != "Mini-Monitor.exe"
            or install.name != "Mini-Monitor"
            or not stage_dir.name.startswith(".mini-monitor-update-")
            or stage_dir.parent.resolve() != install.parent.resolve()
            or ack.name != "startup-ack.json"
            or _reparse(install) or _reparse(stage_dir) or _reparse(journal)
        ):
            return None
        record = json.loads(journal.read_bytes(), object_pairs_hook=_unique_object)
        if (
            record.get("state") != "new_installed"
            or record.get("nonce") != nonce
            or Path(record["install_root"]).resolve() != install
            or Path(record["ack_path"]).resolve(strict=False) != ack
            or not Path(record["backup_root"]).is_dir()
        ):
            return None
        return record, ack, journal
    except (OSError, ValueError, KeyError, TypeError):
        return None


def resolve_update_config() -> Path | None:
    context = _read_update_context()
    if context is None:
        return None
    record, _, _ = context
    raw = os.environ.get("MINI_MONITOR_UPDATE_CONFIG_PATH")
    if raw != record.get("config_path") or not raw:
        return None
    path = Path(raw).resolve(strict=False)
    install = Path(record["install_root"]).resolve()
    if _within(path, install) or _reparse(path):
        return None
    return path


def acknowledge_update_startup(*, current_version: str) -> bool:
    context = _read_update_context()
    if context is None:
        return False
    record, ack, _ = context
    if current_version != record.get("version"):
        return False
    temporary = ack.with_name(f"startup-ack-{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(_canonical({"nonce": record["nonce"], "ready": True, "version": current_version}))
        os.replace(temporary, ack)
        return True
    except OSError:
        return False
    finally:
        if temporary.exists():
            temporary.unlink()


def _repair_update_references(root: Path, backup: Path) -> bool:
    """Run the bundled, hidden link repair before recycling an old target."""

    trusted = windows_powershell_paths()
    script = resource_path("scripts/Repair-UpdateLinks.ps1")
    if trusted is None or not script.is_file() or _reparse(script):
        return False
    powershell, modules = trusted
    environment = dict(os.environ)
    pin_powershell_modules(environment, modules)
    environment["MINI_MONITOR_REPAIR_LINKS"] = base64.b64encode(
        _canonical({"install_root": str(root), "retired_roots": [str(backup)]})
    ).decode("ascii")
    try:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(script)], cwd=root, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=20,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _cleanup_stage_verified(stage: Path, record: dict[str, Any]) -> bool:
    allowed = {"update-journal.json", "startup-ack.json", "release.zip", "Apply-Update.ps1"}
    if any(_reparse(p) or not p.is_file() or p.name not in allowed for p in stage.iterdir()):
        return False
    ack = stage / "startup-ack.json"
    if ack.stat().st_size > MAX_METADATA:
        return False
    acknowledgement = json.loads(ack.read_bytes(), object_pairs_hook=_unique_object)
    if acknowledgement != {"nonce": record["nonce"], "ready": True, "version": record["version"]}:
        return False
    if _sha256_file(stage / "Apply-Update.ps1") != record["helper_sha256"]:
        return False
    archive = stage / "release.zip"
    if archive.stat().st_size > MAX_ARCHIVE:
        return False
    if record.get("archive_sha256"):
        return _sha256_file(archive) == record["archive_sha256"]
    # The 0.2.7 helper does not record the ZIP digest. Its verified payload
    # inventory still lets the new version safely clean its upgrade stage.
    expected = {str(name).casefold(): str(digest) for name, digest in record["new_files"]}
    actual: dict[str, str] = {}
    with zipfile.ZipFile(archive) as package:
        if len(package.infolist()) > MAX_FILES or sum(p.file_size for p in package.infolist()) > MAX_UNPACKED:
            return False
        for info in package.infolist():
            if info.is_dir():
                continue
            _validate_member(info.filename)
            key = info.filename.casefold()
            if key not in expected or key in actual:
                return False
            digest = hashlib.sha256()
            with package.open(info) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            actual[key] = digest.hexdigest()
    return actual == expected


def _cleanup_backup_verified(backup: Path, record: dict[str, Any]) -> bool:
    old_files = tuple((str(path), str(digest)) for path, digest in record["old_files"])
    if (_install_inventory(backup) != old_files
            or _sha256_file(backup / "SHA256SUMS.txt") != record["old_inventory_sha256"]):
        return False
    expected_dirs = {parent.as_posix().casefold() for name, _digest in old_files
                     for parent in Path(name).parents if parent != Path(".")}
    return all(path.relative_to(backup).as_posix().casefold() in expected_dirs
               for path in backup.rglob("*") if path.is_dir())


def cleanup_healthy_update_backup(install_root: Path) -> int:
    if _UPDATE_HANDOFF.is_set() or not _UPDATE_MAINTENANCE_LOCK.acquire(blocking=False):
        return 0
    try:
        return _cleanup_healthy_update_backup(install_root)
    finally:
        _UPDATE_MAINTENANCE_LOCK.release()


def _cleanup_healthy_update_backup(install_root: Path) -> int:
    """Recycle only completed, verified update trees under the install parent.

    Failed/incomplete journals and any tree containing unknown user files are
    retained. The journal itself travels to the Recycle Bin with the stage.
    """
    lock = None
    try:
        root = _regular_unresolved_root(install_root)
        if root.name != "Mini-Monitor" or os.name != "nt":
            return 0
        import msvcrt
        lock_path = root.parent / ".mini-monitor-update.lock"
        if _reparse(lock_path):
            return 0
        lock = lock_path.open("a+b")
        if lock_path.stat().st_size == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        _install_inventory(root)  # Never clean on a damaged/partial new install.
    except (OSError, ValueError):
        if lock is not None:
            lock.close()
        return 0
    removed = 0
    try:
        for stage in list(root.parent.iterdir())[:4096]:
            try:
                if not re.fullmatch(r"\.mini-monitor-update-[A-Za-z0-9_-]{6,40}", stage.name):
                    continue
                _regular_unresolved_root(stage)
                journal = stage / "update-journal.json"
                if not journal.is_file() or _reparse(journal) or journal.stat().st_size > MAX_METADATA:
                    continue
                record = json.loads(journal.read_bytes(), object_pairs_hook=_unique_object)
                if (not isinstance(record, dict) or record.get("schema_version") != 1
                        or record.get("state") not in {"healthy_backup_retained", "backup_recycled"}
                        or Path(record["install_root"]) != root
                        or Path(record["staged_root"]) != stage / "Mini-Monitor"
                        or Path(record["ack_path"]) != stage / "startup-ack.json"):
                    continue
                backup = Path(record["backup_root"])
                if (backup.parent != root.parent
                        or not re.fullmatch(r"\.mini-monitor-backup-[0-9a-f]{32}", backup.name)):
                    continue
                # No wildcard deletion: a personal file, junction, or unknown
                # subdirectory in a stage protects the entire update pair.
                if not _cleanup_stage_verified(stage, record):
                    continue
                config = record.get("config_path")
                if config and any(_within(Path(config), path) for path in (root, stage, backup)):
                    continue
                if record["state"] == "healthy_backup_retained":
                    if not _cleanup_backup_verified(backup, record):
                        continue
                    if not _repair_update_references(root, backup):
                        continue
                    if not _cleanup_stage_verified(stage, record) or not _cleanup_backup_verified(backup, record):
                        continue
                    if not _recycle_directory(backup) or backup.exists():
                        continue
                    removed += 1
                    record["state"] = "backup_recycled"
                    temporary = journal.with_suffix(".tmp")
                    temporary.write_bytes(_canonical(record))
                    os.replace(temporary, journal)
                if not backup.exists() and _cleanup_stage_verified(stage, record):
                    _recycle_directory(stage)
            except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
                continue
    finally:
        lock.close()  # Closing also releases the byte-range lock.
    return removed


def finish_update_cleanup(install_root: Path, *, wait_for_update: bool = False) -> int:
    """Background-only: let the helper finish its acknowledgement/lock first."""

    removed = 0
    for attempt in range(60 if wait_for_update else 1):
        if _UPDATE_HANDOFF.is_set():
            return removed
        removed += cleanup_healthy_update_backup(install_root)
        if not wait_for_update:
            return removed
        pending = False
        try:
            root = _regular_unresolved_root(install_root)
            for stage in list(root.parent.iterdir())[:4096]:
                if not re.fullmatch(r"\.mini-monitor-update-[A-Za-z0-9_-]{6,40}", stage.name):
                    continue
                journal = stage / "update-journal.json"
                if _reparse(stage) or _reparse(journal) or not journal.is_file() or journal.stat().st_size > MAX_METADATA:
                    continue
                try:
                    record = json.loads(journal.read_bytes(), object_pairs_hook=_unique_object)
                    if (isinstance(record, dict) and record.get("install_root") == str(root)
                            and record.get("state") in {"new_installed", "healthy_backup_retained"}):
                        pending = True
                except (OSError, ValueError, TypeError):
                    continue
        except (OSError, ValueError):
            return removed
        if not pending:
            return removed
        if attempt < 59:
            time.sleep(1)
    return removed


class UpdateService:
    """One background worker; Tk callers only enqueue and read immutable snapshots."""

    def __init__(
        self,
        *,
        current_version: str,
        install_root: Path | None,
        config_path: Path | None,
        _fetcher: Any = None,
        _state_path: Path | None = None,
        _clock: Any = None,
    ) -> None:
        _version_tuple(current_version)
        self.current_version = current_version
        self.install_root = install_root
        self.config_path = config_path
        self._fetcher = _fetcher or _fetch_bytes
        self._state_path = _state_path or (user_data_dir() / "updates" / "check-state.json")
        self._clock = _clock or time.time
        self._lock = threading.RLock()
        self._snapshot = UpdateSnapshot()
        self._manifest: UpdateManifest | None = None
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._closed = False
        self._busy = False
        self._last_check: float | None = None
        self._delivered: set[str] = set()
        self._pending_version: str | None = None
        self._startup_recheck_available = False
        self._load_state()
        self._thread = threading.Thread(target=self._run, name="mini-monitor-updater", daemon=True)
        self._thread.start()

    def _load_state(self) -> None:
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
            value = state.get("last_check")
            self._last_check = float(value) if value is not None else None
            self._delivered = set(item for item in state.get("delivered_versions", []) if isinstance(item, str))
            pending = state.get("pending_version")
            try:
                if (
                    pending not in self._delivered
                    and _version_tuple(pending) > _version_tuple(self.current_version)
                ):
                    self._pending_version = pending
                    self._startup_recheck_available = True
            except ValueError:
                pass
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _save_state(self) -> None:
        with self._lock:
            payload = _canonical({
                "last_check": self._last_check,
                "delivered_versions": sorted(self._delivered),
                "pending_version": self._pending_version,
            })
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_name(f"{self._state_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, self._state_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def check(self, *, force: bool = False) -> bool:
        with self._lock:
            if self._closed or self._busy or (
                not force and not self._startup_recheck_available and self._last_check is not None
                and self._clock() - self._last_check < _CHECK_INTERVAL
            ):
                return False
            self._busy = True
            self._startup_recheck_available = False
            self._last_check = self._clock()
            self._snapshot = UpdateSnapshot("checking", self._snapshot.version,
                                             self._snapshot.release_url, "업데이트 확인 중", None, False)
            self._queue.put("check")
            return True

    def prepare(self) -> bool:
        with self._lock:
            if self._closed or self._busy or self._manifest is None or self._snapshot.state != "available":
                return False
            if self.install_root is None:
                self._snapshot = UpdateSnapshot("manual_required", self._manifest.version,
                                                 self._snapshot.release_url, "수동 설치가 필요합니다", None, False)
                return False
            self._busy = True
            self._snapshot = UpdateSnapshot("preparing", self._manifest.version,
                                             self._snapshot.release_url, "업데이트 준비 중", None, False)
            self._queue.put("prepare")
            return True

    def snapshot(self) -> UpdateSnapshot:
        with self._lock:
            return self._snapshot

    def mark_notification_delivered(self, version: str) -> bool:
        """Acknowledge only a displayed banner or successful native notification."""

        with self._lock:
            current = self._snapshot
            if (
                self._closed or not isinstance(version, str)
                or not version or current.version != version
                or current.state not in {"available", "manual_required"}
            ):
                return False
            if version not in self._delivered:
                self._delivered.add(version)
                self._queue.put("persist_delivery")
            if self._pending_version == version:
                self._pending_version = None
            self._snapshot = UpdateSnapshot(
                current.state, current.version, current.release_url,
                current.message, current.prepared, False,
            )
            return True

    def dismiss(self) -> bool:
        with self._lock:
            current = self._snapshot
            if current.version and current.state in {"available", "manual_required"}:
                return self.mark_notification_delivered(current.version)
            self._snapshot = UpdateSnapshot(current.state, current.version, current.release_url,
                                             current.message, current.prepared, False)
            return True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._queue.put(None)
        self._thread.join(timeout=REQUEST_TIMEOUT + 2)

    def _run(self) -> None:
        while command := self._queue.get():
            if command == "persist_delivery":
                try:
                    self._save_state()
                except Exception:
                    # Notice delivery already occurred. Persistence failure
                    # may repeat it after restart, but must neither kill the
                    # worker nor release a reserved check/prepare operation.
                    pass
                continue
            try:
                if command == "check":
                    self._save_state()
                    terminal = self._check_once()
                elif command == "prepare":
                    terminal = self._prepare_once()
                else:
                    continue
            except Exception:
                with self._lock:
                    previous = self._snapshot
                    self._snapshot = UpdateSnapshot("error", previous.version, previous.release_url,
                                                     "업데이트를 확인하거나 준비하지 못했습니다", None, False)
                    self._busy = False
            else:
                with self._lock:
                    self._snapshot = terminal
                    self._busy = False

    def _check_once(self) -> UpdateSnapshot:
        release = json.loads(self._fetcher(API_URL, MAX_METADATA).decode("utf-8"))
        if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
            raise ValueError("latest release is not stable")
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith("v"):
            raise ValueError("stable release tag is invalid")
        version = tag[1:]
        if _version_tuple(version) <= _version_tuple(self.current_version):
            with self._lock:
                self._manifest = None
                self._pending_version = None
            self._record_check()
            return UpdateSnapshot("idle", None, None, "최신 버전", None, False)
        release_url = f"https://github.com/{REPO}/releases/tag/{tag}"
        assets = release.get("assets")
        if not isinstance(assets, list):
            raise ValueError("release assets are missing")
        by_name = {item.get("name"): item.get("browser_download_url") for item in assets if isinstance(item, dict)}
        manifest_url = by_name.get(MANIFEST_ASSET)
        signature_url = by_name.get(SIGNATURE_ASSET)
        manifest: UpdateManifest | None = None
        if isinstance(manifest_url, str) and isinstance(signature_url, str):
            try:
                _validate_release_asset_url(manifest_url, version=version)
                _validate_release_asset_url(signature_url, version=version)
                public_key = resource_path(PUBLIC_KEY_RESOURCE).read_bytes()
                manifest = verify_release_manifest(
                    self._fetcher(manifest_url, MAX_METADATA),
                    self._fetcher(signature_url, 64), public_key,
                )
                if manifest.version != version:
                    raise ValueError("signed manifest does not match release tag")
            except (OSError, ValueError):
                manifest = None
        with self._lock:
            self._manifest = manifest
            first_notice = version not in self._delivered
            self._pending_version = version if first_notice else None
            state = "available" if manifest is not None and self.install_root is not None else "manual_required"
            message = "업데이트를 적용할 수 있습니다" if state == "available" else "새 버전은 수동 다운로드가 필요합니다"
        self._record_check()
        return UpdateSnapshot(state, version, release_url, message, None, first_notice)

    def _record_check(self) -> None:
        with self._lock:
            self._last_check = self._clock()
        self._save_state()

    def _prepare_once(self) -> UpdateSnapshot:
        assert self._manifest is not None and self.install_root is not None
        prepared = stage_update(self._manifest, install_root=self.install_root, config_path=self.config_path)
        with self._lock:
            return UpdateSnapshot("ready", self._manifest.version,
                                  self._snapshot.release_url, "업데이트 준비 완료", prepared, False)
