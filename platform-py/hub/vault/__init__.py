"""AES-GCM envelope vault (format compatible with the previous hub version)."""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, cast

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hub.models import KeyringFile
from hub.vault.crypto import (
    _B64,
    CHUNK,
    DEFAULT_KEY_FILE,
    KEYRING_FILE,
    KEYRING_RELEASE,
    PREFIX,
    _aad_dek,
    _aad_protected,
    _open,
    _public,
    _seal,
    _unb64,
    fingerprint,
    is_encrypted_file,
    is_sealed,
    parse_hex_key,
    read_key_file,
    secure_remove,
    verify_signature,
    write_key_file,
    write_private,
)
from hub.vault.files import decrypt_file, encrypt_file

_META_AAD = b"hub-vault:keyring-meta:v2"
_SEED_AAD = b"hub-vault:audit-seed:v2"


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
            "audit_seed": self._wrap(seed, _SEED_AAD),
            "audit_public_keys": [_public(seed)],
            "protected": {},
            "sealed_meta": self._sealed_meta(self._meta),
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
        self._audit = self._unwrap(self._file["audit_seed"], b"hub-vault:audit-seed" if legacy else _SEED_AAD)
        if legacy:
            self._upgrade_legacy()
        else:
            self._meta = json.loads(self._unwrap(self._file["sealed_meta"], _META_AAD))
        self._check_meta()

    def _sealed_meta(self, meta: dict[str, Any]) -> str:
        return self._wrap(json.dumps(meta, separators=(",", ":")).encode(), _META_AAD)

    def _upgrade_legacy(self) -> None:
        """Release 1 keyring: re-seals the protected metadata under generation-bound AADs and persists."""
        self._meta = {"release": 2, "keyring_id": "kr-" + secrets.token_hex(8), "generation": 0, "names": {}}
        protected: dict[str, str] = {}
        for name, sealed in self._file.get("protected", {}).items():
            plain = self._unwrap(sealed, f"hub-vault:meta:{name}".encode())
            self._meta["generation"] += 1
            gen = self._meta["generation"]
            self._meta["names"][name] = gen
            protected[name] = self._wrap(plain, _aad_protected(name, gen))
        self._file["protected"] = protected
        self._file["audit_seed"] = self._wrap(self._audit, _SEED_AAD)
        self._file["version"] = 2
        self._file["sealed_meta"] = self._sealed_meta(self._meta)
        self.upgraded_legacy = True
        self._persist()

    def _check_meta(self) -> None:
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
            file["audit_seed"] = self._wrap(self._audit, _SEED_AAD)
            for name, value in protected.items():
                gen = self._meta["names"][name]
                if value is not None:
                    file["protected"][name] = self._wrap(value.encode(), _aad_protected(name, gen))
            file["sealed_meta"] = self._sealed_meta(self._meta)
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
        self._file["sealed_meta"] = self._sealed_meta(meta)
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
        dek = self.active_dek
        return encrypt_file(dek, self._deks[dek], src, dst, aad)

    def decrypt_file(self, src: Path, dst: Path, aad: str) -> int:
        return decrypt_file(self._deks, src, dst, aad)


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
