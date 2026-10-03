# turing-smart-screen-python provenance

- Upstream project: `https://github.com/mathoudebine/turing-smart-screen-python`
- Pinned upstream commit: `262a28a3ab615f2047c0bf44afc482cc341c465c`
- Upstream protocol source: `https://github.com/mathoudebine/turing-smart-screen-python/blob/262a28a3ab615f2047c0bf44afc482cc341c465c/library/lcd/lcd_comm_rev_a.py`
- Upstream license at that commit: GNU General Public License version 3 or later (`GPL-3.0-or-later`)
- Upstream copyright: Copyright (C) 2021 Matthieu Houdebine (mathoudebine)

`src/ai_mini_monitor/transport/protocol_rev_a.py` is a modified derivative of the pinned upstream
`library/lcd/lcd_comm_rev_a.py`. The local derivative retains only the rev-A brightness, screen-on,
orientation, and bitmap command encodings needed by the verified 3.5-inch startup/display path,
plus the RGB-to-RGB565 conversion concepts. It was rewritten as a minimal allowlist with a
brightness safety cap and strict bounds, payload-length, pixel-format, and chunk validation. The
local file carries an SPDX notice, the upstream copyright notice, the pinned commit, and a dated
modification notice. Reset, hello, screen-off, clear, firmware, EEPROM, and generic raw command APIs
are intentionally not exposed.

The project and its combined distribution are licensed `GPL-3.0-or-later`. Separately licensed
third-party libraries and fonts retain their own license terms; see `THIRD_PARTY_NOTICES.md` and
`THIRD_PARTY_COMPONENTS.json`.

The complete local preferred-form source is this repository. Exact upstream source is available at
the pinned URL above. `LICENSE`, `COPYRIGHT`, and `AUTHORS` in this directory are unmodified copies
from that upstream commit.

No device-vendor executable, library, package, or proprietary source code is included, inventoried,
or reused by this provenance record.
