# Corresponding source

AI Mini Monitor is distributed under GPL-3.0-or-later. The complete preferred source for modification, tests, build scripts, pinned dependency list, protocol attribution, and third-party source materials is included in the distribution's `source/` directory.

The installed application does not contain or reuse code from the device manufacturer's executable or DLLs. Protocol provenance is documented in `third_party/turing-smart-screen-python/SOURCE.md`.

The unmodified complete pystray 0.19.5 source for official tag `v0.19.5` at commit `1907f8681d6d421517c63d94f425f9cdd74d0034` is included at `third_party/source/pystray-v0.19.5-1907f868-source.zip`. Its LGPL-3.0-or-later texts are under `LICENSES/python/pystray/`. The included application source and build scripts allow pystray to be replaced with a modified build and the onedir application to be re-frozen.

The onedir's other native runtime materials are unmodified dependencies. Exact source pointers, versions, observed runtime filenames, license locations, and integrity hashes are recorded in `THIRD_PARTY_COMPONENTS.json`, `THIRD_PARTY_NOTICES.md`, and `LICENSES/MANIFEST.json`. No source offer is claimed for proprietary Microsoft redistributable binaries; their applicable conditions are preserved in `LICENSES/runtime/CPython-3.13.3-LICENSE.txt`.
