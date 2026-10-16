"""Building blocks of the operations table (`hub.api.ops`): parameters, operations and the shared schemas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MULTIPART = "multipart"
S_STR: dict[str, Any] = {"type": "string"}
S_ID: dict[str, Any] = {"type": "string", "pattern": "^[A-Za-z0-9._:-]{1,80}$"}
S_INT: dict[str, Any] = {"type": "integer", "minimum": 0}
S_ENV: dict[str, Any] = {"type": "string", "enum": ["producao", "staging"]}
S_COMMIT: dict[str, Any] = {"type": "string", "pattern": "^[0-9a-fA-F]{7,40}$"}
# free-text names in the path (roles, monitored services: spaces and accents); never "/" nor "\\"
S_NAME: dict[str, Any] = {"type": "string", "minLength": 1, "maxLength": 80, "pattern": r"^[^/\\]+$"}
S_NUM: dict[str, Any] = {"type": "integer", "minimum": 0}
HEALTH_VIEW = ("editar_configuracoes", "ver_auditoria")


@dataclass(frozen=True)
class Param:
    name: str
    loc: str  # "path" | "query"
    schema: dict[str, Any]
    desc: str
    required: bool = False


@dataclass(frozen=True)
class Op:
    id: str
    path: str  # relativo a /api/v1
    tag: str
    summary: str
    resp: str
    method: str = "GET"
    params: tuple[Param, ...] = ()
    body: str | None = None
    target: tuple[str, str] | None = None  # (internal method, internal path with {param})
    native: str | None = None
    list: str | None = None  # key of the array in the internal response ("" = root); None = not a list
    filters: tuple[str, ...] = ()
    passed: tuple[str, ...] = ()  # query parameters passed through to the internal route
    destructive: bool = False
    mcp: bool = True
    need: tuple[str, ...] = ()  # native only: any one of these permissions (empty = authenticated)

    @property
    def write(self) -> bool:
        return self.method != "GET"

    @property
    def full_path(self) -> str:
        return "/api/v1" + self.path


P_APP = Param("app", "path", S_ID, "Id do app (ex.: ativos)", True)
P_ID = Param("id", "path", S_ID, "Identificador", True)
P_ENV = Param("env", "query", S_ENV, "Ambiente (padrão: producao)")


def _named(name: str, desc: str) -> Param:
    return Param(name, "path", S_ID, desc, True)


def _tgt(method: str, path: str) -> dict[str, Any]:
    return {"target": (method, path)}
