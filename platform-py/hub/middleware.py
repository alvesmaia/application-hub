"""Shell middlewares: security headers and server JSON errors translated to the user's language."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

from fastapi import Request
from fastapi.responses import Response

from hub.ux.texts import resolve, tr_json

# Same CSP as the previous hub version; 'self' covers /assets/ and /hub-sw.js.
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; "
    "img-src 'self' data: blob:; connect-src 'self'; worker-src 'self'; manifest-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)
_HEADERS = {
    "content-security-policy": _CSP,
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "same-origin",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "permissions-policy": "camera=(self), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()",
}


async def translate_errors(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Server JSON error messages in the user's language."""
    resp = await call_next(request)
    path = request.url.path
    ct = resp.headers.get("content-type", "").split(";")[0]
    if (
        resp.status_code < 400
        or ct not in ("application/json", "application/problem+json")
        or path.startswith(("/_frag/", "/assets/", "/oauth/", "/.well-known/", "/apps/"))
        or "content-disposition" in resp.headers
    ):
        return resp
    lang = getattr(request.state, "lang", None) or resolve("", request.headers.get("accept-language"))
    if lang == "pt-BR":
        return resp
    raw = b"".join([c async for c in cast(Any, resp).body_iterator])
    headers = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
    try:
        data = json.loads(raw)
        if tr_json(data, lang):
            raw = json.dumps(data, ensure_ascii=False).encode()
    except ValueError:
        pass
    headers["content-language"] = lang
    return Response(raw, resp.status_code, headers)


async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    resp = await call_next(request)
    path = request.url.path
    if path in ("/login", "/account/password", "/oauth/authorize") and resp.headers.get(
        "content-type", ""
    ).startswith("text/html"):
        # Credential screens open in their own browsing group: `window.open` from an app script
        # receives a window with no DOM access (it does not read the fields).
        resp.headers["cross-origin-opener-policy"] = "noopener-allow-popups"
    for k, v in _HEADERS.items():
        resp.headers.setdefault(k, v)
    return resp
