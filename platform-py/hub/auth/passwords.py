"""Password hashing and policy, e-mail normalization and the hash used for session/token ids."""

from __future__ import annotations

import hashlib
import unicodedata

from argon2 import PasswordHasher, exceptions
from argon2.low_level import Type

PH = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16, type=Type.ID)
DUMMY_HASH = PH.hash("senha-que-nunca-confere")


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def policy(password: str) -> str | None:
    n = len(password)
    if n < 10:
        return "A senha deve ter pelo menos 10 caracteres."
    if n > 128:
        return "A senha deve ter no máximo 128 caracteres."
    if any(unicodedata.category(c) == "Cc" for c in password):
        return "A senha não pode ter caracteres de controle."
    if (
        sum(
            (
                any(c.islower() for c in password),
                any(c.isupper() for c in password),
                any(c.isascii() and c.isdigit() for c in password),
                any(not c.isalnum() for c in password),
            )
        )
        < 2
    ):
        return "Combine letras com números ou símbolos."
    return None


def verify(password: str, encoded: str | None) -> bool:
    try:
        return encoded is not None and PH.verify(encoded, password)
    except (exceptions.VerifyMismatchError, exceptions.VerificationError, ValueError):
        return False


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    local, sep, domain = email.partition("@")
    if len(email) > 254 or not sep or not local or not domain or domain.startswith(".") or "." not in domain:
        raise ValueError("Informe um e-mail válido.")
    if any(c.isspace() or unicodedata.category(c) == "Cc" or c in "<>" for c in email):
        raise ValueError("Informe um e-mail válido.")
    return email
