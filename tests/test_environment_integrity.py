# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_environment import expected_versions, verify_environment  # noqa: E402


def test_pinned_environment_versions_and_record_hashes() -> None:
    distributions, files = verify_environment()
    assert distributions == len(expected_versions()) == 24
    assert files > 500
