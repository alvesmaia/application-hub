"""HTTP proxy for apps (port of hub-gateway): streaming in both directions, headers, timeouts."""

from __future__ import annotations

import asyncio
import html
import os
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from urllib.parse import unquote

import httpx
from starlette.requests import HTTPConnection, Request
from starlette.responses import HTMLResponse, Response, StreamingResponse

from hub.gateway.csp import app_url
from hub.gateway.identity import USER_SIG_HEADER, Identity, sign_user
from hub.gateway.registry import AppEntry, Registry

BASE_HEADER = "x-hub-base"
HOP_BY_HOP = frozenset(
    (
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    )
)
UNSAFE = frozenset(("POST", "PUT", "PATCH", "DELETE"))


def _env_secs(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return v if v > 0 else default


@dataclass
class GwOptions:
    connect_timeout: float = 5.0
    response_timeout: float = field(default_factory=lambda: _env_secs("HUB_UPSTREAM_TIMEOUT_SECS", 30))
    idle_timeout: float = field(default_factory=lambda: _env_secs("HUB_UPSTREAM_IDLE_SECS", 60))
    user_sig_key: bytes | None = None


# --- pure functions ---------------------------------------------------------------------


def public_location(app_id: str, loc: str) -> str | None:
    """The app's own `Location` (`/{id}`, `/{id}/...`) -> `/apps/{id}...`; others are kept as they came."""
    if not loc.startswith("/") or not loc[1:].startswith(app_id):
        return None
    rest = loc[1 + len(app_id) :]
    return f"/apps{loc}" if rest == "" or rest[0] in "/?#" else None


def has_encoded_percent(path: str) -> bool:
    return "%25" in path.lower()


def targets_internal(path: str) -> bool:
    """Any segment (decoded) is `_hub` -> the app's internal endpoint, never forwarded."""
    return any(unquote(seg).strip().lower() == "_hub" for seg in path.split("/"))


def is_hub_cookie(name: str) -> bool:
    n = name.strip().lower()
    for pre in ("__host-", "__secure-"):
        n = n.removeprefix(pre)
    return n.startswith(("hub_", "hub-")) or n == "hub"


def filter_request_cookies(header: str) -> list[str]:
    parts = (p.strip() for p in header.split(";"))
    return [p for p in parts if p and not is_hub_cookie(p.split("=", 1)[0])]


def authorization_allowed(value: str, schemes: tuple[str, ...]) -> bool:
    if "hubt_" in value:
        return False
    scheme = value.strip().split(" ", 1)[0]
    return any(scheme.lower() == s.lower() for s in schemes)


def sanitize_set_cookie(v: str) -> str | None:
    first, *attrs = (p.strip() for p in v.split(";"))
    name = first.split("=", 1)[0].strip()
    if not name or is_hub_cookie(name):
        return None
    keep = [a for a in attrs if a and a.split("=", 1)[0].strip().lower() != "domain"]
    return "; ".join([first, *keep])


def _authority(origin: str) -> str | None:
    for pre in ("https://", "http://"):
        if origin.startswith(pre):
            rest = origin[len(pre) :]
            return rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return None


def csrf_ok(headers: dict[str, str], method: str) -> bool:
    if method not in UNSAFE:
        return True
    if headers.get("sec-fetch-site", "").lower() == "same-origin":
        return True
    origin, host = headers.get("origin"), headers.get("host")
    if origin and host:
        a = _authority(origin)
        return a is not None and a.lower() == host.lower()
    return False


def friendly(
    status: int, title: str, text: str, extra: str = "", headers: dict[str, str] | None = None
) -> Response:
    body = f'<section class="empty-state"{extra}><h1>{html.escape(title)}</h1><p>{html.escape(text)}</p></section>'
    return HTMLResponse(body, status_code=status, headers=headers)


def _unavailable(app: AppEntry, status: int) -> Response:
    # `data-hub-unavailable` + Retry-After: hub.js shows "restarting" and tries again.
    return friendly(
        status,
        "Aplicação indisponível",
        f"{app.name} não respondeu; pode estar reiniciando. Tente novamente em instantes.",
        extra=' data-hub-unavailable=""',
        headers={"retry-after": "5"},
    )


def _forwarded_request_headers(
    req_headers: list[tuple[str, str]], app: AppEntry
) -> tuple[list[tuple[str, str]], list[str]]:
    """Client headers the upstream may see, plus the surviving cookies (sent joined, after them)."""
    out: list[tuple[str, str]] = []
    cookies: list[str] = []
    for k, v in req_headers:
        lk = k.lower()
        if lk.startswith("x-hub-") or lk in ("host", "traceparent", "tracestate") or lk in HOP_BY_HOP:
            continue
        if lk == "cookie":
            cookies += filter_request_cookies(v)
        elif lk != "authorization" or authorization_allowed(v, app.forward_auth_schemes):
            out.append((k, v))
    return out, cookies


def build_upstream_headers(
    req_headers: list[tuple[str, str]],
    app: AppEntry,
    identity: Identity,
    env: str,
    key: bytes | None,
    now: int | None = None,
) -> list[tuple[str, str]]:
    out, cookies = _forwarded_request_headers(req_headers, app)
    if cookies:
        out.append(("cookie", "; ".join(cookies)))
    ident = identity.header_value()
    out.append(("x-hub-user", ident))
    if key:
        out.append(
            (USER_SIG_HEADER, sign_user(key, app.id, env, ident, int(time.time()) if now is None else now))
        )
    out.append(("x-hub-env", env))
    out.append((BASE_HEADER, app_url(app.id)))
    if identity.locale:
        out.append(("x-hub-locale", identity.locale))
    return out


def _response_header(lk: str, v: str, app_id: str) -> tuple[bytes, bytes] | None:
    """One upstream header as the browser receives it, or None to drop it."""
    if lk == "set-cookie":
        c = sanitize_set_cookie(v)
        return None if c is None else (b"set-cookie", c.encode("latin-1", "replace"))
    if lk == "location":
        pub = public_location(app_id, v)
        if pub is not None:
            v = pub
    return (lk.encode("latin-1"), v.encode("latin-1", "replace"))


def filter_response_headers(raw: list[tuple[str, str]], app_id: str) -> list[tuple[bytes, bytes]]:
    listed: set[str] = set()
    for k, v in raw:
        if k.lower() == "connection":
            listed.update(t.strip().lower() for t in v.split(",") if t.strip())
    out: list[tuple[bytes, bytes]] = []
    for k, v in raw:
        lk = k.lower()
        if lk in HOP_BY_HOP or lk in listed or lk in ("date", "server") or lk.startswith("x-hub-"):
            continue
        h = _response_header(lk, v, app_id)
        if h is not None:
            out.append(h)
    return out


# --- proxy ------------------------------------------------------------------------------


class _Tripped(Exception):
    pass


class _Upload:
    """Request body streamed upstream: trips past the app's limit and signals its end (start of the deadline)."""

    def __init__(self, request: Request, limit: int) -> None:
        self.request, self.limit = request, limit
        self.done = asyncio.Event()
        self.tripped = False

    async def chunks(self) -> AsyncIterator[bytes]:
        seen = 0
        try:
            async for chunk in self.request.stream():
                seen += len(chunk)
                if seen > self.limit:
                    self.tripped = True
                    raise _Tripped
                yield chunk
        finally:
            self.done.set()


async def _relay(up: httpx.Response) -> AsyncIterator[bytes]:
    try:
        async for chunk in up.aiter_raw():  # idleness: the client's read timeout
            yield chunk
    finally:
        await up.aclose()


class Gateway:
    def __init__(
        self,
        registry: Registry,
        opts: GwOptions | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.registry = registry
        self.opts = opts or GwOptions()
        self.extra_headers: Callable[[Request, str, Identity], list[tuple[str, str]]] | None = None
        self.client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            timeout=httpx.Timeout(self.opts.idle_timeout, connect=self.opts.connect_timeout),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    def identity_for(self, request: HTTPConnection) -> Identity:
        user = getattr(request.state, "user", {"name": "", "email": "", "id": "", "roles": []})
        return Identity(
            user=user.get("email") or user["name"],
            roles=user["roles"],
            id=user["id"],
            name=user["name"],
            email=user.get("email", ""),
            permissions=getattr(request.state, "permissions", []),
            apps=getattr(request.state, "apps", None),
            locale=getattr(request.state, "lang", ""),
        )

    def pre_check(self, request: Request, app_id: str) -> tuple[AppEntry, Identity] | Response:
        """Validates path/identity/access; returns the app and the identity, or the error response."""
        raw = request.scope.get("raw_path") or request.url.path.encode()
        raw_path = raw.decode("latin-1")
        if (
            has_encoded_percent(raw_path)
            or "/../" in raw_path.lower()
            or ".." in unquote(raw_path).split("/")
        ):
            return Response(status_code=404)
        app = self.registry.get(app_id)
        if app is None or targets_internal(raw_path):
            return Response(status_code=404)
        ident = self.identity_for(request)
        if not ident.allows_app(app.id):
            return friendly(
                403,
                "Acesso negado",
                f"Você não tem acesso a {app.name}. Fale com o administrador da plataforma.",
            )
        return app, ident

    def _staging(self, request: Request, app: AppEntry, ident: Identity) -> tuple[AppEntry, str] | Response:
        """The environment to forward to: staging only with the `hub_env` cookie and the permission."""
        if request.cookies.get("hub_env") != "staging":  # staging mode (set at /hub-env); needs permission
            return app, "producao"
        if not ident.can_view_staging():
            return friendly(403, "Staging restrito", f"Você não tem permissão para ver {app.name} em staging. "
                            f"Volte para produção em /hub-env?to=producao&next=/apps/{app.id}.")  # fmt: skip
        if (up := self.registry.staging.get(app.id)) is None:
            return friendly(404, "Staging indisponível", f"{app.name} não tem versão implantada em staging. "
                            f"Volte para produção em /hub-env?to=producao&next=/apps/{app.id}.")  # fmt: skip
        return replace(app, upstream=up), "staging"

    def _admit(self, request: Request, app: AppEntry, hdrs: dict[str, str], n: int | None) -> Response | None:
        if getattr(request.state, "token", None) is None and not csrf_ok(
            hdrs, request.method
        ):  # token: no browser
            return friendly(
                403,
                "Requisição bloqueada",
                "Não foi possível confirmar que esta ação veio deste site. Recarregue a página e tente novamente.",
            )
        if n is not None and n > app.max_upload_bytes:
            return self._too_large(app)
        return None

    def _build(
        self, request: Request, app: AppEntry, ident: Identity, env: str, content: AsyncIterator[bytes] | None
    ) -> httpx.Request:
        key = self.opts.user_sig_key
        up_headers = build_upstream_headers(list(request.headers.items()), app, ident, env, key)
        if self.extra_headers:
            up_headers += self.extra_headers(request, app.id, ident)
        query = request.scope.get("query_string", b"").decode("latin-1")
        raw_path = (request.scope.get("raw_path") or request.url.path.encode()).decode("latin-1")
        # the upstream path = everything after the request's public prefix (/_frag or /apps)
        tail = raw_path.split("/", 2)[2] if raw_path.count("/") >= 2 else ""
        url = f"{app.upstream.rstrip('/')}/{tail}" + (f"?{query}" if query else "")
        return self.client.build_request(request.method, url, headers=up_headers, content=content)

    async def _deadline(self, done: asyncio.Event) -> None:
        await done.wait()
        await asyncio.sleep(self.opts.response_timeout)

    async def _send(
        self, app: AppEntry, upstream_req: httpx.Request, upload: _Upload
    ) -> httpx.Response | Response:
        send = asyncio.ensure_future(self.client.send(upstream_req, stream=True))
        dl = asyncio.ensure_future(self._deadline(upload.done))
        try:
            await asyncio.wait({send, dl}, return_when=asyncio.FIRST_COMPLETED)
            if not send.done():  # response deadline exceeded
                send.cancel()
                return _unavailable(app, 504)
            try:
                return send.result()
            except (httpx.TimeoutException, TimeoutError):
                return _unavailable(app, 504)
            except (httpx.HTTPError, _Tripped, OSError):
                if upload.tripped:
                    return self._too_large(app)
                return _unavailable(app, 502)
        finally:
            dl.cancel()

    async def forward(self, request: Request, app_id: str, rest: str) -> Response:
        """Forwards to `{upstream}/{app_id}{rest}` (rest starts with `/` or is empty)."""
        checked = self.pre_check(request, app_id)
        if isinstance(checked, Response):
            return checked
        app, ident = checked
        staged = self._staging(request, app, ident)
        if isinstance(staged, Response):
            return staged
        app, env = staged
        hdrs = {k.lower(): v for k, v in request.headers.items()}
        declared = hdrs.get("content-length")
        n = int(declared) if declared and declared.isdigit() else None
        if (denied := self._admit(request, app, hdrs, n)) is not None:
            return denied
        upload = _Upload(request, app.max_upload_bytes)
        has_body = n != 0 and (n is not None or "transfer-encoding" in hdrs)
        if not has_body:
            upload.done.set()
        upstream_req = self._build(request, app, ident, env, upload.chunks() if has_body else None)
        up = await self._send(app, upstream_req, upload)
        if isinstance(up, Response):
            return up
        resp = StreamingResponse(_relay(up), status_code=up.status_code)
        resp.raw_headers = filter_response_headers(list(up.headers.multi_items()), app.id)
        return resp

    @staticmethod
    def _too_large(app: AppEntry) -> Response:
        return friendly(
            413,
            "Arquivo muito grande",
            f"{app.name} aceita envios de até {app.max_upload_mb} MB. Reduza o arquivo e tente novamente.",
        )
