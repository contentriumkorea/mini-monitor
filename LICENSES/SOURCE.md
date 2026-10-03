# Runtime and Python license copies

This directory contains redistributable license and notice texts copied from the exact audited build environment. `MANIFEST.json` records every license/notice file's component, version, size, and SHA-256.

- `python/`: license files installed by the pinned distributions in `.venv`; `pyserial/LICENSE.txt` is from the official `v3.5` tag.
- `runtime/CPython-3.13.3-LICENSE.txt`: copied from the official installed CPython 3.13.3 x64 Windows runtime. This is the complete Windows binary license, including Microsoft Distributable Code conditions and bundled third-party notices.
- `runtime/OpenSSL-3.0.16-LICENSE.txt`: byte-exact `LICENSE.txt` from official tag `openssl-3.0.16`, commit `fa1e5dfb142bb1c26c3c38a10aafa7a095df52e5`; the onedir contains `libcrypto-3.dll` and `libssl-3.dll` reporting 3.0.16.
- `runtime/Tcl-8.6-license.terms`: byte-exact official Tcl `core-8-6-15` license, commit `1a98213e55128c8a2136379fedfaef0c8e15320b`.
- `runtime/Tk-8.6-license.terms`: copied from the Tk 8.6.15 runtime shipped with CPython 3.13.3; after line-ending normalization it is identical to official tag `core-8-6-15`, commit `3c4a7176319e29a790ff82c80e3c3ff74f523ffb`.

Sources:

- https://github.com/pyserial/pyserial/tree/v3.5
- https://github.com/python/cpython/tree/6280bb547840b609feedb78887c6491af75548e8
- https://github.com/openssl/openssl/tree/fa1e5dfb142bb1c26c3c38a10aafa7a095df52e5
- https://github.com/tcltk/tcl/tree/1a98213e55128c8a2136379fedfaef0c8e15320b
- https://github.com/tcltk/tk/tree/3c4a7176319e29a790ff82c80e3c3ff74f523ffb
