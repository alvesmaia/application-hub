"""Cofre AES-GCM em envelope (formato compatível com a versão anterior do hub)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import struct
import time
from pathlib import Path
from typing import Any, cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from hub.models import KeyringFile

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


class Vault:
    data_dir: Path
    key_file: Path | None
    keyring_path: Path | None
    _file: dict[str, Any]
    _meta: dict[str, Any]
    _deks: dict[str, bytes]

    def __init__(self, data_dir: Path, key_file: Path | None = None, *, kek: bytes | None = None) -> None:
        self.data_dir = data_dir
        explicit_key = key_file or (
            Path(os.environ["HUB_MASTER_KEY_FILE"]) if os.getenv("HUB_MASTER_KEY_FILE") else None
        )
        self.key_file = explicit_key or data_dir / DEFAULT_KEY_FILE
        self.keyring_path = data_dir / KEYRING_FILE
        data_dir.mkdir(parents=True, exist_ok=True)
        self.generated_key = False
        if kek is None:
            try:
                kek = read_key_file(self.key_file)
            except FileNotFoundError:
                if explicit_key is not None:
                    raise
                kek = secrets.token_bytes(32)
                write_key_file(self.key_file, kek)
                self.generated_key = True
        if len(kek) != 32:
            raise ValueError("master key must be 32 bytes")
        self._kek = kek
        self.created = not self.keyring_path.exists()
        self.upgraded_legacy = False
        if self.created:
            self._new_keyring()
        else:
            self._load()

    @classmethod
    def open(cls, data_dir: Path, key_file: Path | None = None) -> Vault:
        selected = key_file or (
            Path(os.environ["HUB_MASTER_KEY_FILE"]) if os.getenv("HUB_MASTER_KEY_FILE") else None
        )
        try:
            return cls(data_dir, selected)
        except Exception as first_error:
            active = selected or data_dir / DEFAULT_KEY_FILE
            pending = active.with_suffix(".key.new")
            try:
                new_key = read_key_file(pending)
                vault = cls(data_dir, active, kek=new_key)
                os.replace(pending, active)
                return vault
            except Exception:
                raise first_error from None

    @classmethod
    def in_memory(cls, kek: bytes) -> Vault:
        obj = cls.__new__(cls)
        obj.data_dir = Path()
        obj.key_file = None
        obj.keyring_path = None
        obj.generated_key = obj.created = obj.upgraded_legacy = False
        obj._kek = kek
        obj._new_keyring(persist=False)
        return obj

    def _wrap(self, data: bytes, aad: bytes) -> str:
        return _B64(_seal(self._kek, data, aad)).decode()

    def _unwrap(self, wrapped: str, aad: bytes) -> bytes:
        return _open(self._kek, _unb64(wrapped), aad)

    def _new_keyring(self, persist: bool = True) -> None:
        dek_id = "k" + secrets.token_hex(4)
        dek, seed = secrets.token_bytes(32), secrets.token_bytes(32)
        self._deks = {dek_id: dek}
        self._meta = {"release": 2, "keyring_id": "kr-" + secrets.token_hex(8), "generation": 0, "names": {}}
        self._file = {
            "version": 2,
            "kek_fingerprint": fingerprint(self._kek),
            "active": dek_id,
            "deks": [
                {
                    "id": dek_id,
                    "wrapped": self._wrap(dek, _aad_dek(dek_id)),
                    "created_ms": time.time_ns() // 1_000_000,
                }
            ],
            "audit_seed": self._wrap(seed, b"hub-vault:audit-seed:v2"),
            "audit_public_keys": [_public(seed)],
            "protected": {},
            "sealed_meta": self._wrap(
                json.dumps(self._meta, separators=(",", ":")).encode(), b"hub-vault:keyring-meta:v2"
            ),
        }
        self._audit = seed
        self._meta_problems: dict[str, str] = {}
        if persist:
            self._persist()

    def _load(self) -> None:
        if self.keyring_path is None:
            raise ValueError("missing keyring path")
        self._file = json.loads(self.keyring_path.read_text(encoding="utf-8"))
        KeyringFile.model_validate(self._file)
        if self._file["kek_fingerprint"] != fingerprint(self._kek):
            raise ValueError("master key does not match keyring")
        self._deks = {d["id"]: self._unwrap(d["wrapped"], _aad_dek(d["id"])) for d in self._file["deks"]}
        legacy = self._file["version"] < 2
        if not legacy and not self._file.get("sealed_meta"):
            raise ValueError("release 2 keyring missing sealed metadata")
        self._audit = self._unwrap(
            self._file["audit_seed"], b"hub-vault:audit-seed" if legacy else b"hub-vault:audit-seed:v2"
        )
        if legacy:
            self._meta = {
                "release": 2,
                "keyring_id": "kr-" + secrets.token_hex(8),
                "generation": 0,
                "names": {},
            }
            protected: dict[str, str] = {}
            for name, sealed in self._file.get("protected", {}).items():
                plain = self._unwrap(sealed, f"hub-vault:meta:{name}".encode())
                self._meta["generation"] += 1
                gen = self._meta["generation"]
                self._meta["names"][name] = gen
                protected[name] = self._wrap(plain, _aad_protected(name, gen))
            self._file["protected"] = protected
            self._file["audit_seed"] = self._wrap(self._audit, b"hub-vault:audit-seed:v2")
            self._file["version"] = 2
            self._file["sealed_meta"] = self._wrap(
                json.dumps(self._meta, separators=(",", ":")).encode(), b"hub-vault:keyring-meta:v2"
            )
            self.upgraded_legacy = True
            self._persist()
        else:
            self._meta = json.loads(self._unwrap(self._file["sealed_meta"], b"hub-vault:keyring-meta:v2"))
        self._meta_problems = {}
        names = self._meta["names"]
        for name, gen in names.items():
            sealed = self._file.get("protected", {}).get(name)
            try:
                if sealed is None:
                    raise ValueError
                self._unwrap(sealed, _aad_protected(name, gen))
            except Exception:
                self._meta_problems[name] = f"absent or tampered (expected generation {gen})"
        for name in self._file.get("protected", {}):
            if name not in names:
                self._meta_problems[name] = "unregistered in sealed metadata"
        self._ignored_keys = [p for p in self._file.get("audit_public_keys", []) if p != _public(self._audit)]

    def _persist(self) -> None:
        if self.keyring_path is None:
            return
        if self.keyring_path.exists():
            prev = self.keyring_path.with_suffix(".json.prev")
            write_private(prev, self.keyring_path.read_bytes())
        file = self._file.copy()
        if not file.get("protected"):
            file.pop("protected", None)
        write_private(self.keyring_path, json.dumps(file, indent=2, ensure_ascii=False).encode())

    @property
    def kek_fingerprint(self) -> str:
        return fingerprint(self._kek)

    @property
    def active_dek(self) -> str:
        return cast(str, self._file["active"])

    @property
    def dek_count(self) -> int:
        return len(self._deks)

    def seal(self, plain: str, aad: str) -> str:
        id = self.active_dek
        return f"{PREFIX}{id}:{_B64(_seal(self._deks[id], plain.encode(), aad.encode())).decode()}"

    def open_value(self, sealed: str, aad: str) -> str:
        try:
            prefix, id, text = sealed.split(":", 2)
            if prefix != "vault" or id != "v1":
                raise ValueError
            dek_id, payload = text.split(":", 1)
            return _open(self._deks[dek_id], _unb64(payload), aad.encode()).decode()
        except Exception as exc:
            raise ValueError("sealed value tampered, misplaced, or malformed") from exc

    def rotate_data_key(self) -> str:
        id = "k" + secrets.token_hex(4)
        dek = secrets.token_bytes(32)
        old_file = self._file
        self._file = json.loads(json.dumps(self._file))
        self._deks[id] = dek
        self._file["deks"].append(
            {"id": id, "wrapped": self._wrap(dek, _aad_dek(id)), "created_ms": time.time_ns() // 1_000_000}
        )
        self._file["active"] = id
        try:
            self._persist()
        except Exception:
            self._file = old_file
            del self._deks[id]
            raise
        return id

    def rotate_master_key(self, new: bytes) -> None:
        old = self._kek
        protected = {
            name: self.protected(name) for name in self._meta["names"] if name not in self._meta_problems
        }
        self._kek = new
        original = self._file
        file = json.loads(json.dumps(self._file))
        try:
            for d in file["deks"]:
                d["wrapped"] = self._wrap(self._deks[d["id"]], _aad_dek(d["id"]))
            file["audit_seed"] = self._wrap(self._audit, b"hub-vault:audit-seed:v2")
            for name, value in protected.items():
                gen = self._meta["names"][name]
                if value is not None:
                    file["protected"][name] = self._wrap(value.encode(), _aad_protected(name, gen))
            file["sealed_meta"] = self._wrap(
                json.dumps(self._meta, separators=(",", ":")).encode(), b"hub-vault:keyring-meta:v2"
            )
            file["kek_fingerprint"] = fingerprint(new)
            self._file = file
            self._persist()
        except Exception:
            self._kek = old
            self._file = original
            raise

    def rotate_master_key_file(self, new: bytes | None = None) -> str:
        if self.key_file is None:
            raise ValueError("vault has no master key file")
        key = new or secrets.token_bytes(32)
        pending = self.key_file.with_suffix(".key.new")
        write_key_file(pending, key)
        self.rotate_master_key(key)
        os.replace(pending, self.key_file)
        return fingerprint(key)

    @property
    def signing_public_key(self) -> str:
        return _public(self._audit)

    @property
    def trusted_public_keys(self) -> list[str]:
        return [self.signing_public_key]

    @property
    def ignored_public_keys(self) -> list[str]:
        return list(getattr(self, "_ignored_keys", []))

    def sign(self, message: bytes) -> str:
        return _B64(Ed25519PrivateKey.from_private_bytes(self._audit).sign(message)).decode()

    def protected(self, name: str) -> str | None:
        if name in self._meta_problems:
            raise ValueError(f"protected metadata {name}: {self._meta_problems[name]}")
        gen = self._meta["names"].get(name)
        if gen is None:
            return None
        return self._unwrap(self._file["protected"][name], _aad_protected(name, gen)).decode()

    def set_protected(self, name: str, value: str) -> None:
        generation = self._meta["generation"] + 1
        meta = {**self._meta, "generation": generation, "names": {**self._meta["names"], name: generation}}
        original = self._file
        self._file = json.loads(json.dumps(self._file))
        self._file.setdefault("protected", {})[name] = self._wrap(
            value.encode(), _aad_protected(name, generation)
        )
        self._file["sealed_meta"] = self._wrap(
            json.dumps(meta, separators=(",", ":")).encode(), b"hub-vault:keyring-meta:v2"
        )
        old = self._meta
        self._meta = meta
        try:
            self._persist()
        except Exception:
            self._meta = old
            self._file = original
            raise
        self._meta_problems.pop(name, None)

    @property
    def generation(self) -> int:
        return cast(int, self._meta["generation"])

    @property
    def keyring_id(self) -> str:
        return cast(str, self._meta["keyring_id"])

    @property
    def protected_never_written(self) -> bool:
        return not self._meta["names"]

    @property
    def meta_problems(self) -> list[tuple[str, str]]:
        return list(self._meta_problems.items())

    def encrypt_file(self, src: Path, dst: Path, aad: str) -> int:
        file_key = secrets.token_bytes(32)
        dek = self.active_dek
        wrapped = _B64(_seal(self._deks[dek], file_key, f"hub-vault:file-key:v2:{aad}".encode())).decode()
        header = json.dumps(
            {"alg": "AES-256-GCM-STREAM", "v": 2, "dek": dek, "key": wrapped, "chunk": CHUNK},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        chunk_aad = f"{aad}|hdr:{hashlib.sha256(header).hexdigest()}".encode()
        tmp = dst.with_suffix(".part-enc")
        total = counter = 0
        out_file = _private_stream(tmp)
        try:
            with src.open("rb") as source, out_file as out:
                out.write(FILE_MAGIC + struct.pack(">I", len(header)) + header)
                chunk = source.read(CHUNK)
                while True:
                    nxt = source.read(CHUNK) if len(chunk) == CHUNK else b""
                    last = not nxt
                    nonce = bytes([int(last), 0, 0, 0]) + counter.to_bytes(8, "big")
                    ciphertext = AESGCM(file_key).encrypt(nonce, chunk, chunk_aad)
                    out.write(struct.pack(">I", len(ciphertext)) + ciphertext)
                    total += len(chunk)
                    if last:
                        break
                    chunk = nxt
                    counter += 1
                out.flush()
                os.fsync(out.fileno())
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, dst)
        return total

    def decrypt_file(self, src: Path, dst: Path, aad: str) -> int:
        tmp = dst.with_suffix(".part-dec")
        try:
            with src.open("rb") as f:
                if f.read(8) != FILE_MAGIC:
                    raise ValueError("invalid encrypted file")
                n = struct.unpack(">I", f.read(4))[0]
                if n > 4096:
                    raise ValueError("invalid header")
                hb = f.read(n)
                h = json.loads(hb)
                v2 = h.get("v") == 2
                dek = self._deks[h["dek"]]
                key_aad = f"hub-vault:file-key:v2:{aad}".encode() if v2 else b"hub-vault:file-key"
                file_key = _open(dek, _unb64(h["key"]), key_aad)
                chunk_aad = f"{aad}|hdr:{hashlib.sha256(hb).hexdigest()}".encode() if v2 else aad.encode()
                total = counter = 0
                with _private_stream(tmp) as out:
                    while True:
                        size = struct.unpack(">I", f.read(4))[0]
                        if size < 16 or size > CHUNK + 16:
                            raise ValueError("invalid chunk")
                        encrypted = f.read(size)
                        if len(encrypted) != size:
                            raise ValueError("truncated chunk")
                        nonce = bytes([0, 0, 0, 0]) + counter.to_bytes(8, "big")
                        try:
                            plain = AESGCM(file_key).decrypt(nonce, encrypted, chunk_aad)
                            last = False
                        except Exception:
                            nonce = bytes([1, 0, 0, 0]) + counter.to_bytes(8, "big")
                            plain = AESGCM(file_key).decrypt(nonce, encrypted, chunk_aad)
                            last = True
                        out.write(plain)
                        total += len(plain)
                        if last:
                            if f.read(1):
                                raise ValueError("trailing data")
                            break
                        counter += 1
                    out.flush()
                    os.fsync(out.fileno())
            os.replace(tmp, dst)
            return total
        except Exception:
            tmp.unlink(missing_ok=True)
            raise


__all__ = [
    "CHUNK",
    "DEFAULT_KEY_FILE",
    "KEYRING_FILE",
    "KEYRING_RELEASE",
    "PREFIX",
    "Vault",
    "fingerprint",
    "is_encrypted_file",
    "is_sealed",
    "parse_hex_key",
    "read_key_file",
    "secure_remove",
    "verify_signature",
    "write_key_file",
    "write_private",
]
