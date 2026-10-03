# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "AI-Mini-Monitor.spec"
DPI_AWARE_NAMESPACE = "http://schemas.microsoft.com/SMI/2005/WindowsSettings"
DPI_AWARENESS_NAMESPACE = "http://schemas.microsoft.com/SMI/2016/WindowsSettings"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def current_build_input_fingerprint() -> str:
    candidates: set[Path] = set()
    for directory in (
        "src",
        "tests",
        "scripts",
        "assets",
        "previews",
        "third_party",
        "docs",
        "LICENSES",
    ):
        root = ROOT / directory
        if root.is_dir():
            candidates.update(
                path
                for path in root.rglob("*")
                if path.is_file()
                and path.suffix != ".pyc"
                and "__pycache__" not in path.parts
                and not any(part.endswith(".egg-info") for part in path.parts)
            )
    for relative in (
        "AI-Mini-Monitor.spec",
        "pyproject.toml",
        "requirements.lock",
        "config.example.json",
        "LICENSE",
        "LICENSE.txt",
        "COPYING",
        "NOTICE",
        "THIRD_PARTY_NOTICES.md",
        "THIRD_PARTY_NOTICES.txt",
        "THIRD_PARTY_COMPONENTS.json",
        "README_KO.md",
        "README.md",
        "DEVICE_BENCHMARK.md",
        "TEST_RESULTS.md",
        "SOURCE-OFFER.md",
        "scripts/Build.ps1",
    ):
        path = ROOT / relative
        if path.is_file():
            candidates.add(path)
    lines = []
    for path in sorted(
        candidates,
        key=lambda item: item.relative_to(ROOT).as_posix(),
    ):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(ROOT).as_posix()}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def test_spec_builds_two_executables_in_one_noupx_onedir() -> None:
    text = SPEC.read_text(encoding="utf-8")
    assert "from ai_mini_monitor.cli import main" in text
    assert '"build" / "pyinstaller" / "_ai_mini_monitor_entry.py"' in text
    assert text.count("Analysis(") == 1
    assert text.count("PYZ(") == 1
    assert text.count("EXE(") == 2
    assert text.count("COLLECT(") == 1
    assert re.search(
        r'name="Mini-Monitor".*?upx=False,.*?console=False,',
        text,
        re.DOTALL,
    )
    assert re.search(
        r'name="Mini-Monitor-CLI".*?upx=False,.*?console=True,',
        text,
        re.DOTALL,
    )
    assert 'contents_directory="_internal"' in text
    assert 'name="Mini-Monitor",' in text
    assert "onefile" not in text.casefold()


def test_spec_is_asinvoker_and_collects_required_runtime_content() -> None:
    text = SPEC.read_text(encoding="utf-8")
    assert 'level="asInvoker"' in text
    assert text.count("uac_admin=False") == 2
    assert text.count("uac_uiaccess=False") == 2
    for required in (
        '"assets"',
        '"previews"',
        '"third_party/source"',
        '"third_party/librehardwaremonitor"',
        '"third_party/turing-smart-screen-python"',
        '"config.example.json"',
        '"THIRD_PARTY_NOTICES.md"',
        '"THIRD_PARTY_COMPONENTS.json"',
        '"README_KO.md"',
        '"docs"',
        '"LICENSES"',
    ):
        assert required in text
    for hidden_import in (
        '"_tkinter"',
        '"clr"',
        '"clr_loader"',
        '"PIL.ImageTk"',
        '"pythonnet"',
        '"pystray._win32"',
        '"serial.tools.list_ports_windows"',
        '"tkinter"',
    ):
        assert hidden_import in text


