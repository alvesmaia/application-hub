"""Streamed file encryption (AES-256-GCM in chunks; the last chunk is marked in the nonce)."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import struct
from pathlib import Path
from typing import IO, Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from hub.vault.crypto import _B64, CHUNK, FILE_MAGIC, _open, _private_stream, _seal, _unb64


def encrypt_file(dek_id: str, dek: bytes, src: Path, dst: Path, aad: str) -> int:
    file_key = secrets.token_bytes(32)
    wrapped = _B64(_seal(dek, file_key, f"hub-vault:file-key:v2:{aad}".encode())).decode()
    header = json.dumps(
        {"alg": "AES-256-GCM-STREAM", "v": 2, "dek": dek_id, "key": wrapped, "chunk": CHUNK},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    chunk_aad = f"{aad}|hdr:{hashlib.sha256(header).hexdigest()}".encode()
    tmp = dst.with_suffix(".part-enc")
    out_file = _private_stream(tmp)
    try:
        with src.open("rb") as source, out_file as out:
            out.write(FILE_MAGIC + struct.pack(">I", len(header)) + header)
            total = _encrypt_chunks(source, out, AESGCM(file_key), chunk_aad)
            out.flush()
            os.fsync(out.fileno())
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, dst)
    return total


def _encrypt_chunks(source: IO[bytes], out: IO[bytes], gcm: AESGCM, chunk_aad: bytes) -> int:
    total = counter = 0
    chunk = source.read(CHUNK)
    while True:
        nxt = source.read(CHUNK) if len(chunk) == CHUNK else b""
        last = not nxt
        nonce = bytes([int(last), 0, 0, 0]) + counter.to_bytes(8, "big")
        ciphertext = gcm.encrypt(nonce, chunk, chunk_aad)
        out.write(struct.pack(">I", len(ciphertext)) + ciphertext)
        total += len(chunk)
        if last:
            return total
        chunk = nxt
        counter += 1


def decrypt_file(deks: dict[str, bytes], src: Path, dst: Path, aad: str) -> int:
    tmp = dst.with_suffix(".part-dec")
    try:
        with src.open("rb") as f:
            gcm, chunk_aad = _read_header(f, deks, aad)
            with _private_stream(tmp) as out:
                total = _decrypt_chunks(f, out, gcm, chunk_aad)
                out.flush()
                os.fsync(out.fileno())
        os.replace(tmp, dst)
        return total
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _read_header(f: IO[bytes], deks: dict[str, bytes], aad: str) -> tuple[AESGCM, bytes]:
    """Validates the header and returns the file cipher and the chunk AAD (v1 files had weaker AADs)."""
    if f.read(8) != FILE_MAGIC:
        raise ValueError("invalid encrypted file")
    n = struct.unpack(">I", f.read(4))[0]
    if n > 4096:
        raise ValueError("invalid header")
    hb = f.read(n)
    h: dict[str, Any] = json.loads(hb)
    v2 = h.get("v") == 2
    dek = deks[h["dek"]]
    key_aad = f"hub-vault:file-key:v2:{aad}".encode() if v2 else b"hub-vault:file-key"
    file_key = _open(dek, _unb64(h["key"]), key_aad)
    chunk_aad = f"{aad}|hdr:{hashlib.sha256(hb).hexdigest()}".encode() if v2 else aad.encode()
    return AESGCM(file_key), chunk_aad


def _decrypt_chunks(f: IO[bytes], out: IO[bytes], gcm: AESGCM, chunk_aad: bytes) -> int:
    total = counter = 0
    while True:
        plain, last = _open_chunk(gcm, counter, _read_chunk(f), chunk_aad)
        out.write(plain)
        total += len(plain)
        if last:
            if f.read(1):
                raise ValueError("trailing data")
            return total
        counter += 1


def _read_chunk(f: IO[bytes]) -> bytes:
    size = struct.unpack(">I", f.read(4))[0]
    if size < 16 or size > CHUNK + 16:
        raise ValueError("invalid chunk")
    encrypted = f.read(size)
    if len(encrypted) != size:
        raise ValueError("truncated chunk")
    return encrypted


def _open_chunk(gcm: AESGCM, counter: int, encrypted: bytes, chunk_aad: bytes) -> tuple[bytes, bool]:
    """(plaintext, is_last): the last chunk is the one that only opens with the final-chunk nonce flag."""
    nonce = bytes([0, 0, 0, 0]) + counter.to_bytes(8, "big")
    try:
        return gcm.decrypt(nonce, encrypted, chunk_aad), False
    except Exception:
        nonce = bytes([1, 0, 0, 0]) + counter.to_bytes(8, "big")
        return gcm.decrypt(nonce, encrypted, chunk_aad), True
