"""Identidade injetada pelo hub no cabeçalho ``x-hub-user``.

Formato (contrato, ver ``docs/CONTRATO-APP.md``): JSON em **base64url sem padding**::

    {"user": "ana@corp.com", "roles": ["Administrador"], "id": "u1a2b3c4",
     "name": "Ana", "email": "ana@corp.com", "permissions": ["acessar_aplicacoes"]}

``user`` (login) e ``roles`` sempre presentes; os demais só quando preenchidos.

Regras de confiança: o gateway **sempre** descarta qualquer ``x-hub-*`` vindo do
navegador e injeta o seu — o app confia no cabeçalho porque só o hub o alcança (rede
interna ``hub-app-<id>``). Ausente ou inválido → identidade de convidado (o app nunca
deve tratar isso como administrador).
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field

from starlette.requests import HTTPConnection

USER_HEADER = "x-hub-user"
ENV_HEADER = "x-hub-env"
GUEST = "convidado"
# Teto do cabeçalho decodificado (o hub nunca manda mais que alguns KiB).
MAX_HEADER = 16 * 1024


@dataclass(frozen=True)
class Identity:
    user: str
    roles: tuple[str, ...] = ()
    id: str = ""
    name: str = ""
    email: str = ""
    permissions: tuple[str, ...] = field(default_factory=tuple)
    # Idioma do usuário (onda 9): ``pt-BR``, ``en`` ou ``es``; vazio em hubs anteriores.
    locale: str = ""

    @property
    def display_name(self) -> str:
        """Nome para exibir (`name`, senão o login)."""
        return self.name.strip() or self.user

    @property
    def is_guest(self) -> bool:
        return self.user == GUEST and not self.roles

    def has_permission(self, p: str) -> bool:
        return p in self.permissions

    def encode(self) -> str:
        """Valor do cabeçalho (para testes e para o `hub dev`)."""
        d: dict[str, object] = {"user": self.user, "roles": list(self.roles)}
        for k in ("id", "name", "email", "locale"):
            v = getattr(self, k)
            if v:
                d[k] = v
        if self.permissions:
            d["permissions"] = list(self.permissions)
        raw = json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


GUEST_IDENTITY = Identity(user=GUEST)


def _str_list(v: object) -> tuple[str, ...]:
    if not isinstance(v, list):
        return ()
    return tuple(x for x in v if isinstance(x, str))


def parse_identity(value: str | None) -> Identity | None:
    """Decodifica o cabeçalho; ``None`` se ausente ou inválido."""
    if value is None or not value:
        return None
    v = value.strip()
    if not v or len(v) > MAX_HEADER * 2:
        return None
    try:
        raw = base64.urlsafe_b64decode(v + "=" * (-len(v) % 4))
        data = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    user = data.get("user")
    if not isinstance(user, str) or not user.strip():
        return None

    def s(k: str) -> str:
        x = data.get(k, "")
        return x if isinstance(x, str) else ""

    return Identity(
        user=user.strip(),
        roles=_str_list(data.get("roles")),
        id=s("id"),
        name=s("name"),
        email=s("email"),
        permissions=_str_list(data.get("permissions")),
        locale=s("locale"),
    )


def identity_of(conn: HTTPConnection) -> Identity:
    """Identidade da requisição (convidado se ausente/inválida). Use como dependência
    do FastAPI: ``ident: Identity = Depends(identity_of)``."""
    return parse_identity(conn.headers.get(USER_HEADER)) or GUEST_IDENTITY


def user_key(headers_value: str | None) -> str:
    """Valor cru do cabeçalho, aparado — é o escopo das chaves de idempotência (igual ao
    Rust: a chave de outro usuário nunca devolve a resposta deste)."""
    return (headers_value or "").strip()


def env_of(conn: HTTPConnection) -> str:
    """Ambiente que o hub declarou (`producao`/`staging`); padrão produção."""
    v = conn.headers.get(ENV_HEADER, "producao").strip()
    return v if v in ("producao", "staging") else "producao"
