"""API v1 of the Coletor: catalog, locations, orders and readings (register/list).
OpenAPI at ``/coletor/api/openapi.json``; each operation becomes an MCP tool
(``hub_sdk.mcp``). Erros RFC 9457."""

from __future__ import annotations

import datetime as dt
from typing import Any

import asyncpg
from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from hub_sdk.api import install_api, problem, require_user
from hub_sdk.idempotency import idem_ctx
from pydantic import BaseModel, Field

from coletor.model import line_status, normalize_code
from coletor.reads import clean_value, find_item, read_format, recent_reads, store_read
from coletor.state import State, StateDep, db_notice

BASE = "/coletor/api/v1"
TITLE = "Coletor — API"
VERSION = "1.0.0"


class ItemOut(BaseModel):
    code: str
    code128: str
    sku: str
    name: str
    unit: str
    category: str


class ItemPage(BaseModel):
    items: list[ItemOut]
    total: int


class LocationOut(BaseModel):
    code: str
    name: str
    zone: str


class LocationList(BaseModel):
    items: list[LocationOut]


class OrderLine(BaseModel):
    item: str
    name: str
    expected: int
    received: int
    status: str


class OrderOut(BaseModel):
    code: str
    supplier: str
    expected_date: str
    status: str
    lines: list[OrderLine] = Field(default_factory=list)


class OrderList(BaseModel):
    items: list[OrderOut]


class ReadIn(BaseModel):
    value: str = Field(min_length=1, max_length=256, description="Valor lido (código ou texto do QR)")
    format: str = Field("", max_length=16, description="Formato (ean_13, qr_code…); vazio = deduzido")


class ReadOut(BaseModel):
    id: int
    value: str
    format: str
    source: str
    kind: str = Field(description="item, local, pedido, link ou desconhecido")
    label: str
    at: str


class ReadList(BaseModel):
    items: list[ReadOut]


def _unavailable(msg: str) -> JSONResponse:
    r = problem(503, msg)
    r.headers["retry-after"] = "2"
    return r


router = APIRouter()


def db_ok(st: State) -> bool:
    return db_notice(st) is None


@router.get(
    f"{BASE}/items",
    operation_id="items_list",
    tags=["Catálogo"],
    summary="Itens do catálogo (busca por nome, SKU ou código)",
    response_model=ItemPage,
)
async def items_list(
    req: Request, st: StateDep, q: str = Query("", max_length=100, description="Nome, SKU ou código")
) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    if not db_ok(st):
        return _unavailable("banco de dados indisponível ou não configurado")
    try:
        async with st.sdk.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT * FROM col_items WHERE $1 = '' OR name ILIKE '%' || $1 || '%' OR sku ILIKE $1 || '%' "
                "OR code = $2 OR code128 = $2 ORDER BY code LIMIT 100",
                q.strip(),
                normalize_code(q),
            )
    except (OSError, asyncpg.PostgresError) as e:
        return _unavailable(str(e))
    return JSONResponse({"items": [dict(r) for r in rows], "total": len(rows)})


@router.get(
    f"{BASE}/items/{{code}}",
    operation_id="items_get",
    tags=["Catálogo"],
    summary="Um item pelo EAN-13 ou Code128",
    response_model=ItemOut,
)
async def items_get(req: Request, st: StateDep, code: str) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    if not db_ok(st):
        return _unavailable("banco de dados indisponível")
    async with st.sdk.pool.acquire() as c:
        row = await find_item(c, normalize_code(code))
    return JSONResponse(dict(row)) if row else problem(404, "item não encontrado")


@router.get(
    f"{BASE}/locations",
    operation_id="locations_list",
    tags=["Locais"],
    summary="Locais de estoque",
    response_model=LocationList,
)
async def locations_list(req: Request, st: StateDep) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    if not db_ok(st):
        return _unavailable("banco de dados indisponível")
    async with st.sdk.pool.acquire() as c:
        rows = await c.fetch("SELECT code, name, zone FROM col_locations ORDER BY code")
    return JSONResponse({"items": [dict(r) for r in rows]})


