"""API v1 of ``automations`` (CLI with an ``app-api`` token; each operation is also an MCP tool):
packages (upload multipart or base64, list), processes (list, create, update), runs (start —
same validation as the form: 422 with the errors per field, 202 with the run —, get, list).
Same rules as the screens: admins (``AUTOMATIONS_ADMIN_ROLES``) manage packages and processes and
see every run; others run enabled processes and see their own runs (403/404 otherwise)."""

from __future__ import annotations

import base64
import binascii
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, File, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from hub_sdk.api import install_api, problem, require_user
from pydantic import BaseModel, Field

from automations import packages, processes, store, views
from automations.access import is_admin, visible_login
from automations.fields import FieldSpec, validate
from automations.model import STATUSES, Package, Process
from automations.package_format import MAX_ZIP_BYTES, PackageError
from automations.state import DB_ERRORS, State, StateDep, mark_committed, may_see, new_run, to_int, user_of

BASE = "/automations/api/v1"
TITLE = "Automações — API"
VERSION = "1.0.0"
PKG: list[str | Enum] = ["Pacotes"]
PROC: list[str | Enum] = ["Processos"]
RUNS: list[str | Enum] = ["Execuções"]


class PackageOut(BaseModel):
    name: str
    version: str
    description: str
    entrypoint: str
    fields: list[FieldSpec]
    sha256: str
    size: int
    uploaded_by: str
    created_at: datetime


class PackageList(BaseModel):
    items: list[PackageOut]


class PackageBase64In(BaseModel):
    content_base64: str = Field(description="O zip do pacote em base64 (até 5 MB)")


class ProcessOut(BaseModel):
    id: int
    name: str
    package_name: str
    package_version: str
    params: dict[str, Any]
    timeout_s: int
    enabled: bool
    fields: list[FieldSpec] = Field(default_factory=list, description="Campos da versão selecionada")


class ProcessList(BaseModel):
    items: list[ProcessOut]


class RunIn(BaseModel):
    params: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict, description="Valores dos campos do processo (nome → valor)."
    )


class RunOut(BaseModel):
    id: int
    process_id: int | None
    process_name: str
    package_name: str
    package_version: str
    status: str = Field(json_schema_extra={"enum": list(STATUSES)})
    trigger: str
    requested_by: str
    params: dict[str, Any]
    output: dict[str, Any] | None
    error: str | None
    log: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None


class RunList(BaseModel):
    total: int
    items: list[RunOut]


def dump(model: type[BaseModel], obj: Any, **extra: Any) -> dict[str, Any]:
    return model.model_validate(asdict(obj) | extra).model_dump(mode="json")


def package_json(p: Package) -> dict[str, Any]:
    return dump(PackageOut, p)


def process_json(p: Process, pkg: Package | None) -> dict[str, Any]:
    return dump(ProcessOut, p, fields=list(pkg.specs) if pkg else [])


def unavailable(detail: str = "banco de dados indisponível ou não configurado") -> JSONResponse:
    r = problem(503, detail)
    r.headers["retry-after"] = "2"
    return r


def invalid(errors: dict[str, str] | list[str], detail: str = "parâmetros inválidos") -> JSONResponse:
    """422 in RFC 9457 with the problems (``errors``: per field, or a path-prefixed list)."""
    body = {"type": "about:blank#422", "title": "Unprocessable Content", "status": 422, "detail": detail,
            "errors": errors}  # fmt: skip
    return JSONResponse(body, status_code=422, media_type="application/problem+json")


def guard(req: Request, st: State, admin: bool = False) -> JSONResponse | None:
    """401 without identity, 403 for a non-admin on an admin operation, 503 without database."""
    if isinstance(u := require_user(req), JSONResponse):
        return u
    if admin and not is_admin(req):
        return problem(403, "operação restrita aos administradores do app (AUTOMATIONS_ADMIN_ROLES)")
    return None if st.db_ok() else unavailable()


router = APIRouter()


@router.get(f"{BASE}/packages", operation_id="list_packages", tags=PKG, summary="Lista os pacotes e versões",
            responses={200: {"model": PackageList}})  # fmt: skip
async def list_packages(req: Request, st: StateDep) -> Response:
    if (g := guard(req, st, admin=True)) is not None:
        return g
    return JSONResponse({"items": [package_json(p) for p in await packages.list_packages(st.pool)]})


async def store_package(req: Request, st: State, data: bytes) -> Response:
    try:
        async with st.pool.acquire() as c, c.transaction():
            pkg = await packages.register(c, data, user_of(req))
            await mark_committed(c, req)
    except PackageError as e:
        return invalid(e.errors, "pacote inválido")
    except packages.DuplicateVersion as e:
        return problem(409, str(e))
    return JSONResponse(package_json(pkg), status_code=201)


@router.post(f"{BASE}/packages", operation_id="upload_package", tags=PKG, status_code=201,
             summary="Envia um pacote (multipart, campo arquivo)", responses={201: {"model": PackageOut}})  # fmt: skip
async def upload_package(req: Request, st: StateDep, arquivo: Annotated[UploadFile, File()]) -> Response:
    if (g := guard(req, st, admin=True)) is not None:
        return g
    return await store_package(req, st, await arquivo.read(MAX_ZIP_BYTES + 1))


@router.post(f"{BASE}/packages/base64", operation_id="upload_package_base64", tags=PKG, status_code=201,
             summary="Envia um pacote em base64 (para clientes MCP)", responses={201: {"model": PackageOut}})  # fmt: skip
