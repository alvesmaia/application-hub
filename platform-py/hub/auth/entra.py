"""Sign-in with Microsoft Entra ID (OIDC code + PKCE, v2 endpoints), configured only via env/hub.toml `[sso]`."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from cryptography.hazmat.primitives.hashes import SHA256

DEFAULT_AUTHORITY = "https://login.microsoftonline.com"
JWKS_TTL = 3600.0
SKEW = 120
# Tests inject a transport (fake provider); production uses the network.
TRANSPORT: httpx.AsyncBaseTransport | None = None


class SsoError(Exception):
    """SSO sign-in validation failure (message safe for the user, no secrets)."""


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip().lower() for x in v if str(x).strip()]
    return [x.strip().lower() for x in str(v or "").split(",") if x.strip()]


def parse_role_map(raw: Any) -> dict[str, str]:
    """`{"<groupId|appRole>": "Role"}` as JSON, or `id=Role,id2=Role`."""
    if isinstance(raw, dict):
        return {str(k).strip().lower(): str(v).strip() for k, v in raw.items() if str(v).strip()}
    raw = str(raw or "").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        return parse_role_map(json.loads(raw))
    out: dict[str, str] = {}
    for part in raw.split(","):
        k, sep, v = part.partition("=")
        if sep and k.strip() and v.strip():
            out[k.strip().lower()] = v.strip()
    return out


def _split_jwt(token: str) -> tuple[str, str, str, dict[str, Any], dict[str, Any]]:
    """Segments plus decoded header/claims of an RS256 id_token (only RS256 is accepted)."""
    try:
        h64, c64, s64 = token.split(".")
        header, claims = json.loads(_b64d(h64)), json.loads(_b64d(c64))
    except ValueError as e:
        raise SsoError("Resposta de login inválida.") from e
    if header.get("alg") != "RS256":
        raise SsoError("Resposta de login inválida.")
    return h64, c64, s64, header, claims


@dataclass
class EntraConfig:
    tenant: str
    client_id: str
    client_secret: str = field(repr=False)
    authority: str = DEFAULT_AUTHORITY
    backchannel: str = DEFAULT_AUTHORITY
    allowed_tenants: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    role_map: dict[str, str] = field(default_factory=dict)
    default_role: str = "Colaborador"
    _jwks: dict[str, Any] = field(default_factory=dict, repr=False)
    _jwks_at: float = 0.0

    @property
    def multi_tenant(self) -> bool:
        return self.tenant in ("organizations", "common")

    def endpoint(self, kind: str, back: bool = True) -> str:
        base = self.backchannel if back else self.authority
        if kind == "keys":
            return f"{base}/{self.tenant}/discovery/v2.0/keys"
        return f"{base}/{self.tenant}/oauth2/v2.0/{kind}"

    def tenant_ok(self, tid: str) -> bool:
        tid = tid.lower()
        if not self.multi_tenant and tid != self.tenant.lower():
            return False
        return not self.allowed_tenants or tid in self.allowed_tenants

    def domain_ok(self, email: str) -> bool:
        return not self.allowed_domains or email.rpartition("@")[2] in self.allowed_domains

    def roles_for(self, claims: dict[str, Any]) -> list[str]:
        ids = [str(x).lower() for x in [*claims.get("groups", []), *claims.get("roles", [])]]
        return list(dict.fromkeys(self.role_map[i] for i in ids if i in self.role_map))

    async def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=TRANSPORT, timeout=10.0)

    async def _keys(self, refresh: bool) -> dict[str, Any]:
        if refresh or not self._jwks or time.monotonic() - self._jwks_at > JWKS_TTL:
            async with await self._http() as c:
                r = await c.get(self.endpoint("keys"))
                r.raise_for_status()
                self._jwks = {k["kid"]: k for k in r.json().get("keys", []) if k.get("kty") == "RSA"}
                self._jwks_at = time.monotonic()
        return self._jwks

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> str:
        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "scope": "openid profile email",
        }
        async with await self._http() as c:
            r = await c.post(self.endpoint("token"), data=data)
        if r.status_code != 200 or "id_token" not in r.json():
            raise SsoError("O Microsoft Entra ID recusou o login.")
        return str(r.json()["id_token"])

    async def validate(self, token: str, nonce: str) -> dict[str, Any]:
        h64, c64, s64, header, claims = _split_jwt(token)
        await self._verify_signature(header, f"{h64}.{c64}".encode(), s64)
        self._check_claims(claims, nonce)
        return dict(claims)

    async def _verify_signature(self, header: dict[str, Any], signed: bytes, s64: str) -> None:
        kid = str(header.get("kid", ""))
        key = (await self._keys(False)).get(kid) or (await self._keys(True)).get(kid)
        if not key:
            raise SsoError("Assinatura do login inválida.")
        pub = RSAPublicNumbers(int.from_bytes(_b64d(key["e"])), int.from_bytes(_b64d(key["n"]))).public_key()
        try:
            pub.verify(_b64d(s64), signed, PKCS1v15(), SHA256())
        except Exception as e:
            raise SsoError("Assinatura do login inválida.") from e

    def _check_claims(self, claims: dict[str, Any], nonce: str) -> None:
        now, tid = int(time.time()), str(claims.get("tid", ""))
        if claims.get("aud") != self.client_id:
            raise SsoError("Resposta de login inválida.")
        if claims.get("iss") != f"{self.authority}/{tid}/v2.0":
            raise SsoError("Resposta de login inválida.")
        if int(claims.get("exp", 0)) < now - SKEW or int(claims.get("nbf", 0)) > now + SKEW:
            raise SsoError("O login expirou; tente de novo.")
        if not nonce or not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise SsoError("Resposta de login inválida.")
        if not self.tenant_ok(tid):
            raise SsoError("Esta organização não tem acesso à plataforma.")

    def authorize_url(self, redirect_uri: str) -> tuple[str, dict[str, str]]:
        state, nonce, verifier = (secrets.token_urlsafe(24) for _ in range(3))
        challenge = _b64e(hashlib.sha256(verifier.encode()).digest())
        q = httpx.QueryParams(
            client_id=self.client_id,
            response_type="code",
            redirect_uri=redirect_uri,
            response_mode="query",
            scope="openid profile email",
            state=state,
            nonce=nonce,
            code_challenge=challenge,
            code_challenge_method="S256",
            prompt="select_account",
        )
        return f"{self.endpoint('authorize', back=False)}?{q}", {
            "state": state,
            "nonce": nonce,
            "verifier": verifier,
        }


VAULT_SECRET = "entra-client-secret"  # noqa: S105 - name of the item in the vault


def _get(conf: dict[str, Any], env: str) -> str:
    key = env.removeprefix("HUB_").removeprefix("ENTRA_").removeprefix("SSO_").lower()
    return str(os.getenv(env) or conf.get(key) or "").strip()


def load(config: dict[str, Any] | None = None, vault: Any = None) -> EntraConfig | None:
    """Reads `HUB_SSO_PROVIDER=entra` + `HUB_ENTRA_*` (or `[sso]` of hub.toml). If not everything is configured: None.
    Secret: variable, file or, if neither, the vault (`python -m hub.admin set-entra-secret`)."""
    conf = dict((config or {}).get("sso", {}))
    if _get(conf, "HUB_SSO_PROVIDER").lower() != "entra":
        return None
    secret = _get(conf, "HUB_ENTRA_CLIENT_SECRET")
    if not secret and (f := _get(conf, "HUB_ENTRA_CLIENT_SECRET_FILE")) and Path(f).is_file():
        secret = Path(f).read_text(encoding="utf-8").strip()
    if not secret and vault is not None:
        secret = (vault.protected(VAULT_SECRET) or "").strip()
    tenant, client = _get(conf, "HUB_ENTRA_TENANT_ID"), _get(conf, "HUB_ENTRA_CLIENT_ID")
    if not (tenant and client and secret):
        return None
    authority = (_get(conf, "HUB_ENTRA_AUTHORITY") or DEFAULT_AUTHORITY).rstrip("/")
    allowed = _list(os.getenv("HUB_ENTRA_ALLOWED_TENANTS") or conf.get("allowed_tenants"))
    if tenant.lower() in ("organizations", "common") and not allowed:
        raise SsoError(
            "SSO desligado: com HUB_ENTRA_TENANT_ID=organizations/common defina HUB_ENTRA_ALLOWED_TENANTS."
        )
    return EntraConfig(
        tenant=tenant,
        client_id=client,
        client_secret=secret,
        authority=authority,
        backchannel=(_get(conf, "HUB_ENTRA_BACKCHANNEL_URL") or authority).rstrip("/"),
        allowed_tenants=allowed,
        allowed_domains=_list(os.getenv("HUB_ENTRA_ALLOWED_DOMAINS") or conf.get("allowed_domains")),
        role_map=parse_role_map(os.getenv("HUB_ENTRA_ROLE_MAP") or conf.get("role_map")),
        default_role=_get(conf, "HUB_ENTRA_DEFAULT_ROLE") or "Colaborador",
    )


def local_login_mode(config: dict[str, Any] | None = None) -> str:
    """`on` (default), `admins` (administrators only) or `off` (only applies with SSO configured)."""
    v = _get(dict((config or {}).get("sso", {})), "HUB_LOCAL_LOGIN").lower() or "1"
    if v in ("admins", "admin"):
        return "admins"
    return "off" if v in ("0", "false", "no", "off", "nao", "não") else "on"