async def _order(c: Any, code: str) -> dict[str, Any] | None:
    o = await c.fetchrow("SELECT code, supplier, expected_date, status FROM col_orders WHERE code = $1", code)
    if o is None:
        return None
    lines = await c.fetch(
        "SELECT l.item, i.name, l.expected, l.received FROM col_order_lines l JOIN col_items i ON i.code = l.item "
        "WHERE l.order_code = $1 ORDER BY l.item",
        code,
    )
    d = dict(o)
    d["expected_date"] = (
        d["expected_date"].isoformat() if isinstance(d["expected_date"], dt.date) else str(d["expected_date"])
    )
    d["lines"] = [dict(x) | {"status": line_status(x["expected"], x["received"])} for x in lines]
    return d


@router.get(
    f"{BASE}/orders",
    operation_id="orders_list",
    tags=["Recebimento"],
    summary="Pedidos de recebimento",
    response_model=OrderList,
)
async def orders_list(req: Request, st: StateDep) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    if not db_ok(st):
        return _unavailable("banco de dados indisponível")
    async with st.sdk.pool.acquire() as c:
        codes = [r["code"] for r in await c.fetch("SELECT code FROM col_orders ORDER BY code")]
        items = [await _order(c, x) for x in codes]
    return JSONResponse({"items": [x for x in items if x is not None]})


@router.get(
    f"{BASE}/orders/{{code}}",
    operation_id="orders_get",
    tags=["Recebimento"],
    summary="Um pedido com as linhas (esperado × recebido)",
    response_model=OrderOut,
)
async def orders_get(req: Request, st: StateDep, code: str) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    if not db_ok(st):
        return _unavailable("banco de dados indisponível")
    async with st.sdk.pool.acquire() as c:
        o = await _order(c, normalize_code(code))
    return JSONResponse(o) if o is not None else problem(404, "pedido não encontrado")


def _read_json(d: dict[str, Any]) -> dict[str, Any]:
    at = d.get("at")
    return {
        "id": d["id"],
        "value": d["value"],
        "format": d["format"],
        "source": d["source"],
        "kind": d["kind"],
        "label": d["label"],
        "at": at.isoformat() if isinstance(at, dt.datetime) else str(at),
    }


@router.get(
    f"{BASE}/reads",
    operation_id="reads_list",
    tags=["Leituras"],
    summary="Últimas leituras do usuário (a mais nova primeiro)",
    response_model=ReadList,
)
async def reads_list(req: Request, st: StateDep, limit: int = Query(20, ge=1, le=100)) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    if not db_ok(st):
        return _unavailable("banco de dados indisponível")
    async with st.sdk.pool.acquire() as c:
        rows = await recent_reads(c, u.user, limit)
    return JSONResponse({"items": [_read_json(r) for r in rows]})


@router.post(
    f"{BASE}/reads",
    operation_id="reads_create",
    tags=["Leituras"],
    summary="Registra uma leitura (como o Escanear) e devolve o que ela é",
    response_model=ReadOut,
    status_code=201,
)
async def reads_create(req: Request, st: StateDep, body: ReadIn) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    if not db_ok(st):
        return _unavailable("banco de dados indisponível")
    value = clean_value(body.value)
    if not value:
        return problem(422, "valor vazio")
    fmt, source = read_format({"formato": body.format}, value)
    async with st.sdk.pool.acquire() as c, c.transaction():
        row = await store_read(c, req, value, fmt, "api" if source == "camera" else source)
        if (ctx := idem_ctx(req)) is not None:
            await ctx.mark_committed(c)
    return JSONResponse(_read_json(row), status_code=201)


def install(app: FastAPI) -> None:
    # Added route by route, not with ``include_router``: FastAPI wraps an included router in one
    # opaque route, and ``hub_sdk.api.openapi_of`` only sees the app's direct ``APIRoute``s.
    app.router.routes.extend(router.routes)
    install_api(app, "coletor", title=TITLE, version=VERSION)
