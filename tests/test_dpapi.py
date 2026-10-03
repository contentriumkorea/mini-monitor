from __future__ import annotations

import os

import pytest

from ai_mini_monitor.security.dpapi import DPAPISecretStore


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_dpapi_roundtrip_does_not_store_plaintext(tmp_path) -> None:
    path = tmp_path / "openai.dpapi"
    store = DPAPISecretStore(path)
    secret = "test-admin-key-never-log-this-value"
    store.set(secret)
    encrypted = path.read_bytes()
    assert secret.encode() not in encrypted
    assert store.get() == secret
    assert store.configured()
    assert store.delete()
    assert store.get() is None