def test_build_script_pins_runtime_cleans_only_project_children_and_hashes() -> None:
    text = read("scripts/Build.ps1")
    assert '"3.13.3|64bit|6.22.0"' in text
    assert "PYTHONHASHSEED" in text
    assert "SOURCE_DATE_EPOCH" in text
    assert "Assert-ProjectChildPath" in text
    assert "Get-BuildInputFingerprint" in text
    assert "[StringComparer]::Ordinal" in text
    assert "[Array]::Sort($relativePaths" in text
    assert "Build inputs changed during PyInstaller analysis" in text
    assert "Remove-Item -LiteralPath $Candidate -Recurse -Force" in text
    assert "-m PyInstaller" in text
    assert "--clean" in text
    assert "--noconfirm" in text
    assert "--log-level WARN" in text
    assert "AI-Mini-Monitor.spec" in text
    assert "SHA256SUMS.txt" in text
    assert "BUILD-INFO.json" in text
    assert "tests\\test_licenses.py" in text
    assert "tests\\test_environment_integrity.py" in text
    assert "Fresh packaging artifact validation failed." in text
    assert "Build inputs changed during final artifact validation." in text
    assert text.count('"tests\\test_packaging_config.py"') >= 2
    assert "--onefile" not in text
    assert "--upx" not in text


def test_helper_scripts_route_noninteractive_work_through_console_exe() -> None:
    run = read("scripts/Run.ps1")
    diagnose = read("scripts/Diagnose.ps1")
    key = read("scripts/Set-OpenAIKey.ps1")
    previews = read("scripts/Render-Previews.ps1")

    assert "Mini-Monitor.exe" in run
    assert "Mini-Monitor-CLI.exe" in run
    assert "Mini-Monitor-CLI.exe" in diagnose
    assert "--diagnose" in diagnose
    assert "never opens" in diagnose
    assert "Mini-Monitor-CLI.exe" in key
    assert "--set-openai-key" in key
    assert "Read-Host" not in key
    assert "Mini-Monitor-CLI.exe" in previews
    assert "--render-previews" in previews


def _extract_manifest(executable: Path) -> str:
    pefile = pytest.importorskip("pefile")
    pe = pefile.PE(str(executable), fast_load=False)
    try:
        for resource_type in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            if resource_type.id != 24:  # RT_MANIFEST
                continue
            for resource_id in resource_type.directory.entries:
                for language in resource_id.directory.entries:
                    structure = language.data.struct
                    raw = pe.get_memory_mapped_image()[
                        structure.OffsetToData : structure.OffsetToData
                        + structure.Size
                    ]
                    return raw.decode("utf-8-sig", errors="replace")
    finally:
        pe.close()
    raise AssertionError(f"embedded manifest not found in {executable}")


def _assert_per_monitor_v2_manifest(manifest: str) -> None:
    root = ET.fromstring(manifest)
    legacy = root.find(f".//{{{DPI_AWARE_NAMESPACE}}}dpiAware")
    modern = root.find(f".//{{{DPI_AWARENESS_NAMESPACE}}}dpiAwareness")

    assert legacy is not None
    assert (legacy.text or "").strip().casefold() == "true/pm"
    assert modern is not None
    assert [
        value.strip().casefold()
        for value in (modern.text or "").split(",")
        if value.strip()
    ] == ["permonitorv2", "permonitor"]


def test_spec_declares_per_monitor_v2_with_legacy_fallback() -> None:
    match = re.search(
        r'WINDOWS_MANIFEST\s*=\s*"""(.*?)"""',
        SPEC.read_text(encoding="utf-8"),
        flags=re.DOTALL,
    )
    assert match is not None
    _assert_per_monitor_v2_manifest(match.group(1))


