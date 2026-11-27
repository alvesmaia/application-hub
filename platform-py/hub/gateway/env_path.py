"""Staging has its own URL: `/staging/apps/<id>/…` and `/staging/_frag/…` reach the same routes as production, with
the environment taken from the path (`scope["hub_env"]`), never from a cookie: a staging link opens staging in any
tab, and a production link never lands in staging. The `hub_env` cookie only sends `/apps/<id>` to the staging URL."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

PREFIX = "/staging"
_ROUTED = ("/staging/apps/", "/staging/_frag/")


class StagingPath:
    """Outermost ASGI layer: strips the prefix before routing and authentication."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "") if scope["type"] in ("http", "websocket") else ""
        if path.startswith(_ROUTED):
            raw = scope.get("raw_path") or path.encode()
            scope = {
                **scope,
                "path": path[len(PREFIX) :],
                "raw_path": raw[len(PREFIX) :],
                "hub_env": "staging",
            }
            send = _slash_redirect_in_staging(send, scope["path"])
        await self.app(scope, receive, send)


def _slash_redirect_in_staging(send: Send, path: str) -> Send:
    """The router's trailing-slash redirect (307 to the same path with/without "/") is built from the path without
    the prefix: put the prefix back, or `/staging/apps/x/` lands in production."""
    twins = {path.rstrip("/"), path.rstrip("/") + "/"} - {path}

    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start" and message["status"] == 307:
            message = {**message, "headers": [_prefixed(k, v, twins) for k, v in message.get("headers", [])]}
        await send(message)

    return wrapped


def _prefixed(key: bytes, value: bytes, twins: set[str]) -> tuple[bytes, bytes]:
    if key.lower() != b"location":
        return key, value
    loc = urlsplit(value.decode("latin-1"))
    if loc.path not in twins:
        return key, value
    return key, urlunsplit(loc._replace(path=PREFIX + loc.path)).encode("latin-1")


def env_of(scope: Any) -> str:
    """`staging` when the request came through the staging URL; else `producao`."""
    return "staging" if scope.get("hub_env") == "staging" else "producao"


def public(path: str, env: str) -> str:
    """The public URL of `path` (`/apps/…` or `/_frag/…`) in `env`."""
    return PREFIX + path if env == "staging" and not path.startswith(PREFIX + "/") else path


def to_env(path: str, env: str) -> str:
    """`next` of `/hub-env`: an app page keeps the page in the chosen environment."""
    bare = path[len(PREFIX) :] if path.startswith(PREFIX + "/apps/") else path
    return public(bare, env) if bare.startswith("/apps/") else bare
