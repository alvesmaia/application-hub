"""Vault primitives: AES-GCM sealing, key files, private writes and signature checks."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import stat
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "vault:v1:"
KEYRING_FILE = "keyring.json"
DEFAULT_KEY_FILE = "master.key"
KEYRING_RELEASE = 2
CHUNK = 1 << 20
FILE_MAGIC = b"HUBENC1\n"
_B64 = base64.b64encode


def _unb64(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


def _hex(raw: bytes) -> str:
    return raw.hex()


def _seal(key: bytes, plain: bytes, aad: bytes) -> bytes:
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, plain, aad)


def _open(key: bytes, sealed: bytes, aad: bytes) -> bytes:
    if len(sealed) < 28:
        raise ValueError("ciphertext too short")
    return AESGCM(key).decrypt(sealed[:12], sealed[12:], aad)


def fingerprint(key: bytes) -> str:
    if len(key) != 32:
        raise ValueError("master key must be 32 bytes")
    return hashlib.sha256(b"hub-vault-kek:" + key).digest()[:8].hex()


def parse_hex_key(text: str) -> bytes:
    if len(text) != 64 or any(c not in "0123456789abcdefABCDEF" for c in text):
        raise ValueError("invalid master key: expected 64 hexadecimal characters (256 bits)")
    try:
        return bytes.fromhex(text)
    except ValueError as exc:
        raise ValueError("invalid master key: expected 64 hexadecimal characters (256 bits)") from exc


def read_key_file(path: Path) -> bytes:
    return parse_hex_key(path.read_text(encoding="utf-8").strip())


def write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp-vault")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _private_stream(path: Path) -> Any:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    if os.name == "posix":
        os.chmod(path, 0o600)
    return os.fdopen(fd, "wb")


def write_key_file(path: Path, key: bytes) -> None:
    if len(key) != 32:
        raise ValueError("master key must be 32 bytes")
    write_private(path, key.hex().encode() + b"\n")


def secure_remove(path: Path) -> None:
    if stat.S_ISREG(path.lstat().st_mode):
        with path.open("r+b") as f:
            remaining = path.stat().st_size
            while remaining:
                n = min(remaining, 65536)
                f.write(bytes(n))
                remaining -= n
            f.flush()
            os.fsync(f.fileno())
    path.unlink()


def is_sealed(value: str) -> bool:
    return value.startswith(PREFIX)


def is_encrypted_file(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(8) == FILE_MAGIC
    except OSError:
        return False


def verify_signature(public_b64: str, message: bytes, signature_b64: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(_unb64(public_b64)).verify(_unb64(signature_b64), message)
        return True
    except (ValueError, TypeError):
        return False
    except Exception:
        return False


def _aad_dek(id: str) -> bytes:
    return f"hub-vault:dek:{id}".encode()


def _aad_protected(name: str, generation: int) -> bytes:
    return f"hub-vault:meta:v2:{name}:{generation}".encode()


def _public(seed: bytes) -> str:
    key = Ed25519PrivateKey.from_private_bytes(seed)
    return _B64(
        key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
