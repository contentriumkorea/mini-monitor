"""Publish only pre-signed application assets; the fixed installer is separate."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from ai_mini_monitor.updater import verify_release_manifest

ASSETS = ("Mini-Monitor.zip", "update-v2-manifest.json", "update-v2-manifest.sig")
REPO = "contentriumkorea/mini-monitor"

def validated_assets(folder: Path, public_key: bytes) -> tuple[str, list[Path]]:
    if {p.name for p in folder.iterdir()} != set(ASSETS):
        raise ValueError("Bundle must contain exactly ZIP, v2 manifest and signature; no installer or legacy aliases")
    assets = [folder / name for name in ASSETS]
    if any(p.is_symlink() or not p.is_file() for p in assets):
        raise ValueError("Release assets must be ordinary files")
    manifest = verify_release_manifest(assets[1].read_bytes(), assets[2].read_bytes(), public_key)
    with assets[0].open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if assets[0].stat().st_size != manifest.archive_size or digest != manifest.archive_sha256:
        raise ValueError("Signed ZIP size/hash mismatch")
    return manifest.version, assets

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--notes-file", type=Path)
    args = parser.parse_args()
    key = (Path(__file__).resolve().parents[1] / "assets/update-public-key.pem").read_bytes()
    version, assets = validated_assets(args.bundle, key)
    print(json.dumps({"tag": f"v{version}", "assets": [p.name for p in assets], "publish": args.publish}))
    if args.publish:
        if not args.notes_file or not args.notes_file.is_file():
            parser.error("--notes-file is required for publication")
        tag = f"v{version}"
        # Existing releases are never clobbered; failures leave a non-public draft.
        subprocess.run(["gh", "release", "create", tag, "--repo", REPO, "--verify-tag", "--draft", "--title", f"Mini Monitor {version}", "--notes-file", str(args.notes_file)], check=True)
        subprocess.run(["gh", "release", "upload", tag, *map(str, assets), "--repo", REPO], check=True)
        subprocess.run(["gh", "release", "edit", tag, "--repo", REPO, "--draft=false", "--latest"], check=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

