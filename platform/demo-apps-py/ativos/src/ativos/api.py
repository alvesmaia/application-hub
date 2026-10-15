"""API v1 of ``ativos`` (wave 11): asset registry (CRUD with optimistic locking), search,
XLSX export as a durable job, and the "documented registration" saga. JSON; RFC 9457 errors.

Same rules as the screen: hub identity required (``x-hub-user``), per-column validation
(:func:`ativos.model.validate`), audit in the outbox in the same transaction, and idempotency
(``Idempotency-Key``) via the SDK middleware. Native FastAPI OpenAPI at
``/ativos/api/openapi.json``; each operation becomes an MCP tool (``hub_sdk.mcp``).
"""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from hub_sdk.api import DESTRUCTIVE, install_api, problem, require_user
from hub_sdk.idempotency import idem_ctx
from hub_sdk.jobs import enqueue_with
from pydantic import BaseModel, Field

from ativos import exporter, saga
from ativos.model import COLS, EDIT_FIELDS, PAGE_SIZE, validate
from ativos.query import Filter
from ativos.state import (
    State,
    StateDep,
    audit_tx,
    db_notice,
    get_job,
    jobs_ready,
    list_assets,
    load_asset,
    transient,
    user_of,
)
from ativos.views import asset_values

BASE = "/ativos/api/v1"
TITLE = "Gestão de Ativos — API"
VERSION = "1.0.0"


class AssetFields(BaseModel):
    """Editable fields of an asset (the per-column validation is the screen's)."""

    tag: str | None = None
    name: str | None = None
    category: str | None = None
    location: str | None = None
    status: str | None = None
    responsible: str | None = None
    department: str | None = None
    acquisition_date: str | None = Field(None, description="`AAAA-MM-DD`.")
    acquisition_value_cents: str | None = Field(None, description="Valor em centavos (texto, como na tela).")
    notes: str | None = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "tag": "PAT-0001",
                    "name": "Notebook Latitude 5440",
                    "category": "Tecnologia",
                    "status": "Em uso",
                }
            ]
        }
    }


class AssetUpdate(BaseModel):
    version: int = Field(description="Versão lida (controle otimista; 409 se mudou).")
    fields: AssetFields = Field(default_factory=AssetFields)


class AssetRow(BaseModel):
    """Row of the asset list."""

    id: int
    tag: str
    name: str
    category: str
    location: str
    status: str
    acquisition_value_cents: int | None = None
    acquisition_date: str | None = None


class AssetPage(BaseModel):
    items: list[AssetRow]
    total: int
    limit: int
    offset: int


class ExportIn(BaseModel):
    q: str = ""
    categoria: str = ""
    status: str = ""
    localizacao: str = ""


class Registration(BaseModel):
    """Documented registration request (example saga, wave 12 — 2.9)."""

    tag: str
    name: str
    category: str | None = None
    location: str | None = None
    status: str | None = None
    upload: str | None = Field(
        None, description="Envio em duas fases já feito (`hub_sdk.uploads`), confirmado pela saga."
    )
    file_name: str | None = None
    falhar_em: str | None = Field(
        None, description="Demonstração: passo que deve falhar (`criar`, `anexar`, `notificar`)."
    )


class Enqueued(BaseModel):
    """Tarefa enfileirada."""

    job: str
    status: str


def _unavailable(msg: str) -> JSONResponse:
    r = problem(503, msg)
    r.headers["retry-after"] = "2"
    return r


def asset_json(row: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"id": row["id"], "version": row["version"]}
    for c in COLS:
        out[c.key] = row[c.key]
    return out


router = APIRouter()


def db_ok(st: State) -> bool:
    return db_notice(st) is None


