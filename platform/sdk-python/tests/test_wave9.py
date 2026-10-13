"""Onda 9: idioma (x-hub-locale / identidade / Accept-Language), busca federada e
aprovações pelo canal interno, notificações/pedidos no outbox."""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from hub_sdk import approvals as ap
from hub_sdk import internal, outbox
from hub_sdk import locale as loc
from hub_sdk.identity import Identity
from hub_sdk.outbox import Outbox
from hub_sdk.search import SearchHit, SearchQuery, like_pattern, sanitize, score

TOKEN = "0123456789abcdef0123"


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def ident(perms: tuple[str, ...] = (), locale: str = "") -> str:
    return Identity(user="ana@x", permissions=perms, locale=locale).encode()


def test_locale_resolution_and_formatting() -> None:
    assert loc.from_accept_language("fr,es;q=0.9,en;q=0.8") == "en"  # `es` saiu: vira en
    assert loc.from_accept_language("de, en;q=0") is None
    assert loc.from_headers({"accept-language": "en-US"}) == "en"
    assert loc.from_headers({"x-hub-user": ident(locale="en"), "accept-language": "pt-BR"}) == "en"
    assert loc.from_headers({"x-hub-locale": "pt-BR", "x-hub-user": ident(locale="en")}) == "pt-BR"
    assert loc.from_headers({}) == "pt-BR"
    tok = loc.use("en")
    try:
        assert loc.money_cents(123456) == "BRL 1,234.56"
        assert loc.date(dt.date(2026, 9, 28)) == "09/28/2026"
        assert loc.t("Voltar à plataforma") == "Back to the platform"
        assert loc.t("sem tradução {n}", n=3) == "sem tradução 3"
    finally:
        loc._current.reset(tok)
    assert loc.money_cents(123456) == "R$ 1.234,56"
    assert loc.thousands(-1200) == "-1.200"


def test_outbox_notice_meta_is_trimmed() -> None:
    e = outbox.notification(
        "ana@x", "Importação concluída", link="/contratos/contracts", to_users=["ana@x", " "], level="x"
    )
    assert e.kind == outbox.NOTIFICATION
    assert e.meta is not None and e.meta["level"] == "info" and e.meta["to_users"] == ["ana@x"]
    a = outbox.approval_request("ana@x", "ct-1", "Aprovar")
    assert a.meta is not None and a.meta["approval_id"] == "ct-1" and a.target == "ct-1"
    assert "meta" not in outbox.audit("u", "a").__dict__ or outbox.audit("u", "a").meta is None


async def test_memory_outbox_keeps_meta() -> None:
    o = Outbox.memory()
    await o.emit(outbox.approval_request("ana@x", "ct-9", "Aprovar"))
    await o.emit(outbox.audit("ana@x", "Ação"))
    p = await o.pending(0, 10)
    assert p[0].to_json()["meta"]["approval_id"] == "ct-9"
    assert "meta" not in p[1].to_json()


def test_search_helpers() -> None:
    hits = [
        SearchHit(title="a", url="/demo/x?q=1", score=0.2),
        SearchHit(title="b", url="https://evil/x", score=1.0),
        SearchHit(title="c", url="/demox/y", score=1.0),
        SearchHit(title="d", url="/demo/../admin", score=1.0),
        SearchHit(title="e", url="/demo/e", score=float("nan")),
        SearchHit(title="f", url="/demo/f", score=0.9),
    ]
    assert [h.title for h in sanitize("demo", hits, 10)] == ["f", "a", "e"]
    assert score("Gestão de Contratos", "gestao") == 0.8
    assert score("Gestão de Contratos", "contratos") == 0.6
    assert like_pattern("50%_a\\") == "%50\\%\\_a\\\\%"


def make_app() -> FastAPI:
    seen: dict[str, Any] = {}

    async def search(q: SearchQuery) -> list[SearchHit]:
        who = q.identity.user if q.identity else ""
        return [
            SearchHit(
                title=f"{q.q} {who} {q.locale} {loc.t('Voltar à plataforma')}", url="/demo/1", score=0.5
            ),
            SearchHit(title="fora", url="/admin", score=1.0),
        ]

    async def approve(d: ap.ApprovalDecision) -> str:
        seen["last"] = d
        if d.approval_id == "p-2":
            raise ap.AlreadyDecided("approved")
        if d.approval_id == "p-3":
            raise ap.Forbidden("não pode")
        if d.approval_id != "p-1":
            raise ap.NotFound
        return d.status

    app = FastAPI()
    app.add_middleware(loc.LocaleMiddleware)
    app.include_router(
        internal.router("demo", TOKEN, Outbox.memory(), None, search=search, approvals=approve)
    )

    @app.get("/demo/t")
    async def t(req: Request) -> PlainTextResponse:
        return PlainTextResponse(loc.t("Voltar à plataforma"))

    app.state.seen = seen
    return app


async def test_internal_search_uses_identity_and_locale() -> None:
    async with client(make_app()) as c:
        r = await c.get(
            "/demo/_hub/search?q=note&limit=50",
            headers={internal.TOKEN_HEADER: TOKEN, "x-hub-user": ident(), "x-hub-locale": "en"},
        )
        assert r.status_code == 200
        res = r.json()["results"]
        assert len(res) == 1 and res[0]["title"] == "note ana@x en Back to the platform"
        assert (
            await c.get("/demo/_hub/search?q=", headers={internal.TOKEN_HEADER: TOKEN})
        ).status_code == 400
        assert (await c.get("/demo/_hub/search?q=x")).status_code == 401
        # Middleware de idioma nas rotas do app.
        assert (await c.get("/demo/t", headers={"accept-language": "en"})).text == "Back to the platform"


async def test_internal_approvals_validate_and_map() -> None:
    app = make_app()
    async with client(app) as c:

        async def post(aid: str, perms: tuple[str, ...], body: Any) -> httpx.Response:
            return await c.post(
                f"/demo/_hub/approvals/{aid}",
                json=body,
                headers={internal.TOKEN_HEADER: TOKEN, "x-hub-user": ident(perms)},
            )

        ok = {"decision": "approve", "comment": "ok"}
        p = ("aprovar_solicitacoes",)
        assert (await post("p-1", (), ok)).status_code == 403
        r = await post("p-1", p, {"decision": "reject"})
        assert r.status_code == 200 and r.json()["status"] == "rejected"
        assert app.state.seen["last"].approver.user == "ana@x"
        r = await post("p-2", p, ok)
        assert r.status_code == 409 and r.json()["status"] == "approved"
        assert (await post("p-3", p, ok)).status_code == 403
        assert (await post("zz", p, ok)).status_code == 404
        assert (await post("a b", p, ok)).status_code == 400
        assert (await post("p-1", p, {"decision": "talvez"})).status_code == 422
