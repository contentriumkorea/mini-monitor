from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import zipfile


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "THIRD_PARTY_COMPONENTS.json"
LICENSE_MANIFEST_PATH = ROOT / "LICENSES/MANIFEST.json"
UPSTREAM_COMMIT = "262a28a3ab615f2047c0bf44afc482cc341c465c"
GPL_SHA256 = "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986"
PYSTRAY_COMMIT = "1907f8681d6d421517c63d94f425f9cdd74d0034"
PYSTRAY_SOURCE_SHA256 = "4751562ba90301e054c87606079c1599301d84e7d1e4074b12af4f54a80a4768"
OPENSSL_LICENSE_SHA256 = "7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a"

EXPECTED_PYTHON_PACKAGES = {
    "altgraph": "0.17.5",
    "cffi": "2.1.1",
    "clr_loader": "0.3.1",
    "colorama": "0.4.6",
    "coverage": "7.15.4",
    "cryptography": "50.0.2",
    "iniconfig": "2.3.0",
    "packaging": "26.3",
    "pefile": "2024.8.26",
    "Pillow": "12.3.0",
    "pluggy": "1.6.0",
    "psutil": "7.2.2",
    "pycparser": "3.0",
    "Pygments": "2.20.0",
    "pyinstaller": "6.22.0",
    "pyinstaller-hooks-contrib": "2026.6",
    "pyserial": "3.5",
    "pystray": "0.19.5",
    "pytest": "9.1.1",
    "pytest-cov": "7.1.0",
    "pythonnet": "3.1.0",
    "pywin32-ctypes": "0.2.3",
    "setuptools": "84.0.0",
    "six": "1.17.0",
}

