"""CORS dos endpoints de máquina do MCP/OAuth (`/mcp`, `/.well-known/oauth-*`, `/oauth/{token,register,revoke}`).

Só aceitam bearer/autenticação do cliente, nunca cookie: `Allow-Origin: *` é seguro e `Allow-Credentials` nunca
sai. O preflight responde 204 antes da autenticação; as respostas reais (inclusive 4xx) levam os cabeçalhos para
o navegador ler o `WWW-Authenticate` do 401. `/oauth/authorize` (tela do navegador) fica de fora."""

from __future__ import annotations

import os
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

ALLOW_HEADERS = "authorization, content-type, accept, mcp-protocol-version, mcp-session-id, last-event-id"
EXPOSE = "WWW-Authenticate, Mcp-Session-Id, MCP-Protocol-Version"


def methods_for(path: str, mcp_path: str) -> str | None:
    if path == mcp_path:
        return "GET, POST, DELETE, OPTIONS"
    if path in ("/.well-known/oauth-authorization-server", "/.well-known/oauth-protected-resource") or (
        path.startswith("/.well-known/oauth-protected-resource/")
    ):
        return "GET, OPTIONS"
    if path in ("/oauth/token", "/oauth/register", "/oauth/revoke"):
        return "POST, OPTIONS"
    return None


def machine_public(path: str, mcp_path: str) -> bool:
    """Endpoints de máquina sem sessão (descoberta, token, registro, revogação): o `/mcp` autentica por bearer."""
    m = methods_for(path, mcp_path)
    return m is not None and path != mcp_path


class Cors:
    """Middleware ASGI mais externo (roda antes da autenticação)."""

    def __init__(self, app: ASGIApp, mcp_path: str = "/mcp") -> None:
        self.app, self.mcp_path = app, mcp_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope["path"], scope["method"]
        h = {k.decode().lower(): v.decode("latin-1") for k, v in scope["headers"]}
        diag = os.getenv("HUB_LOG_MCP_REQUESTS") == "1" and (
            "well-known" in path or "oauth" in path or path.startswith(self.mcp_path)
        )
        line = (
            f"{method} {path} ua={h.get('user-agent', '-')[:160]!r} origin={h.get('origin', '-')[:160]!r} "
            f"accept={h.get('accept', '-')[:160]!r} auth={'sim' if 'authorization' in h else 'não'} "
            f"ip={h.get('cf-connecting-ip', '-')}"
        )
        methods = methods_for(path, self.mcp_path)
        if methods is None:
            if diag:
                print(f"hub-web: [mcp-diag] {line} (fora das rotas CORS)", flush=True)
            return await self.app(scope, receive, send)
        if diag:
            print(f"hub-web: [mcp-diag] {line}", flush=True)
        if method == "OPTIONS":
            headers: list[Any] = [
                (b"access-control-allow-origin", b"*"),
                (b"access-control-expose-headers", EXPOSE.encode()),
                (b"access-control-allow-methods", methods.encode()),
                (b"access-control-allow-headers", ALLOW_HEADERS.encode()),
                (b"access-control-max-age", b"600"),
                (b"vary", b"Origin"),
                (b"content-length", b"0"),
            ]
            await send({"type": "http.response.start", "status": 204, "headers": headers})
            return await send({"type": "http.response.body", "body": b""})

        async def wrapped(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                hs = [
                    (k, v) for k, v in msg["headers"] if k.lower() not in (b"access-control-allow-origin",
                    b"access-control-expose-headers", b"access-control-allow-credentials")
                ]  # fmt: skip
                hs += [
                    (b"access-control-allow-origin", b"*"),
                    (b"access-control-expose-headers", EXPOSE.encode()),
                ]
                msg = {**msg, "headers": hs}
            await send(msg)

        await self.app(scope, receive, wrapped)
