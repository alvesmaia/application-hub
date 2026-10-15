"""Inbox (`/inbox`, `/api/me/inbox*`, SSE `/api/me/stream`), access requests and search (`/search`, `/api/me/search`)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError, field_validator

from hub.gateway.identity import Identity
from hub.ux import search as srch
from hub.ux.inbox import InboxError, InboxStore, Who
from hub.ux.pager import paginate
from hub.ux.shell import nav_for
from hub.ux.store import KINDS, UxStore
from hub.ux.texts import tn

MAX_STREAMS = 12
FILTERS = ("all", "unread", "approvals", "notifications", "alerts", "archived")


class IdsIn(BaseModel):
    ids: list[str] | None = None
    archived: bool | None = None


class DecideIn(BaseModel):
    decision: str
    comment: str = ""


class AccessIn(BaseModel):
    app: str
    reason: str = ""


class QueryIn(BaseModel):
    q: str


class RevokeIn(BaseModel):
    item: int

    @field_validator("item", mode="before")
    @classmethod
    def _item_id(cls, v: object) -> object:
        """Accepts the id as the Inbox shows it (`"i:16"`) or just the number."""
        return int(v.removeprefix("i:")) if isinstance(v, str) and v.removeprefix("i:").isdigit() else v


def err(status: int, msg: str, **headers: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status, headers=headers)


def who_of(request: Request) -> Who:
    u = request.state.user
    return Who(
        id=u["id"],
        email=u.get("email", ""),
        name=u["name"],
        roles=u["roles"],
        perms=request.state.permissions,
        apps=request.state.apps,
        session=request.state.token is None,
    )


def entries_of(request: Request) -> list[dict[str, Any]]:
    """Inbox items of the requester (with channel preferences and app names)."""
    st = cast(UxStore, request.app.state.ux)
    inbox = cast(InboxStore, request.app.state.inbox)
    who, prefs = who_of(request), st.prefs(request.state.user["id"])
    reg = request.app.state.gateway.registry

    def name(app_id: str) -> str:
        e = reg.get(app_id)
        return str(e.name) if e else app_id

    return inbox.entries(who, request.state.lang, lambda k: prefs.channel(k).in_app, name)


def filter_entries(entries: list[dict[str, Any]], f: str) -> list[dict[str, Any]]:
    keep: dict[str, Callable[[dict[str, Any]], bool]] = {
        "archived": lambda e: e["archived"],
        "unread": lambda e: not e["archived"] and not e["read"],
        "approvals": lambda e: not e["archived"] and e["approval"] is not None,
        "alerts": lambda e: not e["archived"] and e["kind"] == "alert",
        "notifications": lambda e: not e["archived"] and e["kind"] == "notification",
    }
    return [e for e in entries if keep.get(f, lambda x: not x["archived"])(e)]


def bell_of(request: Request) -> tuple[int, list[dict[str, Any]]]:
    """Bell counter and items (up to 8, excluding archived ones and those with the channel off)."""
    entries = entries_of(request)
    return InboxStore.unread(entries), [e for e in entries if not e["archived"] and e["in_app"]][:8]


async def _woken(wake: asyncio.Queue[None], debounce: float, keepalive: float) -> bool:
    """False on keepalive timeout; True once woken, after a burst of wake-ups is drained."""
    try:
        await asyncio.wait_for(wake.get(), keepalive)
    except TimeoutError:
        return False
    await asyncio.sleep(debounce)  # junta rajadas
    while not wake.empty():
        wake.get_nowait()
    return True


async def stream_events(
    inbox: InboxStore,
    count: Callable[[], int],
    *,
    debounce: float = 0.25,
    keepalive: float = 15.0,
) -> AsyncIterator[str]:
    """SSE: `ready` and `inbox {"unread": n}`; only resends when the counter changes."""
    wake: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
    inbox.watchers.add(wake)
    try:
        last = count()
        yield "event: ready\ndata: {}\n\n"
        yield f"event: inbox\ndata: {json.dumps({'unread': last})}\n\n"
        while True:
            if not await _woken(wake, debounce, keepalive):
                yield ": keepalive\n\n"
                continue
            if (n := count()) != last:
                last = n
                yield f"event: inbox\ndata: {json.dumps({'unread': n})}\n\n"
    finally:
        inbox.watchers.discard(wake)


@dataclass
class InboxCtx:
    """Inbox and search dependencies (`app.state.inbox_ctx`); route handlers get it with `Depends(ctx)`."""

    app: FastAPI
    templates: Jinja2Templates
    page_ctx: Callable[[Request], dict[str, Any]]
    client: httpx.AsyncClient
    inbox: InboxStore
    store: UxStore

    async def audit(self, request: Request, title: str, detail: str) -> None:
        if admin := getattr(self.app.state, "admin", None):
            await admin.audit(request.state.user["name"], title, detail, icon="users")

    async def call_app(
        self, it: dict[str, Any], request: Request, who: Who, decision: str, comment: str
    ) -> str:
        """Notifies the app (`POST /_hub/approvals/{id}`); returns its final result or raises `InboxError`."""
        entry = self.app.state.gateway.registry.get(it["app"])
        token = srch.internal_token(it["app"])
        if entry is None or token is None:
            raise InboxError(502, "Aplicação sem token interno.")
        ident = Identity(
            user=who.email or who.name,
            roles=who.roles,
            id=who.id,
            name=who.name,
            email=who.email,
            permissions=who.perms,
            locale=request.state.lang,
        )
        url = f"{entry.upstream.rstrip('/')}/{entry.id}/_hub/approvals/{it['approval']['ext_id']}"
        body = {
            "decision": decision,
            "comment": comment,
            "event_id": it["source"].partition(":")[2],
            "decided_at": it["approval"]["decided_at"],
        }
        try:
            r = await self.client.post(
                url,
                json=body,
                timeout=8,
                headers={"x-hub-internal-token": token, "x-hub-user": ident.header_value()},
            )
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise InboxError(502, f"o app não respondeu ({type(e).__name__})") from e
        status = str(data.get("status", "")) if isinstance(data, dict) else ""
        if r.status_code in (200, 409) and status in ("approved", "rejected"):
            return status
        raise InboxError(
            502,
            f"o app respondeu {r.status_code}: {data.get('error', 'resposta inesperada') if isinstance(data, dict) else ''}",
        )

    async def decide(self, request: Request, who: Who, item_id: int, b: DecideIn) -> str:
        comment = "".join(c for c in b.comment if c.isprintable())[:500]
        target = self.inbox.items.get(item_id)
        entry = request.app.state.gateway.registry.get(target["app"]) if target else None
        it = self.inbox.begin(who, item_id, b.decision, comment, entry.name if entry else "")
        if it["kind"] == "approval":
            try:
                final = await self.call_app(it, request, who, b.decision, comment)
            except InboxError as e:
                self.inbox.finish(item_id, None, e.message)
                raise
            self.inbox.finish(item_id, final)
            if final != ("approved" if b.decision == "approve" else "rejected"):
                self.inbox.finish(item_id, None, "O resultado registrado pelo app diverge da sua decisão.")
                raise InboxError(502, "O resultado registrado pelo app diverge da sua decisão.")
        status = str(self.inbox.items[item_id]["approval"]["status"])
        self.inbox.set_state(who, [item_id], True, None)
        await self.audit(
            request, f"Pedido {'aprovado' if status == 'approved' else 'recusado'} pela Inbox", it["title"]
        )
        return status

    async def run_search(self, request: Request, q: str, only: str | None, per_app: int) -> srch.Results:
        who = who_of(request)
        users = []
        if "gerenciar_usuarios" in who.perms:
            async with self.app.state.auth.locked() as d:
                users = [{"name": u["name"], "email": u["email"]} for u in d["users"] if u["active"]]
        gw = self.app.state.gateway
        ctx = srch.Ctx(
            identity=gw.identity_for(request),
            perms=who.perms,
            superuser="Administrador" in who.roles,
            reg=gw.registry,
            client=gw.client,
            nav=nav_for("pt-BR"),
            lang=request.state.lang,
            data_dir=self.app.state.data_dir,
            users=users,
        )
        return await srch.search(ctx, q, only, per_app)


def ctx(request: Request) -> InboxCtx:
    return cast(InboxCtx, request.app.state.inbox_ctx)


def ids_of(raw: list[str] | None) -> list[int]:
    return [int(s[2:]) for s in (raw or [])[:1000] if s.startswith("i:") and s[2:].isdigit()]


async def body_of(request: Request, model: type[BaseModel]) -> Any:
    try:
        return model.model_validate(await request.json())
    except (ValidationError, ValueError):
        return None


def too_many() -> JSONResponse:
    return err(429, "Muitas buscas seguidas; aguarde um instante.", **{"retry-after": "1"})


router = APIRouter()


# ---- pages ------------------------------------------------------------------------------
@router.get("/inbox", response_class=HTMLResponse)
async def inbox_page(request: Request, filter: str = "all", c: InboxCtx = Depends(ctx)) -> Response:
    entries = entries_of(request)
    prefs = c.store.prefs(request.state.user["id"])
    items, pager = paginate(request, filter_entries(entries, filter))
    page = c.page_ctx(request) | {
        "items": items,
        "pager": pager,
        "filter": filter,
        "filters": [(f, f"inbox.{f}") for f in FILTERS],
        "unread_all": InboxStore.unread(entries),
        "channels": [(k, prefs.channel(k).in_app) for k in KINDS],
        "opt_out": prefs.analytics_opt_out,
    }
    return c.templates.TemplateResponse(request, "inbox.html", page)


@router.post("/account/notifications/read")
async def read_form(request: Request, next: str = "/inbox", c: InboxCtx = Depends(ctx)) -> Response:
    ids = [int(e["id"][2:]) for e in entries_of(request) if not e["archived"]]
    c.inbox.set_state(who_of(request), ids, True, None)
    return RedirectResponse(next if next.startswith("/") and not next.startswith("//") else "/inbox", 303)


@router.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request, q: str = "", only_app: str = Query("", alias="app"), c: InboxCtx = Depends(ctx)
) -> Response:
    only = only_app or None
    res = None
    if q.strip() and srch.search_allowed(request.state.user["id"]):
        res = await c.run_search(request, q, only, srch.PER_APP_ALL if only else srch.PER_APP)
    groups = [
        g
        | {
            "hits": [(h["url"], srch.mark_split(h["title"], q), _meta(h)) for h in g["hits"]],
            "more_label": tn("search.see_all_app", g["label"], request.state.lang) if g["more_url"] else "",
        }
        for g in (res.groups if res else [])
    ]
    page = c.page_ctx(request) | {
        "q": q,
        "record": "" if not q.strip() or only else q.strip(),
        "back": f"/search?q={quote(q.strip())}" if only else "",
        "partial": bool(res and res.partial),
        "no_results": bool(res and res.total == 0),
        "none_text": tn("search.none", q, request.state.lang),
        "groups": groups,
    }
    return c.templates.TemplateResponse(request, "search.html", page)


# ---- API: Inbox ---------------------------------------------------------------------------
@router.get("/api/me/inbox")
async def inbox_list(request: Request, filter: str = "all") -> Response:
    entries = entries_of(request)
    return JSONResponse(
        {"unread": InboxStore.unread(entries), "items": filter_entries(entries, filter)[:200]}
    )


@router.post("/api/me/inbox/read")
async def inbox_read(request: Request, c: InboxCtx = Depends(ctx)) -> Response:
    b = await body_of(request, IdsIn)
    ids = ids_of(b.ids) if b and b.ids is not None else [int(e["id"][2:]) for e in entries_of(request)]
    return JSONResponse({"marked": c.inbox.set_state(who_of(request), ids, True, None)})


@router.post("/api/me/inbox/archive")
async def inbox_archive(request: Request, c: InboxCtx = Depends(ctx)) -> Response:
    b = await body_of(request, IdsIn)
    if b is None:
        return err(400, "Dados inválidos.")
    on = True if b.archived is None else b.archived
    return JSONResponse({"changed": c.inbox.set_state(who_of(request), ids_of(b.ids), None, on)})


@router.post("/api/me/inbox/{item_id}/decide")
async def inbox_decide(request: Request, item_id: str, c: InboxCtx = Depends(ctx)) -> Response:
    b = await body_of(request, DecideIn)
    n = item_id.removeprefix("i:")
    if not n.isdigit():
        return err(400, "Id inválido.")
    if b is None:
        return err(400, "Dados inválidos.")
    try:
        status = await c.decide(request, who_of(request), int(n), b)
    except InboxError as e:
        return err(e.status, e.message)
    return JSONResponse({"ok": True, "status": status})


@router.get("/api/me/stream")
async def stream(request: Request, c: InboxCtx = Depends(ctx)) -> Response:
    uid = request.state.user["id"]
    if c.inbox.streams.get(uid, 0) >= MAX_STREAMS:
        return err(429, "Muitas conexões ao vivo abertas para este usuário.")
    c.inbox.streams[uid] = c.inbox.streams.get(uid, 0) + 1

    async def events() -> AsyncIterator[str]:
        try:
            async for ev in stream_events(c.inbox, lambda: InboxStore.unread(entries_of(request))):
                yield ev
        finally:
            c.inbox.streams[uid] -= 1

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


@router.post("/api/me/access-requests")
async def access_request(request: Request, c: InboxCtx = Depends(ctx)) -> Response:
    b = await body_of(request, AccessIn)
    if b is None:
        return err(400, "Dados inválidos.")
    entry = request.app.state.gateway.registry.get(b.app)
    if entry is None:
        return err(404, "Aplicação desconhecida.")
    who = who_of(request)
    if who.can_app(entry.id):
        return err(409, "Você já tem acesso a esta aplicação.")
    try:
        item_id = c.inbox.request_access(who, entry.id, entry.name, b.reason)
    except InboxError as e:
        return err(e.status, e.message)
    return JSONResponse({"ok": True, "id": f"i:{item_id}"}, status_code=201)


@router.post("/api/admin/grants/revoke")
async def revoke_grant(request: Request, c: InboxCtx = Depends(ctx)) -> Response:
    b = await body_of(request, RevokeIn)
    if b is None:
        return err(400, "Dados inválidos.")
    if "gerenciar_usuarios" not in request.state.permissions or not c.inbox.revoke_grant(b.item):
        return err(404, "Concessão não encontrada.")
    await c.audit(request, "Acesso concedido por pedido revogado", f"pedido i:{b.item}")
    return JSONResponse({"ok": True})


# ---- API: busca ------------------------------------------------------------------------------
@router.get("/api/me/search")
@router.get("/api/search")
async def api_search(request: Request, q: str = "", c: InboxCtx = Depends(ctx)) -> Response:
    text = q.strip()
    if not text:
        return JSONResponse(
            {"q": "", "groups": [], "recent": c.store.prefs(request.state.user["id"]).searches}
        )
    if not srch.search_allowed(request.state.user["id"]):
        return too_many()
    return JSONResponse((await c.run_search(request, text, None, srch.PER_APP)).json())


@router.post("/api/me/search")
async def search_final(request: Request, c: InboxCtx = Depends(ctx)) -> Response:
    b = await body_of(request, QueryIn)
    text = b.q.strip()[:120] if b else ""
    if not text:
        return err(400, "Informe o termo.")
    if not srch.search_allowed(request.state.user["id"]):
        return too_many()
    res = await c.run_search(request, text, None, srch.PER_APP)
    uid = request.state.user["id"]
    c.store.remember_search(uid, text)
    c.store.step(uid, "search")
    request.app.state.insight.analytics.record(uid, "search", text, n=res.total)
    return JSONResponse({"ok": True, "total": res.total})


def mount_inbox(
    app: FastAPI,
    templates: Jinja2Templates,
    page_ctx: Callable[[Request], dict[str, Any]],
    client: httpx.AsyncClient,
) -> None:
    c = InboxCtx(
        app, templates, page_ctx, client, cast(InboxStore, app.state.inbox), cast(UxStore, app.state.ux)
    )
    app.state.inbox_ctx = c
    app.state.inbox_decide = c.decide
    app.include_router(router)


def _meta(h: dict[str, Any]) -> str:
    return f"{h['kind']} · {h['subtitle']}" if h["subtitle"] else str(h["kind"])
