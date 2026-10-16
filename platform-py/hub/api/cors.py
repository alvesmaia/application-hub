"""CORS for the MCP/OAuth machine endpoints (`/mcp`, `/.well-known/oauth-*`, `/oauth/{token,register,revoke}`).

Only accept the client's bearer/authentication, never cookies: `Allow-Origin: *` is safe and `Allow-Credentials` is never
sent. The preflight answers 204 before authentication; the real responses (including 4xx) carry the headers so
the browser can read the 401's `WWW-Authenticate`. `/oauth/authorize` (browser screen) is excluded."""

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


def upload_path(path: str, mcp_path: str) -> bool:
    """One-time package upload (`{mcp_path}/uploads/<ticket>`, the ticket is the credential; curl, no CORS)."""
    rest = path.removeprefix(mcp_path + "/uploads/")
    return rest != path and bool(rest) and "/" not in rest


def machine_public(path: str, mcp_path: str) -> bool:
    """Session-less machine endpoints (discovery, token, registration, revocation, upload links): `/mcp` itself
    authenticates by bearer."""
    m = methods_for(path, mcp_path)
    return (m is not None and path != mcp_path) or upload_path(path, mcp_path)


def _diag_line(scope: Scope, path: str, mcp_path: str) -> str | None:
    """Request summary for `HUB_LOG_MCP_REQUESTS=1` (debugging MCP clients), or None when off/out of scope."""
    if not (os.getenv("HUB_LOG_MCP_REQUESTS") == "1" and (
        "well-known" in path or "oauth" in path or path.startswith(mcp_path)
    )):  # fmt: skip
        return None
    h = {k.decode().lower(): v.decode("latin-1") for k, v in scope["headers"]}
    return (
        f"{scope['method']} {path} ua={h.get('user-agent', '-')[:160]!r} origin={h.get('origin', '-')[:160]!r} "
        f"accept={h.get('accept', '-')[:160]!r} auth={'sim' if 'authorization' in h else 'não'} "
        f"ip={h.get('cf-connecting-ip', '-')}"
    )


async def _preflight(send: Send, methods: str) -> None:
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
    await send({"type": "http.response.body", "body": b""})


def _with_cors(send: Send) -> Send:
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

    return wrapped


class Cors:
    """Outermost ASGI middleware (runs before authentication)."""

    def __init__(self, app: ASGIApp, mcp_path: str = "/mcp") -> None:
        self.app, self.mcp_path = app, mcp_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        line = _diag_line(scope, path, self.mcp_path)
        methods = methods_for(path, self.mcp_path)
        if methods is None:
            if line is not None:
                print(f"hub-web: [mcp-diag] {line} (fora das rotas CORS)", flush=True)
            return await self.app(scope, receive, send)
        if line is not None:
            print(f"hub-web: [mcp-diag] {line}", flush=True)
        if scope["method"] == "OPTIONS":
            return await _preflight(send, methods)
        await self.app(scope, receive, _with_cors(send))
