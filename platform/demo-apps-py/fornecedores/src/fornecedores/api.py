"""API v1 of the ``fornecedores`` (wave 11): supplier directory, registry lookup (external API
with circuit breaker) and attachment metadata (no content). JSON; RFC 9457 errors.

Hub identity required (``x-hub-user``); outbox audit; idempotency via the middleware
of the SDK. Native FastAPI OpenAPI at ``/fornecedores/api/openapi.json``; each operation becomes
ferramenta MCP (``hub_sdk.mcp``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from hub_sdk import storage
from hub_sdk.api import DESTRUCTIVE, install_api, problem, require_user
from pydantic import BaseModel

from fornecedores.state import StateDep
from fornecedores.suppliers import SUPPLIERS, Supplier, supplier, valid_object_name
from fornecedores.views import storage_status

BASE = "/fornecedores/api/v1"
TITLE = "Rede de Fornecedores — API"
VERSION = "1.0.0"


class SupplierOut(BaseModel):
    code: str
    name: str
    category: str
    city: str
    status: str
    cnpj: str


class SupplierPage(BaseModel):
    items: list[SupplierOut]
    total: int


class AttachmentOut(BaseModel):
    name: str
    size: int
    modified: str
    content_type: str | None = None


class AttachmentPage(BaseModel):
    items: list[AttachmentOut]
    total: int


class Deleted(BaseModel):
    deleted: str


def sup_json(s: Supplier) -> dict[str, str]:
    return {
        "code": s.code,
        "name": s.name,
        "category": s.category,
        "city": s.city,
        "status": s.status,
        "cnpj": s.cnpj,
    }


router = APIRouter()


def no_store() -> Response:
    return problem(503, "armazenamento de anexos não configurado")


@router.get(
    f"{BASE}/suppliers",
    operation_id="suppliers_list",
    tags=["Fornecedores"],
    summary="Lista fornecedores (busca e status)",
    response_model=SupplierPage,
)
async def suppliers_list(
    req: Request,
    q: str = Query("", description="Nome, código ou cidade"),
    status: str = Query("", description="Status (ex.: Aprovado)"),
) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    term, stat = q.strip().lower(), status.strip().lower()
    items = [
        sup_json(s)
        for s in SUPPLIERS
        if (not term or any(term in f.lower() for f in (s.name, s.code, s.city, s.category)))
        and (not stat or s.status.lower() == stat)
    ]
    return JSONResponse({"total": len(items), "items": items})


@router.get(
    f"{BASE}/suppliers/{{code}}",
    operation_id="suppliers_get",
    tags=["Fornecedores"],
    summary="Um fornecedor",
    response_model=SupplierOut,
)
async def suppliers_get(req: Request, code: str = Path(description="Código (ex.: FOR-0284)")) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    s = supplier(code)
    return JSONResponse(sup_json(s)) if s else problem(404, "fornecedor não encontrado")


@router.post(
    f"{BASE}/suppliers/{{code}}/lookup",
    operation_id="suppliers_lookup",
    tags=["Fornecedores"],
    summary="Consulta cadastral (CNPJ, score e risco) na API externa",
)
async def suppliers_lookup(
    req: Request, st: StateDep, code: str = Path(description="Código do fornecedor")
) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    sup = supplier(code)
    if sup is None:
        return problem(404, "fornecedor não encontrado")
    o = await st.lookup.lookup(sup.cnpj)
    if o.info is not None:
        body: dict[str, Any] = {
            "result": "ok",
            "info": o.info.to_json(),
            "attempts": o.attempts,
            "ms": o.ms,
        }
        detail = f"score {o.info.score} ({o.info.risco}) · API"
    else:
        cached = {"info": o.cached[0].to_json(), "age_s": o.cached[1]} if o.cached else None
        body = {"result": "fallback", "reason": o.reason, "attempts": o.attempts, "cached": cached}
        detail = f"contingência: {o.reason} · API"
    st.audit(req, "Consulta cadastral de fornecedor", sup.code, detail)
    return JSONResponse(body)


@router.get(
    f"{BASE}/suppliers/{{code}}/attachments",
    operation_id="attachments_list",
    tags=["Anexos"],
    summary="Metadados dos anexos (nome, tamanho, data)",
    response_model=AttachmentPage,
)
async def attachments_list(
    req: Request, st: StateDep, code: str = Path(description="Código do fornecedor")
) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    sup = supplier(code)
    if sup is None:
        return problem(404, "fornecedor não encontrado")
    if (store := st.user_store(req)) is None:
        return no_store()
    try:
        objs = await store.list(sup.code)
    except storage.StorageError as e:
        if e.status == 404:
            return JSONResponse({"total": 0, "items": []})
        return problem(storage_status(e)[0], e.message)
    items = [
        {"name": o.name, "size": o.size, "modified": o.modified, "content_type": o.content_type}
        for o in objs
        if not o.folder
    ]
    return JSONResponse({"total": len(items), "items": items})


@router.delete(
    f"{BASE}/suppliers/{{code}}/attachments/{{name}}",
    operation_id="attachments_delete",
    tags=["Anexos"],
    summary="Exclui um anexo",
    response_model=Deleted,
    openapi_extra=DESTRUCTIVE,
)
async def attachments_delete(
    req: Request,
    st: StateDep,
    code: str = Path(description="Código do fornecedor"),
    name: str = Path(description="Nome do objeto (da listagem)"),
) -> Response:
    if isinstance(u := require_user(req), JSONResponse):
        return u
    sup = supplier(code)
    if sup is None or not valid_object_name(name):
        return problem(404, "anexo não encontrado")
    if (store := st.user_store(req)) is None:
        return no_store()
    try:
        await store.delete(f"{sup.code}/{name}")
    except storage.StorageError as e:
        return problem(storage_status(e)[0], e.message)
    st.audit(req, "Anexo de fornecedor excluído (API)", sup.code, name)
    return JSONResponse({"deleted": name})


def install(app: FastAPI) -> None:
    app.include_router(router)
    install_api(app, "fornecedores", title=TITLE, version=VERSION)