async def _invalid(req: Request, exc: RequestValidationError) -> Response:
    if not req.url.path.startswith(BASE):
        from fastapi.exception_handlers import request_validation_exception_handler

        return await request_validation_exception_handler(req, exc)
    detail = "; ".join(f"{'.'.join(str(x) for x in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
    return problem(422, detail)


@router.get(
    f"{BASE}/assets",
    operation_id="assets_list",
    tags=["Ativos"],
    summary="Lista ativos (busca, filtros, página de 50)",
    response_model=AssetPage,
)
async def assets_list(
    req: Request,
    st: StateDep,
    q: str = Query("", description="Nome ou patrimônio"),
    categoria: str = Query("", description="Categoria"),
    status: str = Query("", description="Status"),
    localizacao: str = Query("", description="Localização"),
    page: str = Query("", description="Página (1…)"),
) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    if not db_ok(st):
        return _unavailable("banco de dados indisponível ou não configurado")
    f = Filter.from_query(
        {"q": q, "categoria": categoria, "status": status, "localizacao": localizacao, "page": page}
    )
    try:
        total, rows = await list_assets(st, f)
    except (OSError, asyncpg.PostgresError) as e:
        return _unavailable(str(e))
    return JSONResponse(
        {
            "items": [
                {
                    "id": r["id"],
                    "tag": r["tag"],
                    "name": r["name"],
                    "category": r["category"],
                    "location": r["location"],
                    "status": r["status"],
                    "acquisition_value_cents": r["acquisition_value_cents"],
                    "acquisition_date": r["acquisition_date"].isoformat() if r["acquisition_date"] else None,
                }
                for r in rows
            ],
            "total": total,
            "limit": PAGE_SIZE,
            "offset": (f.page - 1) * PAGE_SIZE,
        }
    )


@router.get(
    f"{BASE}/assets/{{id}}",
    operation_id="assets_get",
    tags=["Ativos"],
    summary="Um ativo (todos os campos e a versão)",
)
async def assets_get(req: Request, st: StateDep, id: int) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    if not db_ok(st):
        return _unavailable("banco de dados indisponível ou não configurado")
    try:
        row = await load_asset(st, id)
    except (OSError, asyncpg.PostgresError) as e:
        return _unavailable(str(e))
    return JSONResponse(asset_json(row)) if row else problem(404, "ativo não encontrado")


@router.post(
    f"{BASE}/assets",
    operation_id="assets_create",
    tags=["Ativos"],
    summary="Cadastra um ativo",
    status_code=201,
)
async def assets_create(req: Request, st: StateDep, body: AssetFields) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    if not db_ok(st):
        return _unavailable("banco de dados indisponível ou não configurado")
    vals, errs = validate(body.model_dump(exclude_none=True))
    if errs:
        return problem(422, errs)
    try:
        async with st.sdk.pool.acquire() as c, c.transaction():
            id = await c.fetchval(
                "INSERT INTO assets (tag, name, category, location, status, responsible, department, "
                "acquisition_date, acquisition_value_cents, notes) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) "
                "RETURNING id",
                *[vals[k] for k in EDIT_FIELDS],
            )
            await audit_tx(c, req, "Ativo cadastrado (API)", vals["tag"], vals["name"] or "")
    except asyncpg.UniqueViolationError:
        return problem(409, "patrimônio já cadastrado")
    except (OSError, asyncpg.PostgresError) as e:
        if transient(e):
            return _unavailable("banco indisponível; nada foi confirmado")
        return problem(500, str(e))
    row = await load_asset(st, id)
    return JSONResponse(asset_json(row) if row else {"id": id}, status_code=201)


@router.put(
    f"{BASE}/assets/{{id}}",
    operation_id="assets_update",
    tags=["Ativos"],
    summary="Altera um ativo (envie a versão lida; 409 se mudou)",
)
async def assets_update(req: Request, st: StateDep, id: int, body: AssetUpdate) -> Response:  # noqa: C901
    if isinstance(u := require_user(req), JSONResponse):
        return u
    if not db_ok(st):
        return _unavailable("banco de dados indisponível ou não configurado")
    try:
        current = await load_asset(st, id)
    except (OSError, asyncpg.PostgresError) as e:
        return _unavailable(str(e))
    if current is None:
        return problem(404, "ativo não encontrado")
    # Missing fields keep their current value.
    merged = {k: v for k, v in asset_values(current).items() if v != ""}
    merged.update(body.fields.model_dump(exclude_none=True))
    keys = tuple(k for k in EDIT_FIELDS if k != "tag")
    vals, errs = validate(merged, keys)
    if errs:
        return problem(422, errs)
    try:
        async with st.sdk.pool.acquire() as c, c.transaction():
            version = await c.fetchval(
                "UPDATE assets SET name=$3, category=$4, location=$5, status=$6, responsible=$7, department=$8, "
                "acquisition_date=$9, acquisition_value_cents=$10, notes=$11, version = version + 1, "
                "updated_at = now() WHERE id = $1 AND version = $2 RETURNING version",
                id,
                body.version,
                *[vals[k] for k in keys],
            )
            if version is not None:
                await audit_tx(
                    c, req, "Ativo atualizado (API)", current["tag"], f"versão {body.version} → {version}"
                )
    except (OSError, asyncpg.PostgresError) as e:
        if transient(e):
            return _unavailable("banco indisponível; nada foi confirmado")
        return problem(500, str(e))
    if version is None:
        return problem(
            409, "o ativo foi alterado por outra pessoa: leia de novo e reenvie com a versão atual"
        )
    row = await load_asset(st, id)
    return JSONResponse(asset_json(row)) if row else problem(404, "ativo não encontrado")


@router.delete(
    f"{BASE}/assets/{{id}}",
    operation_id="assets_delete",
    tags=["Ativos"],
    summary="Exclui um ativo",
    openapi_extra=DESTRUCTIVE,
)
async def assets_delete(
    req: Request,
    st: StateDep,
    id: int,
    version: int = Query(..., description="Versão lida (controle otimista)"),
) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    if not db_ok(st):
        return _unavailable("banco de dados indisponível ou não configurado")
    try:
        async with st.sdk.pool.acquire() as c, c.transaction():
            row = await c.fetchrow(
                "DELETE FROM assets WHERE id = $1 AND version = $2 RETURNING tag, name", id, version
            )
            if row is not None:
                await audit_tx(c, req, "Ativo excluído (API)", row["tag"], row["name"])
    except (OSError, asyncpg.PostgresError) as e:
        if transient(e):
            return _unavailable("banco indisponível; nada foi excluído")
        return problem(500, str(e))
    if row is None:
        exists = await load_asset(st, id)
        return (
            problem(409, "versão desatualizada: leia de novo")
            if exists
            else problem(404, "ativo não encontrado")
        )
    return JSONResponse({"deleted": id, "tag": row["tag"], "name": row["name"]})


@router.get(
    f"{BASE}/search",
    operation_id="assets_search",
    tags=["Ativos"],
    summary="Busca rápida por nome ou patrimônio (até 20)",
)
async def assets_search(req: Request, st: StateDep, q: str = Query(..., description="Termo")) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    term = q.strip()
    if not term:
        return problem(400, "informe ?q=")
    if not db_ok(st):
        return _unavailable("banco de dados indisponível ou não configurado")
    try:
        total, rows = await list_assets(st, Filter.from_query({"q": term}))
    except (OSError, asyncpg.PostgresError) as e:
        return _unavailable(str(e))
    return JSONResponse(
        {
            "items": [
                {"id": r["id"], "tag": r["tag"], "name": r["name"], "status": r["status"]} for r in rows[:20]
            ],
            "total": total,
        }
    )


@router.post(
    f"{BASE}/exports",
    operation_id="assets_export",
    tags=["Exportação"],
    status_code=202,
    summary="Gera a planilha XLSX dos ativos filtrados (tarefa em segundo plano)",
)
async def assets_export(req: Request, st: StateDep, body: ExportIn) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    if not db_ok(st):
        return _unavailable("banco de dados indisponível")
    f = Filter.from_query(body.model_dump())
    ctx = idem_ctx(req)
    try:
        async with st.sdk.pool.acquire() as c, c.transaction():
            job, _ = await enqueue_with(c, exporter.SPEC, f.payload(), user_of(req), ctx.key if ctx else None)
            if ctx is not None:
                await ctx.mark_committed(c)
    except (OSError, asyncpg.PostgresError) as e:
        return _unavailable(str(e))
    st.sdk.wake()
    return JSONResponse(
        {"job": job.id, "status": job.status, "status_url": f"/apps/ativos/api/v1/exports/{job.id}"},
        status_code=202,
    )


@router.get(
    f"{BASE}/exports/{{job}}",
    operation_id="assets_export_status",
    tags=["Exportação"],
    summary="Situação de uma exportação",
)
async def assets_export_status(req: Request, st: StateDep, job: str) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    if not jobs_ready(st):
        return _unavailable("banco indisponível")
    j = await get_job(st, job, exporter.KIND)
    if j is None:
        return problem(404, "exportação não encontrada")
    return JSONResponse(
        {
            "job": j.id,
            "status": j.status,
            "progress_done": j.progress_done,
            "progress_total": j.progress_total,
            "error": j.error,
            "download": f"/apps/ativos/itens/export/{j.id}/ativos.xlsx" if j.result is not None else None,
        }
    )


@router.post(
    f"{BASE}/assets/registrations",
    operation_id="assets_register_documented",
    tags=["Ativos"],
    status_code=202,
    response_model=Enqueued,
    summary="Cadastro documentado (saga): cria o ativo, confirma o documento e notifica; falha compensa",
)
async def assets_register_documented(req: Request, st: StateDep, body: Registration) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    if not jobs_ready(st):
        return _unavailable("banco indisponível")
    text = {k: (v or "").strip() for k, v in body.model_dump().items()}
    if not text["tag"] or not text["name"]:
        return problem(422, "tag e name são obrigatórios")
    payload: dict[str, Any] = {
        "tag": text["tag"],
        "name": text["name"],
        "category": text["category"] or "Outros",
        "location": text["location"] or "Sede",
        "status": text["status"] or "Em estoque",
    }
    for k in ("upload", "file_name", "falhar_em"):
        if text[k]:
            payload[k] = text[k]
    ctx = idem_ctx(req)
    try:
        async with st.sdk.pool.acquire() as c, c.transaction():
            job, _ = await enqueue_with(c, saga.SPEC, payload, user_of(req), ctx.key if ctx else None)
            if ctx is not None:
                await ctx.mark_committed(c)
    except (OSError, asyncpg.PostgresError) as e:
        return _unavailable(str(e))
    st.sdk.wake()
    return JSONResponse({"job": job.id, "status": job.status}, status_code=202)


def install(app: FastAPI) -> None:
    app.exception_handler(RequestValidationError)(_invalid)
    # Added route by route, not with ``include_router``: FastAPI wraps an included router in one
    # opaque route, and ``hub_sdk.api.openapi_of`` only sees the app's direct ``APIRoute``s.
    app.router.routes.extend(router.routes)
    install_api(app, "ativos", title=TITLE, version=VERSION)
