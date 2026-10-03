# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deterministic Windows onedir build with desktop and console entry points."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).resolve()
ENTRY_POINT = ROOT / "build" / "pyinstaller" / "_ai_mini_monitor_entry.py"
ENTRY_SOURCE = """from ai_mini_monitor.cli import main

raise SystemExit(main())
"""
# Package ``__main__.py`` uses a relative import and cannot be executed as a
# top-level frozen script.  Generate a deterministic absolute-import bootstrap
# inside the disposable PyInstaller work tree instead of altering app source.
ENTRY_POINT.parent.mkdir(parents=True, exist_ok=True)
if not ENTRY_POINT.is_file() or ENTRY_POINT.read_text(encoding="utf-8") != ENTRY_SOURCE:
    ENTRY_POINT.write_text(ENTRY_SOURCE, encoding="utf-8", newline="\n")


def add_tree(collection, relative_source, destination=None):
    source = ROOT / relative_source
    if source.is_dir():
        collection.append(
            (
                str(source),
                destination if destination is not None else relative_source,
            )
        )


def add_optional_file(collection, relative_source, destination="."):
    source = ROOT / relative_source
    if source.is_file():
        collection.append((str(source), destination))


datas = []
binaries = []

# Immutable runtime assets retain the same resource_path() layout in _MEIPASS.
add_tree(datas, "assets", "assets")
add_tree(datas, "previews", "previews")
add_tree(datas, "third_party/source", "third_party/source")
add_tree(
    datas,
    "third_party/turing-smart-screen-python",
    "third_party/turing-smart-screen-python",
)

lhm_directory = ROOT / "third_party" / "librehardwaremonitor"
if lhm_directory.is_dir():
    for source in sorted(lhm_directory.iterdir(), key=lambda item: item.name.casefold()):
        if not source.is_file():
            continue
        destination = "third_party/librehardwaremonitor"
        if source.suffix.casefold() == ".dll":
            binaries.append((str(source), destination))
        else:
            datas.append((str(source), destination))

# Distribution documents are optional while drafting, but automatically enter
# the bundle as soon as they exist in the source tree.
for filename in (
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
    "config.example.json",
    "requirements.lock",
):
    add_optional_file(datas, filename)
add_tree(datas, "docs", "docs")
add_tree(datas, "LICENSES", "LICENSES")


hiddenimports = sorted(
    set(
        [
            "_tkinter",
            "clr",
            "PIL.ImageTk",
            "psutil._pswindows",
            "pythonnet",
            "pystray",
            "pystray._win32",
            "serial.tools.list_ports_windows",
            "tkinter",
            "tkinter.ttk",
        ]
        + collect_submodules("clr_loader")
    )
)


WINDOWS_MANIFEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0"
          xmlns:asmv3="urn:schemas-microsoft-com:asm.v3">
  <assemblyIdentity version="1.0.0.0" processorArchitecture="*"
                    name="AI-Mini-Monitor" type="win32"/>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <asmv3:application>
    <asmv3:windowsSettings>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2, PerMonitor</dpiAwareness>
    </asmv3:windowsSettings>
  </asmv3:application>
</assembly>
"""


analysis = Analysis(
    [str(ENTRY_POINT)],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pystray._appindicator",
        "pystray._darwin",
        "pystray._dummy",
        "pystray._gtk",
        "pystray._xorg",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)


desktop_exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="AI-Mini-Monitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    manifest=WINDOWS_MANIFEST,
    uac_admin=False,
    uac_uiaccess=False,
    contents_directory="_internal",
)


cli_exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="AI-Mini-Monitor-CLI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    manifest=WINDOWS_MANIFEST,
    uac_admin=False,
    uac_uiaccess=False,
    contents_directory="_internal",
)


distribution = COLLECT(
    desktop_exe,
    cli_exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="AI-Mini-Monitor",
)
