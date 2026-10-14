"""Publishing must never mix the fixed bootstrap into an application release."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def module():
    path = Path(__file__).parents[1] / "scripts/publish_app_release.py"
    assert path.exists(), "Application-only publication validator is missing"
    spec = importlib.util.spec_from_file_location("publish_app_release", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def bundle(tmp_path):
    key = Ed25519PrivateKey.generate()
    payload = b"signed test payload"
    (tmp_path / "Mini-Monitor.zip").write_bytes(payload)
    manifest = dict(schema_version=1, version="1.2.3", channel="stable",
                    archive_url="https://github.com/contentriumkorea/mini-monitor/releases/download/v1.2.3/Mini-Monitor.zip",
                    archive_sha256=hashlib.sha256(payload).hexdigest(), archive_size=len(payload),
                    files=[["Mini-Monitor/file.txt", "a" * 64]])
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    (tmp_path / "update-v2-manifest.json").write_bytes(raw)
    (tmp_path / "update-v2-manifest.sig").write_bytes(key.sign(raw))
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def test_publish_exactly_three_signed_app_assets(tmp_path):
    key = bundle(tmp_path)
    version, assets = module().validated_assets(tmp_path, key)
    assert version == "1.2.3"
    assert [p.name for p in assets] == ["Mini-Monitor.zip", "update-v2-manifest.json", "update-v2-manifest.sig"]


@pytest.mark.parametrize("fault", ["installer", "unsigned", "tampered", "extra"])
def test_reject_invalid_or_mixed_publication(tmp_path, fault):
    key = bundle(tmp_path)
    if fault == "installer": (tmp_path / "Mini-Monitor-Setup.exe").write_bytes(b"exe")
    if fault == "unsigned": (tmp_path / "update-v2-manifest.sig").unlink()
    if fault == "tampered": (tmp_path / "Mini-Monitor.zip").write_bytes(b"wrong")
    if fault == "extra": (tmp_path / "update-manifest.json").write_bytes(b"legacy")
    with pytest.raises(ValueError):
        module().validated_assets(tmp_path, key)

