# Third-party notices

This notice records the audited Windows development/build environment and the files intentionally
bundled in the source tree as of 2026-08-10. The machine-readable companion is
`THIRD_PARTY_COMPONENTS.json`.

## OpenAI Codex account runtime

The release bundles the unmodified official OpenAI Codex standalone app-server
for Windows x64, pinned to [`rust-v0.161.0`](https://github.com/openai/codex/releases/tag/rust-v0.161.0).
`third_party/codex-app-server/RUNTIME.json` records the release URL, ZIP and EXE
sizes, and SHA-256 digests. The Git repository excludes the large EXE; the build
fetches and verifies it before packaging. Complete upstream source is available
at the pinned [Codex source tag](https://github.com/openai/codex/tree/rust-v0.161.0).
Codex is Apache-2.0; the full license text is in
`LICENSES/python/cryptography/LICENSE.APACHE`, and the upstream NOTICE is in
`third_party/codex-app-server/NOTICE`. Mini Monitor runs the app-server as a
separate process with an isolated application-owned `CODEX_HOME`; it does not
read any other Codex application's local credentials or sessions.

## Project license and protocol provenance

AI Mini Monitor and its combined distribution are licensed under `GPL-3.0-or-later`; the complete
terms are in `LICENSE`. `src/ai_mini_monitor/transport/protocol_rev_a.py` is a modified derivative
of `library/lcd/lcd_comm_rev_a.py` from
[turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python) commit
`262a28a3ab615f2047c0bf44afc482cc341c465c`. The pinned upstream notices and exact GPL text are in
`third_party/turing-smart-screen-python/`.

The local allowlist is restricted to brightness (capped at 50%), screen-on, orientation, and bitmap
output for the exact supported 3.5-inch identity. It does not expose a generic serial-command API.

No device-vendor executable, library, package, or proprietary source code is included, inventoried,
or reused. The protocol implementation is derived only from the cited GPL-licensed upstream project.

## Installed Python dependency closure

The following versions were actually installed in `.venv` and are reachable from the declared
runtime, build, or test dependencies. “Direct” and “transitive” describe the relationship to this
project, not whether a package is embedded in every release artifact.

| Component | Version | Relationship | License | Source |
|---|---:|---|---|---|
| altgraph | 0.17.5 | build transitive | MIT | [source](https://github.com/ronaldoussoren/altgraph) |
| cffi | 2.1.1 | runtime transitive | MIT-0 | [source](https://github.com/python-cffi/cffi) |
| clr_loader | 0.3.1 | runtime transitive | MIT | [source](https://github.com/pythonnet/clr-loader) |
| colorama | 0.4.6 | test transitive on Windows | BSD-3-Clause | [source](https://github.com/tartley/colorama) |
| coverage | 7.15.4 | test transitive | Apache-2.0 | [source](https://github.com/coveragepy/coveragepy) |
| cryptography | 50.0.2 | runtime direct | Apache-2.0 OR BSD-3-Clause | [source](https://github.com/pyca/cryptography) |
| iniconfig | 2.3.0 | test transitive | MIT | [source](https://github.com/pytest-dev/iniconfig) |
| packaging | 26.3 | build/test transitive | Apache-2.0 OR BSD-2-Clause | [source](https://github.com/pypa/packaging) |
| pefile | 2024.8.26 | build transitive on Windows | MIT | [source](https://github.com/erocarrera/pefile) |
| Pillow | 12.3.0 | runtime direct | MIT-CMU | [source](https://github.com/python-pillow/Pillow) |
| pluggy | 1.6.0 | test transitive | MIT | [source](https://github.com/pytest-dev/pluggy) |
| psutil | 7.2.2 | runtime direct | BSD-3-Clause | [source](https://github.com/giampaolo/psutil) |
| pycparser | 3.0 | runtime transitive | BSD-3-Clause | [source](https://github.com/eliben/pycparser) |
| Pygments | 2.20.0 | test transitive | BSD-2-Clause | [source](https://github.com/pygments/pygments) |
| PyInstaller | 6.22.0 | build direct | GPL-2.0-or-later WITH Bootloader-exception; runtime hooks Apache-2.0; isolated module GPL-2.0-or-later OR MIT | [source](https://github.com/pyinstaller/pyinstaller) |
| pyinstaller-hooks-contrib | 2026.6 | build transitive | GPL-2.0-or-later; runtime hooks Apache-2.0 | [source](https://github.com/pyinstaller/pyinstaller-hooks-contrib) |
| pyserial | 3.5 | runtime direct | BSD-3-Clause | [source](https://github.com/pyserial/pyserial) |
| pystray | 0.19.5 | runtime direct | LGPL-3.0-or-later | [official tag commit](https://github.com/moses-palmer/pystray/tree/1907f8681d6d421517c63d94f425f9cdd74d0034) |
| pytest | 9.1.1 | test direct | MIT | [source](https://github.com/pytest-dev/pytest) |
| pytest-cov | 7.1.0 | test direct | MIT | [source](https://github.com/pytest-dev/pytest-cov) |
| pythonnet | 3.1.0 | runtime direct | MIT | [source](https://github.com/pythonnet/pythonnet) |
| pywin32-ctypes | 0.2.3 | build transitive on Windows | BSD-3-Clause | [source](https://github.com/enthought/pywin32-ctypes) |
| setuptools | 84.0.0 | build direct | MIT | [source](https://github.com/pypa/setuptools) |
| six | 1.17.0 | runtime transitive | MIT | [source](https://github.com/benjaminp/six) |

Redistributable copies of the applicable Python license and notice files are under
`LICENSES/python/`; every copied file's size and SHA-256 is recorded in `LICENSES/MANIFEST.json`.
`pyserial` 3.5 does not install a standalone license file, so `LICENSES/python/pyserial/LICENSE.txt`
is the byte-exact text from its official `v3.5` tag. `wheel==0.47.0` is declared for isolated builds
but was not installed in the audited `.venv`, so it is not represented as an installed component.

For `pystray`, the unmodified complete source for official tag `v0.19.5` (tag object
`c587fbe535610c177d49b585264762fa06abddca`, commit
`1907f8681d6d421517c63d94f425f9cdd74d0034`) is bundled as
`third_party/source/pystray-v0.19.5-1907f868-source.zip`. Its `COPYING` and `COPYING.LGPL` are also
in `LICENSES/python/pystray/`. Together with the complete application source and build scripts, this
preserves access to corresponding source and the practical ability to replace pystray with a
modified build and re-freeze the application. PyInstaller's bootloader exception permits
distribution of applications containing its bootloader, while modifications and separately
redistributed PyInstaller files remain subject to their terms.

## Frozen Windows runtime observed in the onedir

The audited onedir was inspected by filename and Windows version metadata; these are the native
runtime materials actually observed. Their recorded license texts are copied under
`LICENSES/runtime/` and hashed in `LICENSES/MANIFEST.json`.

| Runtime material observed | Version | Applicable terms retained | Source |
|---|---:|---|---|
| `python3.dll`, `python313.dll`, Python `.pyd` modules | CPython 3.13.3 x64 | Official Windows binary `LICENSE.txt`, including Python terms, bundled third-party notices, and Microsoft Distributable Code conditions | [exact CPython tag commit](https://github.com/python/cpython/tree/6280bb547840b609feedb78887c6491af75548e8) |
| `libcrypto-3.dll`, `libssl-3.dll` | OpenSSL 3.0.16 | Apache-2.0; exact upstream `LICENSE.txt` at `LICENSES/runtime/OpenSSL-3.0.16-LICENSE.txt` | [exact OpenSSL tag commit](https://github.com/openssl/openssl/tree/fa1e5dfb142bb1c26c3c38a10aafa7a095df52e5) |
| `tcl86t.dll`, `_tcl_data/**` | Tcl 8.6.15 | Tcl/Tk license at `LICENSES/runtime/Tcl-8.6-license.terms` | [exact Tcl tag commit](https://github.com/tcltk/tcl/tree/1a98213e55128c8a2136379fedfaef0c8e15320b) |
| `tk86t.dll`, `_tk_data/**` | Tk 8.6.15 | Tcl/Tk license at `LICENSES/runtime/Tk-8.6-license.terms` | [exact Tk tag commit](https://github.com/tcltk/tk/tree/3c4a7176319e29a790ff82c80e3c3ff74f523ffb) |
| `VCRUNTIME140.dll`, `VCRUNTIME140_1.dll`, `ucrtbase.dll`, API-set DLLs | Visual C++ 14.42.34438; UCRT 10.0.26100.8521 | Microsoft Distributable Code conditions contained in the official CPython Windows license copy | [Microsoft redistributable guidance](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist) |

The CPython Windows license copy is the complete 645-line file installed with the official 3.13.3
x64 runtime, not only the shorter CPython source-tree license. It explicitly contains the Microsoft
Distributable Code restrictions and bundled notices for bzip2, libffi, Apache-2.0 material, Tcl, and
Tk. OpenSSL's exact official 3.0.16 Apache-2.0 text is additionally retained as its own file for an
unambiguous `libcrypto-3.dll` / `libssl-3.dll` mapping. No claim is made that Microsoft runtime
binaries are open source; they remain subject to Microsoft's redistribution conditions.

## Bundled fonts

| Component | Version | License | Bundled files | Source |
|---|---|---|---|---|
| Inter Variable | 4.001 (`git-66647c0bb`) | OFL-1.1 | `assets/fonts/Inter-Variable.ttf`, `assets/fonts/Inter-OFL.txt` | [google/fonts pinned commit](https://github.com/google/fonts/tree/2d85e20401920891efb7cd6272d6339685df2820/ofl/inter) |
| JetBrains Mono Variable | 2.211 | OFL-1.1 | `assets/fonts/JetBrainsMono-Variable.ttf`, `assets/fonts/JetBrainsMono-OFL.txt` | [google/fonts pinned commit](https://github.com/google/fonts/tree/2d85e20401920891efb7cd6272d6339685df2820/ofl/jetbrainsmono) |

Both full OFL texts are bundled beside the fonts. File hashes and sizes are in the JSON inventory and
`assets/fonts/SOURCE.md`.

## Bundled LibreHardwareMonitor libraries

| File/component | Version | License | Source |
|---|---:|---|---|
| `LibreHardwareMonitorLib.dll` | 0.9.6 | MPL-2.0 | [source](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/tree/3d331e3370efb858411f19511373eff65a218701) |
| `BlackSharp.Core.dll` | 1.0.7 | MPL-2.0 | [source](https://github.com/Blacktempel/BlackSharp/tree/c70b735c6cec123ee8a046ac4a0bc6c606f52cf0) |
| `DiskInfoToolkit.dll` | 1.1.2 | MPL-2.0 | [source](https://github.com/Blacktempel/DiskInfoToolkit/tree/25319eae5781e75bcf141e844ceab2afe94d40ea) |
| `HidSharp.dll` | 2.6.4 | Apache-2.0 | [source](https://github.com/SeekHisKingdom/HIDSharp) |
| `RAMSPDToolkit-NDD.dll` | 1.4.2 | MPL-2.0 | [source](https://github.com/Blacktempel/RAMSPDToolkit/tree/3b47b960e0830fef344624ad5e389675d5f0a1ce) |
| `System.Buffers.dll` | 4.6.1 | MIT | [source](https://github.com/dotnet/maintenance-packages/tree/6b84308fcb7590726e785167fe20433094e5580f) |
| `System.Memory.dll` | 4.6.3 | MIT | [source](https://github.com/dotnet/maintenance-packages/tree/f62ca0009c81759a669cfd5dd3a4f55fd2896478) |
| `System.Numerics.Vectors.dll` | 4.6.1 | MIT | [source](https://github.com/dotnet/maintenance-packages/tree/6b84308fcb7590726e785167fe20433094e5580f) |
| `System.Runtime.CompilerServices.Unsafe.dll` | 6.1.2 | MIT | [source](https://github.com/dotnet/maintenance-packages/tree/f62ca0009c81759a669cfd5dd3a4f55fd2896478) |

The unmodified LibreHardwareMonitor 0.9.6 source archive is bundled at
`third_party/source/LibreHardwareMonitor-v0.9.6-source.zip`. It also preserves source for the
upstream tree's Aga.Controls code (BSD-3-Clause) and the relevant PawnIO integration; the upstream
third-party notices, including PawnIO.Modules' LGPL-2.1 terms, are retained at
`third_party/librehardwaremonitor/THIRD-PARTY-NOTICES.txt`. The full MPL-2.0 text is at
`third_party/librehardwaremonitor/LICENSE`.

Only the listed managed libraries are runtime bundle inputs. No standalone GUI executable,
installer, driver, service executable, updater, or device-vendor binary is used by the application.
The retained source archive is corresponding-source material and is never extracted or executed at
runtime.
