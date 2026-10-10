"""Identidade `x-hub-user` e sua assinatura (`x-hub-user-sig`), idênticas ao hub Rust (hub-gateway)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field

USER_SIG_HEADER = "x-hub-user-sig"
USER_SIG_MAX_AGE_S = 900
STAGING_PERMISSION = "editar_configuracoes"


@dataclass
class Identity:
    user: str
    roles: list[str]
    id: str = ""
    name: str = ""
    email: str = ""
    permissions: list[str] = field(default_factory=list)
    locale: str = ""
    apps: list[str] | None = None  # só para o gateway; nunca vai ao app

    def header_value(self) -> str:
        """JSON compacto (ordem dos campos do Rust, vazios omitidos) em base64url sem padding."""
        d: dict[str, object] = {"user": self.user, "roles": self.roles}
        for k in ("id", "name", "email"):
            if v := getattr(self, k):
                d[k] = v
        if self.permissions:
            d["permissions"] = self.permissions
        if self.locale:
            d["locale"] = self.locale
        raw = json.dumps(d, separators=(",", ":"), ensure_ascii=False).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def allows_app(self, app: str) -> bool:
        return self.apps is None or app in self.apps

    def can_view_staging(self) -> bool:
        return STAGING_PERMISSION in self.permissions


def demo_admin() -> Identity:
    # TODO F1 (auth): a identidade vem da sessão/RBAC; até lá, admin fixo de demonstração.
    return Identity(
        user="admin@demo.local",
        roles=["Administrador"],
        id="u-admin",
        name="Administrador",
        email="admin@demo.local",
        permissions=[
            "acessar_aplicacoes",
            "gerenciar_usuarios",
            "editar_configuracoes",
            "ver_auditoria",
            "gerenciar_variaveis",
            "aprovar_solicitacoes",
        ],
    )


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def sign_user(key: bytes, app: str, env: str, user_header: str, ts: int) -> str:
    """`v1.<unix>.<hex(HMAC-SHA256)>` com chave derivada por app (mesmo algoritmo do Rust)."""
    k_app = _hmac(key, f"hub-user-sig/v1/{app}".encode())
    mac = _hmac(k_app, f"v1\n{env}\n{ts}\n{user_header}".encode())
    return f"v1.{ts}.{mac.hex()}"


def verify_user(key: bytes, app: str, env: str, user_header: str, sig: str, now: int | None = None) -> bool:
    now = int(time.time()) if now is None else now
    parts = sig.strip().split(".", 2)
    if len(parts) != 3 or parts[0] != "v1" or not parts[1].isdigit():
        return False
    ts = int(parts[1])
    if ts > now + 60 or now - ts > USER_SIG_MAX_AGE_S or len(parts[2]) != 64:
        return False
    return hmac.compare_digest(sign_user(key, app, env, user_header, ts), sig.strip())


def user_sig_key() -> bytes:
    """`HUB_USER_SIG_KEY` (>= 32 caracteres) ou chave aleatória do processo."""
    fixed = os.environ.get("HUB_USER_SIG_KEY", "").strip()
    return (fixed if len(fixed) >= 32 else secrets.token_hex(32)).encode()