async def upload_package_base64(body: PackageBase64In, req: Request, st: StateDep) -> Response:
    if (g := guard(req, st, admin=True)) is not None:
        return g
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError):
        return invalid(["content_base64: base64 inválido"], "pacote inválido")
    return await store_package(req, st, data)


@router.get(f"{BASE}/processes", operation_id="list_processes", tags=PROC, responses={200: {"model": ProcessList}},
            summary="Processos com os campos da versão selecionada (não administradores: só os ativos)")  # fmt: skip
async def list_processes(req: Request, st: StateDep) -> Response:
    if (g := guard(req, st)) is not None:
        return g
    pkgs = {(k.name, k.version): k for k in await packages.list_packages(st.pool)}
    items = await processes.list_processes(st.pool, only_enabled=not is_admin(req))
    return JSONResponse(
        {"items": [process_json(p, pkgs.get((p.package_name, p.package_version))) for p in items]}
    )


async def save_process(req: Request, st: State, old: Process | None, body: processes.ProcessIn) -> Response:
    pkg = await packages.get_package(st.pool, body.package_name, body.package_version)
    if pkg is None:
        return invalid({"package_version": "esta versão do pacote não existe"})
    values, errors = processes.check_defaults(pkg.specs, body.params)
    if errors:
        return invalid({f"params.{k}": v for k, v in errors.items()})
    try:
        async with st.pool.acquire() as c, c.transaction():
            pid = await processes.save_process(
                c, old, body.model_copy(update={"params": values}), user_of(req)
            )
            await mark_committed(c, req)
    except processes.ProcessConflict as e:
        return problem(409, str(e))
    p = await processes.get_process(st.pool, pid)
    return JSONResponse(process_json(p, pkg) if p else {}, status_code=200 if old else 201)


@router.post(f"{BASE}/processes", operation_id="create_process", tags=PROC, status_code=201,
             summary="Cria um processo (pacote + versão)", responses={201: {"model": ProcessOut}})  # fmt: skip
async def create_process(body: processes.ProcessIn, req: Request, st: StateDep) -> Response:
    if (g := guard(req, st, admin=True)) is not None:
        return g
    return await save_process(req, st, None, body)


@router.put(f"{BASE}/processes/{{id}}", operation_id="update_process", tags=PROC, responses={200: {"model": ProcessOut}},
            summary="Altera um processo (inclusive a versão do pacote — auditado)")  # fmt: skip
async def update_process(id: str, body: processes.ProcessIn, req: Request, st: StateDep) -> Response:
    if (g := guard(req, st, admin=True)) is not None:
        return g
    n = to_int(id)
    old = await processes.get_process(st.pool, n) if n is not None else None
    if old is None:
        return problem(404, f"processo {id!r} não encontrado")
    return await save_process(req, st, old, body)


@router.post(f"{BASE}/processes/{{id}}/runs", operation_id="start_run", tags=RUNS, status_code=202,
             summary="Executa um processo (validado como no formulário)", responses={202: {"model": RunOut}})  # fmt: skip
async def start_run(id: str, body: RunIn, req: Request, st: StateDep) -> Response:
    if (g := guard(req, st)) is not None:
        return g
    a = await views.runnable(st.pool, to_int(id), is_admin(req))
    if a is None or a.process is None:
        return problem(404, f"processo {id!r} não encontrado ou inativo")
    values, errors = validate(a.fields, body.params)
    if errors:
        return invalid(errors)
    try:
        run = await new_run(st, req, a.process, dict(values), "api")
    except DB_ERRORS as e:
        return unavailable(str(e))
    return JSONResponse(dump(RunOut, run), status_code=202)


@router.get(f"{BASE}/runs/{{id}}", operation_id="get_run", tags=RUNS, summary="Uma execução",
            responses={200: {"model": RunOut}})  # fmt: skip
async def get_run(id: str, req: Request, st: StateDep) -> Response:
    if (g := guard(req, st)) is not None:
        return g
    n = to_int(id)
    run = await store.get_run(st.pool, n) if n is not None else None
    if run is None or not may_see(req, run):
        return problem(404, f"execução {id!r} não encontrada")
    return JSONResponse(dump(RunOut, run))


@router.get(f"{BASE}/runs", operation_id="list_runs", tags=RUNS, responses={200: {"model": RunList}},
            summary="Execuções, mais recentes primeiro (não administradores: só as suas)")  # fmt: skip
async def list_runs(
    req: Request,
    st: StateDep,
    processo: int = Query(0, ge=0, description="Id do processo (0 = todos)"),
    status: str = Query("", description="Status da execução"),
    limit: int = Query(25, ge=1, le=100, description="Até 100"),
) -> Response:
    if (g := guard(req, st)) is not None:
        return g
    f = store.RunFilter(process_id=processo, status=status if status in STATUSES else "", days=3650,
                        login=visible_login(req))  # fmt: skip
    total, items = await store.list_runs(st.pool, f, limit)
    return JSONResponse({"total": total, "items": [dump(RunOut, r) for r in items]})


async def _invalid(req: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestValidationError)
    if not req.url.path.startswith(BASE):
        from fastapi.exception_handlers import request_validation_exception_handler

        return await request_validation_exception_handler(req, exc)
    detail = "; ".join(f"{'.'.join(str(x) for x in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
    return problem(422, detail)


def install(app: FastAPI) -> None:
    app.include_router(router)
    app.add_exception_handler(RequestValidationError, _invalid)
    install_api(app, "automations", title=TITLE, version=VERSION)
