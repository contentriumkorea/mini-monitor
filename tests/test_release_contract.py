"""Public release naming, version, and immutable update resources."""

from __future__ import annotations

import hashlib
import re
import tomllib
from pathlib import Path

from ai_mini_monitor import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_release_versions_and_program_names_match() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    spec = (ROOT / "AI-Mini-Monitor.spec").read_text(encoding="utf-8")

    assert project["project"]["version"] == __version__ == "0.2.0"
    assert re.search(r'console=False,.*?name="Mini-Monitor"', spec, re.DOTALL) or re.search(
        r'name="Mini-Monitor".*?console=False,', spec, re.DOTALL
    )
    assert re.search(r'name="Mini-Monitor-CLI".*?console=True,', spec, re.DOTALL)
    assert re.search(r'COLLECT\(.*?name="Mini-Monitor"', spec, re.DOTALL)


def test_release_key_and_helper_are_packaged_resources() -> None:
    spec = (ROOT / "AI-Mini-Monitor.spec").read_text(encoding="utf-8")
    build = (ROOT / "scripts/Build.ps1").read_text(encoding="utf-8")
    public_key = (ROOT / "assets/update-public-key.pem").read_bytes()

    assert hashlib.sha256(public_key).hexdigest() == (
        "f5a46fe4a0b5992a0dd0c7592c87001b60228644ad4d6fc073d403ea95f43598"
    )
    assert '"scripts/Apply-Update.ps1"' in spec
    assert 'add_tree(datas, "assets", "assets")' in spec
    assert '"scripts",' in build
    assert '"assets",' in build


def test_release_script_names_use_new_distribution_only() -> None:
    for relative in (
        "scripts/Build.ps1",
        "scripts/Run.ps1",
        "scripts/Diagnose.ps1",
        "scripts/Render-Previews.ps1",
        "scripts/Set-OpenAIKey.ps1",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "dist\\Mini-Monitor" in text or relative == "scripts/Build.ps1"
        assert "AI-Mini-Monitor.exe" not in text
        assert "AI-Mini-Monitor-CLI.exe" not in text


def test_build_checks_release_contract_before_and_after_freeze() -> None:
    build = (ROOT / "scripts/Build.ps1").read_text(encoding="utf-8")
    assert build.count('"tests\\test_release_contract.py"') == 2
    assert build.count('"tests\\test_release_archive.py"') == 2


def test_corresponding_source_contains_root_readme() -> None:
    build = (ROOT / "scripts/Build.ps1").read_text(encoding="utf-8")
    assert build.count('"README.md"') == 3
