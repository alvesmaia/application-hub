"""__APP_NAME__ — app da Application Hub (FastAPI + hub_sdk, SSR com Jinja2, sem JavaScript próprio).

Rotas servidas em ``/__APP_ID__/…`` (o navegador vê ``/apps/__APP_ID__/…``; o hub busca os fragmentos em
``/_frag/__APP_ID__/…``). Links sempre com a base pública (``x-hub-base``, variável ``base`` nos templates).
API JSON em ``/__APP_ID__/api/v1/*`` (OpenAPI em ``/__APP_ID__/api/openapi.json``; vira ferramenta MCP no hub).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, Response
from hub_sdk import dashboards as dash
from hub_sdk import db
from hub_sdk.api import install_api, require_user
from hub_sdk.app import HubSdk
from hub_sdk.render import AppInfo, NavItem, Renderer
from hub_sdk.storage import StorageError
from pydantic import BaseModel, Field, ValidationError
from starlette.requests import HTTPConnection

from __PKG__ import painel, store

log = logging.getLogger("__PKG__")

APP_ID = "__APP_ID__"
VERSION = "0.1.0"
HERE = Path(__file__).resolve().parent
# demo.css = estilos padrão dos fragmentos (sidebar, painéis, formulários); app.css = os do app.
ASSETS = ("demo.css", "app.css")

INFO = AppInfo(
    id=APP_ID,
    name="__APP_NAME__",
    tagline="App da plataforma",
    icon="dashboard",
    color="#e6efff",
    ink="#1d4ed8",
    nav=(
        NavItem("inicio", "Início", "dashboard"),
        NavItem("painel", "Painel", "chart"),
        NavItem("itens", "Itens", "file"),
    ),
)


class ItemIn(BaseModel):
    """Entrada de item (formulário e API) — validada nas bordas."""

    name: str = Field(min_length=1, max_length=80)
    quantity: int = Field(ge=0, le=1_000_000)


class ItemOut(BaseModel):
    id: int
    name: str
    quantity: int


def public_base(req: HTTPConnection) -> str:
    """Base pública do app (``/apps/__APP_ID__``) — cabeçalho ``x-hub-base`` do hub."""
    b = req.headers.get("x-hub-base", "").strip()
    ok = b.startswith("/apps/") and all(c.isalnum() or c in "/-_" for c in b)
    return b if ok else f"/apps/{APP_ID}"


def create_app() -> FastAPI:
    sdk = HubSdk.from_env(APP_ID)
    render = Renderer(INFO, HERE / "templates")
    engine = db.engine()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        db.upgrade(HERE / "migrations", str(engine.url.render_as_string(hide_password=False)))
        async with sdk.lifespan(app):
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    sdk.install(app)

    def page(req: Request, template: str, active: str, status: int = 200, **ctx: Any) -> Response:
        return render.page(req, template, active, status, base=public_base(req), **ctx)

    @app.get(f"/{APP_ID}/assets/{{name}}")
    async def asset(name: str) -> Response:
        if name not in ASSETS:  # lista fechada: nenhum caminho vindo do cliente
            return render.not_found()
        css = (HERE / "assets" / name).read_bytes()
        if name == "app.css":  # o shell carrega só demo.css + app.css: o CSS dos dashboards vai junto
            css += b"\n" + dash.CSS.encode()
        return Response(css, media_type="text/css; charset=utf-8", headers={"Cache-Control": "no-cache"})

    @app.get(f"/{APP_ID}/_skeleton")
    async def skeleton() -> Response:
        return render.skeleton()

    @app.get(f"/{APP_ID}")
    @app.get(f"/{APP_ID}/")
    @app.get(f"/{APP_ID}/inicio")
    def inicio(req: Request) -> Response:
        items = store.list_items(engine)
        total = sum(i["quantity"] for i in items)
        return page(req, "inicio.html", "inicio", count=len(items), total=total)

    @app.get(f"/{APP_ID}/painel")
    async def painel_page(req: Request) -> Response:
        values = painel.BOARD.values(req.query_params)
        base = public_base(req)
        ctx: dict[str, Any] = {"values": values, "error": ""}
        try:
            ctx |= await painel.view(values)
        except StorageError as e:  # conexão não vinculada / banco fora do ar: a tela explica, sem dados
            log.warning("painel: %s (HTTP %s)", e.message, e.status)
            ctx["error"] = "Conexão de dados indisponível. Vincule a conexão vendas em Conexões."
        ctx["filters"] = dash.filters_form(painel.BOARD.params, values, f"{base}/painel")
        ctx["export"] = dash.export_links(f"/_frag/{APP_ID}/painel/exportar", values)
        return page(req, "painel.html", "painel", **ctx)  # 200 com aviso (503 = "app reiniciando" no shell)

    @app.get(f"/{APP_ID}/painel/exportar")
    async def painel_export(req: Request, format: str = "csv") -> Response:
        values = painel.BOARD.values(req.query_params)
        try:
            r = await painel.BOARD.load("detalhe", values)
        except StorageError as e:
            return JSONResponse({"error": e.message}, status_code=e.status)
        return dash.export_response(r, "xlsx" if format == "xlsx" else "csv", "vendas")

    @app.get(f"/{APP_ID}/itens")
    def itens(req: Request) -> Response:
        return page(req, "itens.html", "itens", items=store.list_items(engine), error="")

    @app.post(f"/{APP_ID}/itens")
    def criar(
        req: Request, name: Annotated[str, Form()] = "", quantity: Annotated[str, Form()] = "0"
    ) -> Response:
        try:
            item = ItemIn(name=name.strip(), quantity=int(quantity or "0"))
        except (ValidationError, ValueError):
            items = store.list_items(engine)
            error = "Informe nome e quantidade válidos."
            return page(req, "itens.html", "itens", 422, items=items, error=error)
        store.add_item(engine, item.name, item.quantity)
        return page(req, "itens.html", "itens", items=store.list_items(engine), error="")

    @app.get(f"/{APP_ID}/api/v1/itens", operation_id="listar_itens", response_model=list[ItemOut])
    def api_listar(req: Request) -> Any:
        who = require_user(req)
        if isinstance(who, JSONResponse):
            return who
        return store.list_items(engine)

    @app.post(f"/{APP_ID}/api/v1/itens", operation_id="criar_item", status_code=201, response_model=ItemOut)
    def api_criar(req: Request, body: ItemIn) -> Any:
        who = require_user(req)
        if isinstance(who, JSONResponse):
            return who
        return store.add_item(engine, body.name, body.quantity)

    @app.get(f"/{APP_ID}/{{section}}")
    def desconhecida(section: str) -> Response:
        return render.not_found()

    install_api(app, APP_ID, title="__APP_NAME__", version=VERSION)
    return app