EXPECTED_BUNDLED_DLLS = {
    "BlackSharp.Core.dll",
    "DiskInfoToolkit.dll",
    "HidSharp.dll",
    "LibreHardwareMonitorLib.dll",
    "RAMSPDToolkit-NDD.dll",
    "System.Buffers.dll",
    "System.Memory.dll",
    "System.Numerics.Vectors.dll",
    "System.Runtime.CompilerServices.Unsafe.dll",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inventory() -> dict[str, object]:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _license_manifest() -> dict[str, object]:
    return json.loads(LICENSE_MANIFEST_PATH.read_text(encoding="utf-8"))


def _components(kind: str | None = None) -> list[dict[str, object]]:
    components = _inventory()["components"]
    assert isinstance(components, list)
    if kind is None:
        return components
    return [component for component in components if component["kind"] == kind]


def test_gpl_text_is_exact_pinned_upstream_copy() -> None:
    root_license = ROOT / "LICENSE"
    upstream_copy = ROOT / "third_party/turing-smart-screen-python/LICENSE"

    assert root_license.read_bytes() == upstream_copy.read_bytes()
    assert _sha256(root_license) == GPL_SHA256
    assert _sha256(upstream_copy) == GPL_SHA256
    text = root_license.read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in text
    assert "Version 3, 29 June 2007" in text
    assert "END OF TERMS AND CONDITIONS" in text


def test_upstream_attribution_and_derivation_are_explicit() -> None:
    provenance_dir = ROOT / "third_party/turing-smart-screen-python"
    copyright_text = (provenance_dir / "COPYRIGHT").read_text(encoding="utf-8")
    authors_text = (provenance_dir / "AUTHORS").read_text(encoding="utf-8")
    source_text = (provenance_dir / "SOURCE.md").read_text(encoding="utf-8")
    protocol_text = (
        ROOT / "src/ai_mini_monitor/transport/protocol_rev_a.py"
    ).read_text(encoding="utf-8")

    assert "Copyright (C) 2021 Matthieu Houdebine (mathoudebine)" in copyright_text
    assert "Matthieu Houdebine (@mathoudebine)" in authors_text
    assert UPSTREAM_COMMIT in source_text
    assert "src/ai_mini_monitor/transport/protocol_rev_a.py" in source_text
    assert "combined distribution are licensed `GPL-3.0-or-later`" in source_text
    assert "SPDX-License-Identifier: GPL-3.0-or-later" in protocol_text
    assert UPSTREAM_COMMIT in protocol_text


def test_inventory_schema_and_component_identity() -> None:
    inventory = _inventory()
    project = inventory["project"]
    components = _components()

    assert inventory["schema_version"] == "1.1"
    assert project["license"] == "GPL-3.0-or-later"
    assert project["combined_distribution_license"] == "GPL-3.0-or-later"
    assert project["device_vendor_material_included"] is False
    assert inventory["exclusion_statement"].startswith("No device-vendor executable")
    assert inventory["redistributable_license_bundle"]["manifest"] == "LICENSES/MANIFEST.json"

    refs = [component["bom_ref"] for component in components]
    assert len(refs) == len(set(refs))
    for component in components:
        assert component["name"]
        assert component["version"]
        assert component["license"]
        assert str(component["source_url"]).startswith("https://")
        assert isinstance(component["scope"], list) and component["scope"]
        assert isinstance(component["files"], list)
        assert component["file_note"]
        assert component["source_offer_note"]


def test_installed_python_dependency_closure_matches_snapshot() -> None:
    recorded = {
        component["name"]: component["version"]
        for component in _components("python-package")
    }
    assert recorded == EXPECTED_PYTHON_PACKAGES

    for name, version in EXPECTED_PYTHON_PACKAGES.items():
        assert importlib.metadata.version(name) == version

    for component in _components("python-package"):
        assert any(
            relationship.endswith("-direct") or relationship.endswith("-transitive")
            for relationship in component["scope"]
        )
        for relative in component["installed_license_files"]:
            assert (ROOT / relative).is_file(), relative


def test_all_recorded_files_exist_and_match_size_and_sha256() -> None:
    inventory = _inventory()
    records = [inventory["project"]["license_file"]]
    records.extend(
        record
        for component in _components()
        for record in component["files"]
    )

    root_resolved = ROOT.resolve()
    for record in records:
        relative = PurePosixPath(record["path"])
        assert not relative.is_absolute()
        assert ".." not in relative.parts
        path = ROOT.joinpath(*relative.parts)
        assert path.resolve().is_relative_to(root_resolved)
        assert path.is_file(), record["path"]
        assert path.stat().st_size == record["size"], record["path"]
        assert _sha256(path) == record["sha256"], record["path"]


def test_bundled_dll_and_font_inventory_is_complete() -> None:
    disk_dlls = {
        path.name
        for path in (ROOT / "third_party/librehardwaremonitor").glob("*.dll")
    }
    recorded_dlls = {
        PurePosixPath(record["path"]).name
        for component in _components()
        for record in component["files"]
        if record["path"].lower().endswith(".dll")
    }
    assert disk_dlls == EXPECTED_BUNDLED_DLLS
    assert recorded_dlls == EXPECTED_BUNDLED_DLLS

    recorded_fonts = {
        PurePosixPath(record["path"]).name
        for component in _components("font")
        for record in component["files"]
        if record["path"].lower().endswith(".ttf")
    }
    assert recorded_fonts == {"Inter-Variable.ttf", "JetBrainsMono-Variable.ttf"}


def test_official_codex_runtime_provenance_and_pinned_binary() -> None:
    component = next(component for component in _components() if component["name"] == "OpenAI Codex app-server")
    pin = json.loads((ROOT / "third_party/codex-app-server/RUNTIME.json").read_text(encoding="utf-8"))
    assert component["version"] == "rust-v0.161.0"
    assert component["license"] == "Apache-2.0"
    assert component["runtime_pin"] == "third_party/codex-app-server/RUNTIME.json"
    assert component["source_url"] == pin["upstream_source"]
    assert pin["archive_sha256"] == "7f1c62370b877006ee044dfea731ce753f3646ccd7082d6cb3d1d81f207f3a5e"
    assert pin["runtime_sha256"] == "bbc4400446037926e2446f36ccc74d7fa01577084e517c5f43ae2c7c7c82346b"
    runtime = ROOT / pin["runtime_path"]
    if runtime.is_file():
        assert runtime.stat().st_size == pin["runtime_size"]
        assert _sha256(runtime) == pin["runtime_sha256"]
    source = (ROOT / "third_party/codex-app-server/SOURCE.md").read_text(encoding="utf-8")
    assert "LICENSES/python/cryptography/LICENSE.APACHE" in source
    assert "Apache License" in (ROOT / "LICENSES/python/cryptography/LICENSE.APACHE").read_text(encoding="utf-8")
    assert "OpenAI Codex" in (ROOT / "third_party/codex-app-server/NOTICE").read_text(encoding="utf-8")


def test_no_executable_is_claimed_as_a_bundled_component() -> None:
    recorded_paths = [
        record["path"]
        for component in _components()
        for record in component["files"]
    ]
    assert all(not path.lower().endswith(".exe") for path in recorded_paths)


def test_human_notice_covers_key_obligations() -> None:
    notice = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert UPSTREAM_COMMIT in notice
    assert "combined distribution are licensed under `GPL-3.0-or-later`" in notice
    assert "LGPL-3.0-or-later" in notice
    assert "GPL-2.0-or-later WITH Bootloader-exception" in notice
    assert "LibreHardwareMonitor-v0.9.6-source.zip" in notice
    assert "No device-vendor executable" in notice
    assert "pystray-v0.19.5-1907f868-source.zip" in notice
    assert "OpenSSL 3.0.16" in notice
    assert "Microsoft Distributable Code" in notice
    assert "Tcl 8.6.15" in notice
    assert "Tk 8.6.15" in notice


def test_redistributable_license_manifest_is_complete_and_integral() -> None:
    manifest = _license_manifest()
    assert manifest["schema_version"] == "1.0"
    records = manifest["files"]
    assert isinstance(records, list)

    recorded_paths = {record["path"] for record in records}
    disk_paths = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "LICENSES").rglob("*")
        if path.is_file() and path.name not in {"SOURCE.md", "MANIFEST.json"}
    }
    assert recorded_paths == disk_paths

    root_resolved = ROOT.resolve()
    for record in records:
        relative = PurePosixPath(record["path"])
        assert not relative.is_absolute()
        assert ".." not in relative.parts
        path = ROOT.joinpath(*relative.parts)
        assert path.resolve().is_relative_to(root_resolved)
        assert path.stat().st_size == record["size"]
        assert _sha256(path) == record["sha256"]
        assert record["component"] in manifest["component_versions"]

    python_components = {
        record["component"]
        for record in records
        if str(record["path"]).startswith("LICENSES/python/")
    }
    assert python_components == set(EXPECTED_PYTHON_PACKAGES)


