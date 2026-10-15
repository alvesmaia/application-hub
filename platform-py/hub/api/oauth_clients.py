"""OAuth 2.1 for `/mcp` — Clients: dynamic registration (RFC 7591) and Client ID Metadata Documents (CIMD)."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets
import socket
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response

from hub.api.oauth_base import OAuthBase
from hub.api.oauth_util import (
    K_CLIENT,
    KEPT_CLIENT_MS,
    MAX_UNUSED_CLIENTS,
    PENDING_CONSENT_RESERVE,
    REGISTER_GLOBAL,
    REGISTER_GLOBAL_WEIGHT,
    REGISTER_PER_IP,
    UNUSED_CLIENT_MS,
    OAuthError,
    clean_name,
    host_of,
    ip_public,
    now_ms,
    parse_url,
    redirect_ok,
    sha,
)
from hub.auth import client_ip
from hub.state import Change


async def _capped_body(res: httpx.Response) -> bytes:
    """The client document, refused past 16 KiB while streaming (never buffers a huge answer)."""
    if res.status_code >= 300:
        raise OAuthError(f"documento do cliente respondeu {res.status_code}")
    body = b""
    async for chunk in res.aiter_bytes():
        body += chunk
        if len(body) > 16 * 1024:
            raise OAuthError("documento do cliente grande demais")
    return body


class OAuthClients(OAuthBase):
    # ---- clientes ----------------------------------------------------------------------------------
    @staticmethod
    def client_from(v: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(v.get("client_id"), str) or not isinstance(v.get("redirect_uris"), list):
            return None
        return {"id": v["client_id"], "name": v.get("client_name", ""), "redirect_uris": v["redirect_uris"],
                "kind": v.get("kind", "dcr"), "auth_method": v.get("token_endpoint_auth_method", "none"),
                "secret_hash": v.get("secret_hash"), "disabled": bool(v.get("disabled"))}  # fmt: skip

    async def fetch_cimd(self, url: str) -> dict[str, Any]:
        now = now_ms()
        if (c := self.cimd_cache.get(url)) and c[1] > now:
            return c[0]
        target, host = await self.cimd_target(url)
        c = self.cimd_client(url, await self.cimd_download(target, host))
        if len(self.cimd_cache) > 500:
            self.cimd_cache = {k: x for k, x in self.cimd_cache.items() if x[1] > now}
        self.cimd_cache[url] = (c, now + 5 * 60_000)
        return c

    async def cimd_target(self, url: str) -> tuple[str, str]:
        """SSRF guard: the URL to fetch with the host pinned to an already-vetted IP (no second DNS lookup)."""
        import os

        private_ok = os.getenv("HUB_OAUTH_CIMD_ALLOW_PRIVATE", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
            "sim",
        )
        p = parse_url(url)
        if p is None:
            raise OAuthError("client_id não é uma URL válida")
        if p[0] != "https" and not (private_ok and p[0] == "http"):
            raise OAuthError("client_id (URL) precisa ser HTTPS")
        if p[3].split("?")[0] in ("", "/"):
            raise OAuthError("client_id (URL) precisa ter caminho")
        host = p[1].strip("[]")
        port = int(p[2]) if p[2] else (443 if p[0] == "https" else 80)
        ips = [ipaddress.ip_address(a) for a in await self.cimd_resolve(host, port)]
        if not ips:
            raise OAuthError("host do client_id não resolve")
        if not private_ok and any(not ip_public(ip) for ip in ips):
            raise OAuthError("client_id aponta para endereço interno (recusado)")
        pin = f"[{ips[0]}]" if ips[0].version == 6 else str(ips[0])
        return url.replace(p[1] + (f":{p[2]}" if p[2] else ""), f"{pin}:{port}", 1), host

    async def cimd_resolve(self, host: str, port: int) -> list[str]:
        try:
            if self.resolver:
                return await self.resolver(host, port)
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
            return [str(i[4][0]) for i in infos]
        except OSError as e:
            raise OAuthError(f"host do client_id não resolve: {e}") from e

    async def cimd_download(self, target: str, host: str) -> bytes:
        try:
            async with httpx.AsyncClient(
                timeout=5, follow_redirects=False, transport=self.cimd_transport
            ) as cli, cli.stream("GET", target, headers={"host": host, "accept": "application/json"},
                                  extensions={"sni_hostname": host}) as res:  # fmt: skip
                return await _capped_body(res)
        except httpx.HTTPError as e:
            raise OAuthError(f"documento do cliente inacessível: {e}") from e

    def cimd_client(self, url: str, body: bytes) -> dict[str, Any]:
        try:
            v = json.loads(body)
        except ValueError as e:
            raise OAuthError("documento do cliente não é JSON válido") from e
        if not isinstance(v, dict) or v.get("client_id") != url:
            raise OAuthError("client_id do documento não é a própria URL")
        if not str(v.get("client_name", "")).strip():
            raise OAuthError("documento do cliente sem client_name")
        uris = v.get("redirect_uris")
        if not isinstance(uris, list):
            raise OAuthError("documento do cliente sem redirect_uris")
        if not uris or any(
            not isinstance(u, str) or not redirect_ok(u, self.cfg.allowed_redirect_hosts) for u in uris
        ):
            raise OAuthError("redirect_uris do documento inválidas ou não permitidas")
        if v.get("token_endpoint_auth_method", "none") != "none":
            raise OAuthError("documento do cliente: só token_endpoint_auth_method = none")
        return {"id": url, "name": clean_name(v["client_name"]), "redirect_uris": uris, "kind": "cimd",
                "auth_method": "none", "secret_hash": None, "disabled": False}  # fmt: skip

    async def load_client(self, cid: str) -> dict[str, Any]:
        raw = await self.rec.get(K_CLIENT, sha(cid))
        stored = self.client_from(raw) if raw else None
        if stored and stored["disabled"]:
            raise OAuthError("cliente bloqueado pela administração da plataforma")
        if cid.startswith(("https://", "http://")):
            if not self.cfg.client_metadata_documents:
                raise OAuthError("client_id por URL (metadata document) desligado neste hub")
            return await self.fetch_cimd(cid)
        if stored is None:
            raise OAuthError("client_id desconhecido")
        return stored

    async def remember_cimd(self, c: dict[str, Any]) -> None:
        fresh = {"client_id": c["id"], "client_name": c["name"], "redirect_uris": c["redirect_uris"], "kind": "cimd",
                 "token_endpoint_auth_method": "none", "created_at": now_ms(), "used": True}  # fmt: skip

        def f(cur: Any) -> tuple[Change, None]:
            if cur and cur.get("disabled"):
                return Change.keep(), None
            if cur:
                cur = cur | {
                    "client_name": fresh["client_name"],
                    "redirect_uris": fresh["redirect_uris"],
                    "used": True,
                }
                return Change.set(cur, now_ms() + KEPT_CLIENT_MS), None
            return Change.set(fresh, now_ms() + KEPT_CLIENT_MS), None

        await self.rec.update(K_CLIENT, sha(c["id"]), f)

    async def evict_unused(self, need: int) -> None:
        now = now_ms()
        cands = sorted(
            await self.rec.list(K_CLIENT, ("used", "false"), 20_000), key=lambda x: x[1].get("created_at", 0)
        )
        pending = sum(1 for _, v, _ in cands if v.get("consent_until", 0) > now)
        unprotected, done = max(pending - PENDING_CONSENT_RESERVE, 0), 0
        for key, v, _ in cands:
            if done >= need:
                break
            is_pending = v.get("consent_until", 0) > now
            force = is_pending and unprotected > 0
            if is_pending and not force:
                continue
            unprotected -= 1 if force else 0

            def f(cur: Any, force: bool = force) -> tuple[Change, bool]:
                if (
                    cur
                    and cur.get("used") is not True
                    and cur.get("disabled") is not True
                    and (force or cur.get("consent_until", 0) <= now)
                ):
                    return Change.delete(), True
                return Change.keep(), False

            if await self.rec.update(K_CLIENT, key, f):
                done += 1

    async def mark_consent_pending(self, cid: str, until: int) -> None:
        def f(cur: Any) -> tuple[Change, None]:
            if cur and cur.get("used") is not True:
                return Change.set(
                    cur | {"consent_until": until}, now_ms() + max(UNUSED_CLIENT_MS, until - now_ms())
                ), None
            return Change.keep(), None

        await self.rec.update(K_CLIENT, sha(cid), f)

    async def mark_client_used(self, cid: str) -> None:
        def f(cur: Any) -> tuple[Change, None]:
            if cur and cur.get("used") is not True:
                return Change.set(cur | {"used": True}, now_ms() + KEPT_CLIENT_MS), None
            return Change.keep(), None

        await self.rec.update(K_CLIENT, sha(cid), f)

    # ---- registro (RFC 7591) -----------------------------------------------------------------------
    async def register(self, request: Request, body: bytes) -> Response:
        c = self.cfg
        if not c.dynamic_registration:
            return self.oerr(
                404,
                "not_found",
                "Registro dinâmico de clientes desligado ([mcp.oauth].dynamic_registration = false).",
            )
        peer = client_ip(request)
        if await self.register_limited(request.app, peer):
            return self.oerr(429, "slow_down", "Registros demais deste endereço; tente mais tarde.")
        try:
            v = json.loads(body)
        except ValueError:
            return self.bad_metadata("Corpo JSON inválido.")
        if not isinstance(v, dict):
            return self.bad_metadata("Corpo JSON inválido.")
        meta = self.register_metadata(v)
        if isinstance(meta, Response):
            return meta
        uris, method = meta
        name = clean_name(str(v.get("client_name") or "")) or "Cliente sem nome"
        unused = await self.rec.count(K_CLIENT, ("used", "false"))
        if unused >= MAX_UNUSED_CLIENTS:
            await self.evict_unused(unused - MAX_UNUSED_CLIENTS + 1)
        return await self.register_client(name, uris, method, peer)

    async def register_limited(self, app: FastAPI, peer: str) -> bool:
        over = not await self.hit(app, f"oauth-reg:{peer}", REGISTER_PER_IP)
        if not over:
            reserved = await self.hit(app, f"oauth-reg-g:{peer}", REGISTER_GLOBAL_WEIGHT)
            glob = await self.hit(app, "oauth-reg:*", REGISTER_GLOBAL)
            over = not (glob or reserved)
        return over

    def bad_metadata(self, d: str) -> Response:
        return self.oerr(400, "invalid_client_metadata", d)

    def register_metadata(self, v: dict[str, Any]) -> tuple[list[str], str] | Response:
        """Validated `(redirect_uris, token_endpoint_auth_method)` of a registration request, or the error."""
        raw = v.get("redirect_uris")
        if not isinstance(raw, list) or not 1 <= len(raw) <= 10:
            return self.oerr(400, "invalid_redirect_uri", "redirect_uris obrigatório (1 a 10).")
        uris: list[str] = [u for u in raw if isinstance(u, str)]
        for u in uris:
            if not redirect_ok(u, self.cfg.allowed_redirect_hosts):
                return self.oerr(
                    400,
                    "invalid_redirect_uri",
                    f"redirect_uri não permitida: {u} (use https, ou http só em localhost; sem fragmento; hosts permitidos pela administração)",
                )
        method = v.get("token_endpoint_auth_method", "client_secret_basic")
        if method not in ("none", "client_secret_post", "client_secret_basic"):
            return self.bad_metadata(
                "token_endpoint_auth_method: none, client_secret_post ou client_secret_basic."
            )
        if isinstance(gt := v.get("grant_types"), list) and any(
            g not in ("authorization_code", "refresh_token") for g in gt
        ):
            return self.bad_metadata("grant_types: authorization_code e refresh_token.")
        if isinstance(rt := v.get("response_types"), list) and any(g != "code" for g in rt):
            return self.bad_metadata("response_types: code.")
        return uris, str(method)  # already one of the three allowed names; str() only narrows the type

    async def register_client(self, name: str, uris: list[str], method: str, peer: str) -> Response:
        now, cid = now_ms(), f"hubc_{secrets.token_hex(16)}"
        secret = f"hubcs_{secrets.token_hex(24)}" if method != "none" else None
        rec = {"client_id": cid, "client_name": name, "redirect_uris": uris, "kind": "dcr",
               "token_endpoint_auth_method": method, "secret_hash": sha(secret) if secret else None,
               "created_at": now, "used": False, "ip": peer}  # fmt: skip
        await self.rec.put(K_CLIENT, sha(cid), rec, now + UNUSED_CLIENT_MS)
        await self.audit("OAuth", "OAuth: cliente registrado (registro dinâmico)",
                         f"{name} · {cid} · retorno: {', '.join(host_of(u) for u in uris)}")  # fmt: skip
        out: dict[str, Any] = {
            "client_id": cid, "client_id_issued_at": now // 1000, "client_name": name, "redirect_uris": uris,
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": method,
        }  # fmt: skip
        if secret:
            out |= {"client_secret": secret, "client_secret_expires_at": 0}
        return self.json_res(201, out)
