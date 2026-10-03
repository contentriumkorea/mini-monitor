from __future__ import annotations

import ctypes
import os
import tempfile
from ctypes import wintypes
from pathlib import Path

from ..resources import user_data_dir


CRYPTPROTECT_UI_FORBIDDEN = 0x1
DESCRIPTION = "AI Mini Monitor OpenAI Admin Key"
ENTROPY = b"AI-Mini-Monitor:v1:OpenAI-Admin-Key"


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DATA_BLOB, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _ensure_windows() -> None:
    if os.name != "nt":
        raise OSError("Windows DPAPI is only available on Windows")


def protect(plaintext: bytes) -> bytes:
    _ensure_windows()
    source, source_buffer = _blob(plaintext)
    entropy, entropy_buffer = _blob(ENTROPY)
    output = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        DESCRIPTION,
        ctypes.byref(entropy),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        _ = (source_buffer, entropy_buffer)


def unprotect(ciphertext: bytes) -> bytes:
    _ensure_windows()
    source, source_buffer = _blob(ciphertext)
    entropy, entropy_buffer = _blob(ENTROPY)
    output = DATA_BLOB()
    description = wintypes.LPWSTR()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        ctypes.byref(description),
        ctypes.byref(entropy),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        if description:
            kernel32.LocalFree(description)
        _ = (source_buffer, entropy_buffer)


class DPAPISecretStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (user_data_dir() / "secrets" / "openai_admin_key.dpapi")

    def set(self, secret: str) -> None:
        if not secret or not secret.strip():
            raise ValueError("secret must not be empty")
        encrypted = protect(secret.strip().encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def get(self) -> str | None:
        if not self.path.exists():
            return None
        return unprotect(self.path.read_bytes()).decode("utf-8")

    def delete(self) -> bool:
        if not self.path.exists():
            return False
        self.path.unlink()
        return True

    def configured(self) -> bool:
        return self.path.is_file() and self.path.stat().st_size > 0

