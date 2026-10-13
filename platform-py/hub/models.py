"""Modelos Pydantic das fronteiras JSON (formato compatível com a versão anterior do hub)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class CompatibleModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class UserRecord(CompatibleModel):
    id: str
    email: str
    name: str
    roles: list[str]
    active: bool
    created_at: int


class RoleRecord(CompatibleModel):
    name: str
    permissions: list[bool] = Field(default_factory=list)


class TokenRecord(CompatibleModel):
    id: str
    name: str
    hash: str
    apps: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    created_at: int


class SessionRecord(CompatibleModel):
    id_hash: str
    user_id: str
    created_at: int
    last_seen: int


class AuthDocument(CompatibleModel):
    users: list[UserRecord] = Field(default_factory=list)
    sessions: list[SessionRecord] = Field(default_factory=list)
    tokens: list[TokenRecord] = Field(default_factory=list)
    next_id: int = 0


class KeyringDek(CompatibleModel):
    id: str
    wrapped: str
    created_ms: int


class KeyringFile(CompatibleModel):
    version: int
    kek_fingerprint: str | None = None
    active: str
    deks: list[KeyringDek]
    audit_seed: str | None = None
    audit_public_keys: list[str] = Field(default_factory=list)
    sealed_meta: str | None = None


class AdminDocument(CompatibleModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    env: list[dict[str, Any]] = Field(default_factory=list)
    roles: list[RoleRecord] = Field(default_factory=list)
    health: list[dict[str, Any]] = Field(default_factory=list)
    audit: list[dict[str, Any]] = Field(default_factory=list)
    next_audit_id: int = 0


class AppRequires(BaseModel):
    """`requires` do manifesto: o que o hub provisiona na implantação (banco, armazenamento) e as
    variáveis/segredos que o app espera (valores definidos no hub, nunca no pacote)."""

    model_config = ConfigDict(extra="forbid")
    database: bool = False
    storage: bool = False
    variables: list[str] = Field(default_factory=list, max_length=64)
    secrets: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("variables", "secrets")
    @classmethod
    def _names(cls, v: list[str]) -> list[str]:
        for n in v:
            if not _ENV_NAME.match(n) or n.startswith("HUB_") or n == "DATABASE_URL":
                raise ValueError(
                    f"nome inválido: {n!r} (MAIÚSCULAS_COM_SUBLINHADO, sem HUB_* nem DATABASE_URL)"
                )
        if len(set(v)) != len(v):
            raise ValueError("nomes repetidos")
        return v

    @model_validator(mode="after")
    def _disjoint(self) -> AppRequires:
        both = set(self.variables) & set(self.secrets)
        if both:
            raise ValueError(f"declarado como variável e segredo: {', '.join(sorted(both))}")
        return self


_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def requires_error(raw: object) -> str | None:
    """Mensagem curta se `requires` do manifesto for inválido (None = ok ou ausente)."""
    if raw is None:
        return None
    try:
        AppRequires.model_validate(raw)
    except ValidationError as e:
        err = e.errors()[0]
        where = ".".join(str(x) for x in err["loc"])
        return f"requires{'.' + where if where else ''}: {err['msg'].removeprefix('Value error, ')}"
    return None


class AppManifest(CompatibleModel):
    id: str
    name: str | None = None
    version: str | None = None
    limits: dict[str, Any] = Field(default_factory=dict)
    forward_auth_schemes: list[str] = Field(default_factory=list)
    stack: dict[str, Any] | None = None
    runtime: dict[str, Any] | None = None
    api: dict[str, Any] = Field(default_factory=dict)
    mcp: dict[str, Any] = Field(default_factory=dict)
    requires: AppRequires | None = None


class AppRecord(CompatibleModel):
    manifest: AppManifest
    upstream: str
    display: dict[str, str] = Field(default_factory=dict)


class AuditEntry(CompatibleModel):
    id: int
    at: int
    actor: str
    icon: str
    title: str
    detail: str
    scope: str


class LoginInput(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class TokenCreateInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    apps: list[str] = Field(default_factory=list)
    actions: list[str] = Field(min_length=1)
    expires_days: int | None = Field(default=None, ge=1, le=365)
