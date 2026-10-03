# SPDX-License-Identifier: GPL-3.0-or-later

"""Verify pinned installed versions and every available wheel RECORD hash."""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements.lock"


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def expected_versions() -> dict[str, str]:
    expected: dict[str, str] = {}
    for raw in LOCK.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line:
            raise AssertionError(f"unlocked requirement: {line}")
        name, version = line.split("==", 1)
        expected[normalized(name)] = version
    return expected


def verify_environment() -> tuple[int, int]:
    distributions = {
        normalized(distribution.metadata["Name"]): distribution
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }
    verified_files = 0
    expected = expected_versions()
    for name, version in expected.items():
        distribution = distributions.get(name)
        if distribution is None:
            raise AssertionError(f"missing pinned distribution: {name}=={version}")
        if distribution.version != version:
            raise AssertionError(
                f"version drift for {name}: expected {version}, got {distribution.version}"
            )
        hashed_for_distribution = 0
        for package_path in distribution.files or ():
            recorded = package_path.hash
            if recorded is None or recorded.mode != "sha256":
                continue
            path = Path(distribution.locate_file(package_path))
            if not path.is_file():
                raise AssertionError(f"missing installed file: {path}")
            actual = base64.urlsafe_b64encode(
                hashlib.sha256(path.read_bytes()).digest()
            ).rstrip(b"=").decode("ascii")
            if actual != recorded.value:
                raise AssertionError(f"RECORD hash mismatch: {path}")
            hashed_for_distribution += 1
            verified_files += 1
        if hashed_for_distribution == 0:
            raise AssertionError(f"no RECORD hashes available for {name}")
    return len(expected), verified_files


def main() -> int:
    distributions, files = verify_environment()
    print(f"Verified {distributions} pinned distributions and {files} RECORD hashes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
