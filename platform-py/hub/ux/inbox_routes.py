"""Inbox (`/inbox`, `/api/me/inbox*`, SSE `/api/me/stream`), pedidos de acesso e busca (`/search`, `/api/me/search`)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any, cast
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError, field_validator

from hub.gateway.identity import Identity
from hub.ux import search as srch
from hub.ux.inbox import InboxError, InboxStore, Who
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
    step_up: str = ""


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
        """Aceita o id como a Inbox o mostra (`"i:16"`) ou só o número, como o hub Rust."""
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
    """Itens da Inbox de quem pede (com as preferências de canal e os nomes dos apps)."""
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
    """Contador e itens do sino (até 8, sem os arquivados nem os com canal desligado)."""
    entries = entries_of(request)
    return InboxStore.unread(entries), [e for e in entries if not e["archived"] and e["in_app"]][:8]


async def stream_events(
    inbox: InboxStore,
    count: Callable[[], int],
    *,
    debounce: float = 0.25,
    keepalive: float = 15.0,
) -> AsyncIterator[str]:
    """SSE: `ready` e `inbox {"unread": n}`; só reenvia quando o contador muda."""
    wake: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
    inbox.watchers.add(wake)
    try:
        last = count()
        yield "event: ready\ndata: {}\n\n"
        yield f"event: inbox\ndata: {json.dumps({'unread': last})}\n\n"
        while True:
            try:
                await asyncio.wait_for(wake.get(), keepalive)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            await asyncio.sleep(debounce)  # junta rajadas
            while not wake.empty():
                wake.get_nowait()
            if (n := count()) != last:
                last = n
                yield f"event: inbox\ndata: {json.dumps({'unread': n})}\n\n"
    finally:
        inbox.watchers.discard(wake)


def mount_inbox(
    app: FastAPI,
    templates: Jinja2Templates,
    page_ctx: Callable[[Request], dict[str, Any]],
    client: httpx.AsyncClient,
) -> None:
    inbox = cast(InboxStore, app.state.inbox)
    store = cast(UxStore, app.state.ux)

    def reg() -> Any:
        return app.state.gateway.registry

    async def audit(request: Request, title: str, detail: str) -> None:
        if admin := getattr(app.state, "admin", None):
            await admin.audit(request.state.user["name"], title, detail, icon="users")

    # ---- páginas ---------------------------------------------------------------------------
    @app.get("/inbox", response_class=HTMLResponse)
    async def inbox_page(request: Request, filter: str = "all") -> Response:
        entries = entries_of(request)
        prefs = store.prefs(request.state.user["id"])
        ctx = page_ctx(request) | {
            "items": filter_entries(entries, filter)[:200],
            "filter": filter,
            "filters": [(f, f"inbox.{f}") for f in FILTERS],
            "unread_all": InboxStore.unread(entries),
            "channels": [(k, prefs.channel(k).in_app) for k in KINDS],
            "opt_out": prefs.analytics_opt_out,
        }
        return templates.TemplateResponse(request, "inbox.html", ctx)

    @app.post("/account/notifications/read")
    async def read_form(request: Request, next: str = "/inbox") -> Response:
        ids = [int(e["id"][2:]) for e in entries_of(request) if not e["archived"]]
        inbox.set_state(who_of(request), ids, True, None)
        return RedirectResponse(next if next.startswith("/") and not next.startswith("//") else "/inbox", 303)

    @app.get("/search", response_class=HTMLResponse)
    async def search_page(request: Request, q: str = "", only_app: str = Query("", alias="app")) -> Response:
        only = only_app or None
        res = None
        if q.strip() and srch.search_allowed(request.state.user["id"]):
            res = await run_search(request, q, only, srch.PER_APP_ALL if only else srch.PER_APP)
        groups = [
            g
            | {
                "hits": [(h["url"], srch.mark_split(h["title"], q), _meta(h)) for h in g["hits"]],
                "more_label": tn("search.see_all_app", g["label"], request.state.lang)
                if g["more_url"]
                else "",
            }
            for g in (res.groups if res else [])
        ]
        ctx = page_ctx(request) | {
            "q": q,
            "record": "" if not q.strip() or only else q.strip(),
            "back": f"/search?q={quote(q.strip())}" if only else "",
            "partial": bool(res and res.partial),
            "no_results": bool(res and res.total == 0),
            "none_text": tn("search.none", q, request.state.lang),
            "groups": groups,
        }
        return templates.TemplateResponse(request, "search.html", ctx)

    # ---- API: Inbox ---------------------------------------------------------------------------
    @app.get("/api/me/inbox")
    async def inbox_list(request: Request, filter: str = "all") -> Response:
        entries = entries_of(request)
        return JSONResponse(
            {"unread": InboxStore.unread(entries), "items": filter_entries(entries, filter)[:200]}
        )

    def ids_of(raw: list[str] | None) -> list[int]:
        return [int(s[2:]) for s in (raw or [])[:1000] if s.startswith("i:") and s[2:].isdigit()]

    async def body_of(request: Request, model: type[BaseModel]) -> Any:
        try:
            return model.model_validate(await request.json())
        except (ValidationError, ValueError):
            return None

    @app.post("/api/me/inbox/read")
    async def inbox_read(request: Request) -> Response:
        b = await body_of(request, IdsIn)
        ids = ids_of(b.ids) if b and b.ids is not None else [int(e["id"][2:]) for e in entries_of(request)]
        return JSONResponse({"marked": inbox.set_state(who_of(request), ids, True, None)})

    @app.post("/api/me/inbox/archive")
    async def inbox_archive(request: Request) -> Response:
        b = await body_of(request, IdsIn)
        if b is None:
            return err(400, "Dados inválidos.")
        on = True if b.archived is None else b.archived
        return JSONResponse({"changed": inbox.set_state(who_of(request), ids_of(b.ids), None, on)})

    async def call_app(it: dict[str, Any], request: Request, who: Who, decision: str, comment: str) -> str:
        """Avisa o app (`POST /_hub/approvals/{id}`); devolve o resultado final dele ou levanta `InboxError`."""
        entry = reg().get(it["app"])
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
            r = await client.post(
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

    async def decide(request: Request, who: Who, item_id: int, b: DecideIn) -> str:
        it = inbox.item(item_id)
        comment = "".join(c for c in b.comment if c.isprintable())[:500]
        if b.decision == "approve" and it and it["kind"] == "agent":
            h = (it["agent"] or {}).get("args_hash", "")
            if not b.step_up or not h or not inbox.stepup_consume(b.step_up, who.id, item_id, h):
                await audit(
                    request,
                    "Aprovação de ação do agente recusada",
                    f"pedido i:{item_id} · reautenticação ausente",
                )
                raise InboxError(
                    403,
                    "Aprovar uma ação do agente exige confirmar sua identidade de novo: "
                    f"abra /account/agent-approval/{item_id}.",
                )
        target = inbox.items.get(item_id)
        entry = request.app.state.gateway.registry.get(target["app"]) if target else None
        it = inbox.begin(who, item_id, b.decision, comment, entry.name if entry else "")
        if it["kind"] == "approval":
            try:
                final = await call_app(it, request, who, b.decision, comment)
            except InboxError as e:
                inbox.finish(item_id, None, e.message)
                raise
            inbox.finish(item_id, final)
            if final != ("approved" if b.decision == "approve" else "rejected"):
                inbox.finish(item_id, None, "O resultado registrado pelo app diverge da sua decisão.")
                raise InboxError(502, "O resultado registrado pelo app diverge da sua decisão.")
        status = str(inbox.items[item_id]["approval"]["status"])
        inbox.set_state(who, [item_id], True, None)
        await audit(
            request, f"Pedido {'aprovado' if status == 'approved' else 'recusado'} pela Inbox", it["title"]
        )
        return status

    @app.post("/api/me/inbox/{item_id}/decide")
    async def inbox_decide(request: Request, item_id: str) -> Response:
        b = await body_of(request, DecideIn)
        n = item_id.removeprefix("i:")
        if not n.isdigit():
            return err(400, "Id inválido.")
        if b is None:
            return err(400, "Dados inválidos.")
        try:
            status = await decide(request, who_of(request), int(n), b)
        except InboxError as e:
            return err(e.status, e.message)
        return JSONResponse({"ok": True, "status": status})

    app.state.inbox_decide = decide

    @app.get("/api/me/stream")
    async def stream(request: Request) -> Response:
        uid = request.state.user["id"]
        if inbox.streams.get(uid, 0) >= MAX_STREAMS:
            return err(429, "Muitas conexões ao vivo abertas para este usuário.")
        inbox.streams[uid] = inbox.streams.get(uid, 0) + 1

        async def events() -> AsyncIterator[str]:
            try:
                async for ev in stream_events(inbox, lambda: InboxStore.unread(entries_of(request))):
                    yield ev
            finally:
                inbox.streams[uid] -= 1

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )

    @app.post("/api/me/access-requests")
    async def access_request(request: Request) -> Response:
        b = await body_of(request, AccessIn)
        if b is None:
            return err(400, "Dados inválidos.")
        entry = reg().get(b.app)
        if entry is None:
            return err(404, "Aplicação desconhecida.")
        who = who_of(request)
        if who.can_app(entry.id):
            return err(409, "Você já tem acesso a esta aplicação.")
        try:
            item_id = inbox.request_access(who, entry.id, entry.name, b.reason)
        except InboxError as e:
            return err(e.status, e.message)
        return JSONResponse({"ok": True, "id": f"i:{item_id}"}, status_code=201)

    @app.post("/api/admin/grants/revoke")
    async def revoke_grant(request: Request) -> Response:
        b = await body_of(request, RevokeIn)
        if b is None:
            return err(400, "Dados inválidos.")
        if "gerenciar_usuarios" not in request.state.permissions or not inbox.revoke_grant(b.item):
            return err(404, "Concessão não encontrada.")
        await audit(request, "Acesso concedido por pedido revogado", f"pedido i:{b.item}")
        return JSONResponse({"ok": True})

    # ---- API: busca ------------------------------------------------------------------------------
    async def run_search(request: Request, q: str, only: str | None, per_app: int) -> srch.Results:
        who = who_of(request)
        users = []
        if "gerenciar_usuarios" in who.perms:
            async with app.state.auth.locked() as d:
                users = [{"name": u["name"], "email": u["email"]} for u in d["users"] if u["active"]]
        gw = app.state.gateway
        ctx = srch.Ctx(
            identity=gw.identity_for(request),
            perms=who.perms,
            superuser="Administrador" in who.roles,
            reg=gw.registry,
            client=gw.client,
            nav=nav_for("pt-BR"),
            lang=request.state.lang,
            data_dir=app.state.data_dir,
            users=users,
        )
        return await srch.search(ctx, q, only, per_app)

    def too_many() -> JSONResponse:
        return err(429, "Muitas buscas seguidas; aguarde um instante.", **{"retry-after": "1"})

    @app.get("/api/me/search")
    @app.get("/api/search")
    async def api_search(request: Request, q: str = "") -> Response:
        text = q.strip()
        if not text:
            return JSONResponse(
                {"q": "", "groups": [], "recent": store.prefs(request.state.user["id"]).searches}
            )
        if not srch.search_allowed(request.state.user["id"]):
            return too_many()
        return JSONResponse((await run_search(request, text, None, srch.PER_APP)).json())

    @app.post("/api/me/search")
    async def search_final(request: Request) -> Response:
        b = await body_of(request, QueryIn)
        text = b.q.strip()[:120] if b else ""
        if not text:
            return err(400, "Informe o termo.")
        if not srch.search_allowed(request.state.user["id"]):
            return too_many()
        res = await run_search(request, text, None, srch.PER_APP)
        uid = request.state.user["id"]
        store.remember_search(uid, text)
        store.step(uid, "search")
        request.app.state.insight.analytics.record(uid, "search", text, n=res.total)
        return JSONResponse({"ok": True, "total": res.total})


def _meta(h: dict[str, Any]) -> str:
    return f"{h['kind']} · {h['subtitle']}" if h["subtitle"] else str(h["kind"])