def test_pystray_corresponding_source_is_exact_and_complete() -> None:
    component = next(
        component for component in _components("python-package")
        if component["name"] == "pystray"
    )
    assert component["upstream_tag"] == "v0.19.5"
    assert component["upstream_commit"] == PYSTRAY_COMMIT

    archive = ROOT / "third_party/source/pystray-v0.19.5-1907f868-source.zip"
    assert archive.stat().st_size == 68171
    assert _sha256(archive) == PYSTRAY_SOURCE_SHA256

    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        assert names
        for name in names:
            path = PurePosixPath(name)
            assert not path.is_absolute()
            assert ".." not in path.parts

        info_name = next(name for name in names if name.endswith("/lib/pystray/_info.py"))
        info = bundle.read(info_name).decode("utf-8")
        assert "__version__ = (0, 19, 5)" in info

        for license_name in ("COPYING", "COPYING.LGPL"):
            archived_name = next(name for name in names if name.endswith(f"/{license_name}"))
            assert bundle.read(archived_name) == (
                ROOT / "LICENSES/python/pystray" / license_name
            ).read_bytes()

        source_prefix = info_name.split("/lib/pystray/", 1)[0] + "/lib/pystray/"
        source_modules = {
            name[len(source_prefix):]: bundle.read(name)
            for name in names
            if name.startswith(source_prefix) and name.endswith(".py")
        }

    distribution = importlib.metadata.distribution("pystray")
    installed_modules = {
        str(path).replace("\\", "/").split("pystray/", 1)[1]:
            distribution.locate_file(path).read_bytes()
        for path in distribution.files or []
        if str(path).replace("\\", "/").startswith("pystray/")
        and str(path).endswith(".py")
    }
    assert installed_modules == source_modules


def test_frozen_windows_runtime_terms_are_explicit_and_hashed() -> None:
    by_name = {component["name"]: component for component in _components()}
    assert by_name["CPython Windows runtime"]["version"] == "3.13.3 x64"
    assert by_name["OpenSSL runtime libraries"]["version"] == "3.0.16"
    assert by_name["Tcl runtime"]["version"] == "8.6.15"
    assert by_name["Tk runtime"]["version"] == "8.6.15"
    assert any(
        path.endswith("/VCRUNTIME140.dll")
        for path in by_name[
            "Microsoft Visual C++ and Universal CRT runtime files"
        ]["runtime_files_observed"]
    )

    openssl_license = ROOT / "LICENSES/runtime/OpenSSL-3.0.16-LICENSE.txt"
    assert _sha256(openssl_license) == OPENSSL_LICENSE_SHA256
    assert "Apache License" in openssl_license.read_text(encoding="utf-8")

    cpython_license = (
        ROOT / "LICENSES/runtime/CPython-3.13.3-LICENSE.txt"
    ).read_text(encoding="utf-8")
    assert "Additional Conditions for this Windows binary build" in cpython_license
    assert "Microsoft Distributable Code" in cpython_license
    assert "bzip2/libbzip2 version 1.0.8" in cpython_license
    assert "libffi - Copyright" in cpython_license
