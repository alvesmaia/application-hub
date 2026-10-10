"""Proxy HTTP dos apps (port de hub-gateway): streaming nos dois sentidos, cabeçalhos, timeouts."""

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


# --- funções puras --------------------------------------------------------------------


def public_location(app_id: str, loc: str) -> str | None:
    """`Location` do próprio app (`/{id}`, `/{id}/...`) -> `/apps/{id}...`; outros ficam como vieram."""
    if not loc.startswith("/") or not loc[1:].startswith(app_id):
        return None
    rest = loc[1 + len(app_id) :]
    return f"/apps{loc}" if rest == "" or rest[0] in "/?#" else None


def has_encoded_percent(path: str) -> bool:
    return "%25" in path.lower()


def targets_internal(path: str) -> bool:
    """Algum segmento (decodificado) é `_hub` -> endpoint interno do app, nunca encaminhado."""
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
    # `data-hub-unavailable` + Retry-After: o hub.js mostra "reiniciando" e tenta de novo.
    return friendly(
        status,
        "Aplicação indisponível",
        f"{app.name} não respondeu — pode estar reiniciando. Tente novamente em instantes.",
        extra=' data-hub-unavailable=""',
        headers={"retry-after": "5"},
    )


def build_upstream_headers(
    req_headers: list[tuple[str, str]],
    app: AppEntry,
    identity: Identity,
    env: str,
    key: bytes | None,
    now: int | None = None,
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    cookies: list[str] = []
    for k, v in req_headers:
        lk = k.lower()
        if lk.startswith("x-hub-") or lk in ("host", "traceparent", "tracestate") or lk in HOP_BY_HOP:
            continue
        if lk == "cookie":
            cookies += filter_request_cookies(v)
        elif lk == "authorization":
            if authorization_allowed(v, app.forward_auth_schemes):
                out.append((k, v))
        else:
            out.append((k, v))
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
        if lk == "set-cookie":
            c = sanitize_set_cookie(v)
            if c is not None:
                out.append((b"set-cookie", c.encode("latin-1", "replace")))
            continue
        if lk == "location":
            pub = public_location(app_id, v)
            if pub is not None:
                v = pub
        out.append((lk.encode("latin-1"), v.encode("latin-1", "replace")))
    return out


# --- proxy ------------------------------------------------------------------------------


class _Tripped(Exception):
    pass


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
        """Valida caminho/identidade/acesso; devolve o app e a identidade ou a resposta de erro."""
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

    async def forward(self, request: Request, app_id: str, rest: str) -> Response:
        """Encaminha para `{upstream}/{app_id}{rest}` (rest começa com `/` ou é vazio)."""
        checked = self.pre_check(request, app_id)
        if isinstance(checked, Response):
            return checked
        app, ident = checked
        env = "producao"
        if request.cookies.get("hub_env") == "staging":  # modo staging (ligado em /hub-env), só com permissão
            if not ident.can_view_staging():
                return friendly(403, "Staging restrito", f"Você não tem permissão para ver {app.name} em staging. "
                                f"Volte para produção em /hub-env?to=producao&next=/apps/{app.id}.")  # fmt: skip
            if (up := self.registry.staging.get(app.id)) is None:
                return friendly(404, "Staging indisponível", f"{app.name} não tem versão implantada em staging. "
                                f"Volte para produção em /hub-env?to=producao&next=/apps/{app.id}.")  # fmt: skip
            app, env = replace(app, upstream=up), "staging"
        hdrs = {k.lower(): v for k, v in request.headers.items()}
        method = request.method
        if getattr(request.state, "token", None) is None and not csrf_ok(
            hdrs, method
        ):  # token: sem navegador
            return friendly(
                403,
                "Requisição bloqueada",
                "Não foi possível confirmar que esta ação veio deste site. Recarregue a página e tente novamente.",
            )
        limit = app.max_upload_bytes
        declared = hdrs.get("content-length")
        n = int(declared) if declared and declared.isdigit() else None
        if n is not None and n > limit:
            return self._too_large(app)

        key = self.opts.user_sig_key
        up_headers = build_upstream_headers(list(request.headers.items()), app, ident, env, key)
        if self.extra_headers:
            up_headers += self.extra_headers(request, app.id, ident)
        query = request.scope.get("query_string", b"").decode("latin-1")
        raw_path = (request.scope.get("raw_path") or request.url.path.encode()).decode("latin-1")
        # o caminho do upstream = tudo depois do prefixo público (/_frag ou /apps) do pedido
        tail = raw_path.split("/", 2)[2] if raw_path.count("/") >= 2 else ""
        url = f"{app.upstream.rstrip('/')}/{tail}" + (f"?{query}" if query else "")

        done = asyncio.Event()
        tripped = False

        async def body() -> AsyncIterator[bytes]:
            nonlocal tripped
            seen = 0
            try:
                async for chunk in request.stream():
                    seen += len(chunk)
                    if seen > limit:
                        tripped = True
                        raise _Tripped
                    yield chunk
            finally:
                done.set()

        has_body = n != 0 and (n is not None or "transfer-encoding" in hdrs)
        if not has_body:
            done.set()
        upstream_req = self.client.build_request(
            method, url, headers=up_headers, content=body() if has_body else None
        )
        send = asyncio.ensure_future(self.client.send(upstream_req, stream=True))

        async def deadline() -> None:
            await done.wait()
            await asyncio.sleep(self.opts.response_timeout)

        dl = asyncio.ensure_future(deadline())
        try:
            await asyncio.wait({send, dl}, return_when=asyncio.FIRST_COMPLETED)
            if not send.done():  # prazo da resposta estourou
                send.cancel()
                return _unavailable(app, 504)
            try:
                up = send.result()
            except (httpx.TimeoutException, TimeoutError):
                return _unavailable(app, 504)
            except (httpx.HTTPError, _Tripped, OSError):
                if tripped:
                    return self._too_large(app)
                return _unavailable(app, 502)
        finally:
            dl.cancel()

        async def stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in up.aiter_raw():  # ociosidade: read timeout do cliente
                    yield chunk
            finally:
                await up.aclose()

        resp = StreamingResponse(stream(), status_code=up.status_code)
        resp.raw_headers = filter_response_headers(list(up.headers.multi_items()), app.id)
        return resp

    @staticmethod
    def _too_large(app: AppEntry) -> Response:
        return friendly(
            413,
            "Arquivo muito grande",
            f"{app.name} aceita envios de até {app.max_upload_mb} MB. Reduza o arquivo e tente novamente.",
        )