def test_built_artifact_layout_and_pe_subsystems_when_present() -> None:
    artifact = ROOT / "dist" / "Mini-Monitor"
    # COLLECT creates the directory before all binaries have arrived.  The
    # manifest is written by Build.ps1 only after the onedir is complete, so
    # concurrent source-test runs must not inspect a half-built artifact.
    build_info_path = artifact / "BUILD-INFO.json"
    if (
        not artifact.is_dir()
        or not (artifact / "SHA256SUMS.txt").is_file()
        or not build_info_path.is_file()
    ):
        pytest.skip("packaging artifact has not completed yet")
    build_info = json.loads(build_info_path.read_text(encoding="utf-8"))
    if build_info.get("input_fingerprint") != current_build_input_fingerprint():
        pytest.skip("packaging artifact was built from an older source snapshot")

    desktop = artifact / "Mini-Monitor.exe"
    cli = artifact / "Mini-Monitor-CLI.exe"
    assert desktop.is_file()
    assert cli.is_file()
    assert (artifact / "_internal/assets/fonts/Inter-Variable.ttf").is_file()
    assert (artifact / "_internal/assets/fonts/JetBrainsMono-Variable.ttf").is_file()
    assert (artifact / "_internal/assets/update-public-key.pem").is_file()
    assert (artifact / "_internal/scripts/Apply-Update.ps1").is_file()
    assert (
        artifact
        / "_internal/third_party/librehardwaremonitor/LibreHardwareMonitorLib.dll"
    ).is_file()
    assert build_info == {
        "schema": 1,
        "input_fingerprint": current_build_input_fingerprint(),
        "python": "3.13.3",
        "architecture": "64bit",
        "pyinstaller": "6.22.0",
        "source_date_epoch": "1704067200",
        "layout": "onedir",
    }

    for optional in (
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "THIRD_PARTY_COMPONENTS.json",
        "README_KO.md",
        "DEVICE_BENCHMARK.md",
        "TEST_RESULTS.md",
        "SOURCE-OFFER.md",
        "config.example.json",
    ):
        if (ROOT / optional).is_file():
            assert (artifact / optional).is_file()
    assert (artifact / "LICENSES/runtime/CPython-3.13.3-LICENSE.txt").is_file()
    preview_names = {
        "normal.png",
        "zero.png",
        "hundred.png",
        "temperature_warning.png",
        "memory_99.png",
        "ai_not_configured.png",
        "ai_delayed.png",
        "reconnecting.png",
        "disconnected.png",
    }
    assert preview_names <= {path.name for path in (artifact / "previews").glob("*.png")}
    assert (artifact / "previews/manifest.json").is_file()
    assert (artifact / "_internal/previews/manifest.json").is_file()
    assert (
        artifact
        / "_internal/third_party/turing-smart-screen-python/LICENSE"
    ).is_file()
    assert (artifact / "source/src/ai_mini_monitor/controller.py").is_file()
    assert (artifact / "source/src/ai_mini_monitor/desktop_session.py").is_file()
    assert (artifact / "source/src/ai_mini_monitor/ui/setup.py").is_file()
    assert (artifact / "source/src/ai_mini_monitor/ui/overlay.py").is_file()
    assert (artifact / "source/src/ai_mini_monitor/ui/layered_window.py").is_file()
    assert (artifact / "source/src/ai_mini_monitor/ai/codex_usage.py").is_file()
    assert (artifact / "source/tests/test_overlay.py").is_file()
    assert (artifact / "source/tests/test_layered_window.py").is_file()
    assert (artifact / "source/tests/test_tray.py").is_file()
    assert (artifact / "source/scripts/Build.ps1").is_file()
    assert (artifact / "source/LICENSES/MANIFEST.json").is_file()
    assert (artifact / "source/previews/manifest.json").is_file()
    assert not any(
        path.name == "__pycache__"
        or path.suffix == ".pyc"
        or path.name.endswith(".egg-info")
        for path in (artifact / "source").rglob("*")
    )

    manifest_entries: dict[str, str] = {}
    for line in (artifact / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        assert relative not in manifest_entries
        manifest_entries[relative] = digest
    packaged_files = {
        path.relative_to(artifact).as_posix(): path
        for path in artifact.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS.txt"
    }
    assert set(manifest_entries) == set(packaged_files)
    for relative, path in packaged_files.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest_entries[relative]

    pefile = pytest.importorskip("pefile")
    desktop_pe = pefile.PE(str(desktop), fast_load=True)
    cli_pe = pefile.PE(str(cli), fast_load=True)
    try:
        assert desktop_pe.OPTIONAL_HEADER.Subsystem == 2  # Windows GUI
        assert cli_pe.OPTIONAL_HEADER.Subsystem == 3  # Windows console
        assert not any(section.Name.rstrip(b"\0").startswith(b"UPX") for section in desktop_pe.sections)
        assert not any(section.Name.rstrip(b"\0").startswith(b"UPX") for section in cli_pe.sections)
    finally:
        desktop_pe.close()
        cli_pe.close()
    for executable in (desktop, cli):
        embedded_manifest = _extract_manifest(executable)
        assert "asInvoker" in embedded_manifest
        _assert_per_monitor_v2_manifest(embedded_manifest)
