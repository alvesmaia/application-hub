"""Feature flags (`hub-flags`): validation, evaluation per user/role/percentage and the `x-hub-flags` header."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

HEADER = "x-hub-flags"
ON, OFF = "on", "off"
MAX_HEADER_BYTES, MAX_FLAGS_PER_APP = 4096, 100
DIGEST_PREFIX = "sha256:"
_TOKEN = re.compile(r"[a-z0-9._-]{1,64}")


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roles: list[str] = Field(default_factory=list)
    users: list[str] = Field(default_factory=list)
    serve: str


class Rollout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    percent: int = Field(ge=0, le=255)
    serve: str


class EnvRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    default: str
    rules: list[Rule] = Field(default_factory=list)
    rollout: Rollout | None = None


class Flag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    app: str
    description: str = ""
    kind: Literal["boolean", "variant"] = "boolean"
    variants: list[str] = Field(default_factory=list)
    envs: dict[str, EnvRule] = Field(default_factory=dict)

    def values(self) -> list[str]:
        return [OFF, ON] if self.kind == "boolean" else self.variants

    def off_value(self) -> str:
        return OFF if self.kind == "boolean" else (self.variants[0] if self.variants else OFF)

    def validate_rules(self) -> None:
        """Raises `ValueError` with the message of the previous hub version."""
        if not valid_token(self.key):
            raise ValueError("Chave inválida: use letras minúsculas, números, '.', '_' ou '-' (até 64).")
        if len(self.description) > 200:
            raise ValueError("Descrição muito longa (até 200 caracteres).")
        if self.kind == "variant":
            if not 2 <= len(self.variants) <= 10:
                raise ValueError("Flag de variantes precisa de 2 a 10 variantes.")
            if any(not valid_token(v) for v in self.variants):
                raise ValueError("Variante inválida (letras minúsculas, números, '.', '_' ou '-').")
        vals = self.values()
        for env, r in self.envs.items():
            _validate_env(env, r, vals)

    def dump(self) -> dict[str, Any]:
        """Envs in key order, `rollout` omitted when empty (format of the previous hub version)."""
        d = self.model_dump(exclude_none=True)
        d["envs"] = dict(sorted(d["envs"].items()))
        return d


def _validate_env(env: str, r: EnvRule, vals: list[str]) -> None:
    if env not in ("staging", "producao"):
        raise ValueError("Ambiente inválido (staging ou producao).")
    served = [r.default, *(x.serve for x in r.rules)]
    if r.rollout:
        if r.rollout.percent > 100:
            raise ValueError("Percentual deve ser de 0 a 100.")
        served.append(r.rollout.serve)
    if bad := next((v for v in served if v not in vals), None):
        raise ValueError(f'Valor "{bad}" não é uma opção desta flag.')
    if len(r.rules) > 20 or any(len(x.roles) + len(x.users) > 100 for x in r.rules):
        raise ValueError("Regras demais (até 20, com até 100 funções/usuários cada).")


def parse_flag(raw: Any) -> Flag:
    try:
        return Flag.model_validate(raw)
    except ValidationError as e:
        raise ValueError("Dados inválidos: verifique os campos enviados.") from e


def valid_token(s: str) -> bool:
    return bool(_TOKEN.fullmatch(s))


def user_digest(key: str, user: str) -> str:
    return DIGEST_PREFIX + hashlib.sha256(f"hub-flag-user:v1:{key}:{user.lower()}".encode()).hexdigest()


def bucket(key: str, user: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{key}:{user}".encode()).digest()[:4], "big") % 10_000


def evaluate(flag: Flag, env: str, user: str, roles: list[str]) -> str:
    """Kill switch → user/role rule → percentage → default."""
    r = flag.envs.get(env)
    if r is None or not r.enabled:
        return flag.off_value()
    for rule in r.rules:
        by_user = bool(user) and any(
            u == user_digest(flag.key, user) if u.startswith(DIGEST_PREFIX) else u.lower() == user.lower()
            for u in rule.users
        )
        if by_user or any(x in roles for x in rule.roles):
            return rule.serve
    if r.rollout and user and bucket(flag.key, user) < r.rollout.percent * 100:
        return r.rollout.serve
    return r.default


def header_value(flags: list[Flag], app: str, env: str, user: str, roles: list[str]) -> str:
    out = ""
    for k, v in sorted((f.key, evaluate(f, env, user, roles)) for f in flags if f.app == app):
        item = f"{k}={v}"
        if len(out) + len(item) + bool(out) > MAX_HEADER_BYTES:
            break
        out += ("," if out else "") + item
    return out


def flag_detail(f: Flag) -> str:
    def part(env: str, r: EnvRule) -> str:
        ro = f", {r.rollout.percent}% → {r.rollout.serve}" if r.rollout else ""
        ru = f", {len(r.rules)} regra(s)" if r.rules else ""
        return f"{'Produção' if env == 'producao' else 'Staging'}: {'ligada' if r.enabled else 'desligada'}{ro}{ru}"

    return f"{f.key} · {' · '.join(part(e, r) for e, r in sorted(f.envs.items()))}"


def for_push(flags: list[Flag], app: str, env: str) -> list[dict[str, Any]]:
    """What the hub pushes to the environment's container: only the `env` rules, with users replaced by digests."""
    out = []
    for f in (x for x in flags if x.app == app):
        d = f.model_copy(update={"envs": {k: v for k, v in f.envs.items() if k == env}}, deep=True)
        for r in d.envs.values():
            for rule in r.rules:
                rule.users = [u if u.startswith(DIGEST_PREFIX) else user_digest(d.key, u) for u in rule.users]
        out.append(d.dump())
    return out
