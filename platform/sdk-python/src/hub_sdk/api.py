"""API do app (onda 11): OpenAPI 3.1 **nativo do FastAPI** servido no caminho do
contrato (``GET /{app}/api/openapi.json``), só com as rotas da API (``/{app}/api/...``).

Uso::

    app = FastAPI(..., openapi_url=None)            # sem o /openapi.json padrão
    @app.get("/contratos/api/v1/contracts", operation_id="contracts_list")
    async def contracts_list(...): ...
    @app.delete("/contratos/api/v1/contracts/{cid}", operation_id="contracts_delete",
                openapi_extra=DESTRUCTIVE)
    async def contracts_delete(...): ...
    install_api(app, "contratos", title="Gestão de Contratos — API", version="1.0.0")

Cada operação vira uma ferramenta MCP (``hub_sdk.mcp``); ``openapi_extra=DESTRUCTIVE``
(ou ``DELETE``) marca a ferramenta como destrutiva (o hub pede confirmação) e
``openapi_extra=NO_MCP`` a deixa de fora.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from hub_sdk.identity import Identity, parse_identity

DESTRUCTIVE: dict[str, Any] = {"x-hub-destructive": True}
NO_MCP: dict[str, Any] = {"x-hub-mcp": False}


def problem(status: int, detail: str) -> JSONResponse:
    """Erro RFC 9457 (``application/problem+json``)."""
    from http import HTTPStatus

    try:
        title = HTTPStatus(status).phrase
    except ValueError:
        title = "Erro"
    return JSONResponse(
        {"type": f"about:blank#{status}", "title": title, "status": status, "detail": detail},
        status_code=status,
        media_type="application/problem+json",
    )


def require_user(request: Request) -> Identity | JSONResponse:
    """Identidade do hub obrigatória nas rotas da API (sem ``x-hub-user`` → 401)."""
    ident = parse_identity(request.headers.get("x-hub-user"))
    if ident is None:
        return problem(401, "identidade do hub ausente (x-hub-user)")
    return ident


def openapi_of(app: FastAPI, app_id: str, title: str, version: str) -> dict[str, Any]:
    """Documento OpenAPI só com as rotas ``/{app_id}/api/``."""
    prefix = f"/{app_id}/api/"
    routes = [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith(prefix)]
    doc = get_openapi(title=title, version=version, openapi_version="3.1.0", routes=routes)
    # Onda 12 (2.4): mesmas convenções do SDK Rust — servidor relativo, esquemas de
    # segurança do hub e erros RFC 9457 tipados (``Problem``) em toda operação.
    doc["servers"] = [{"url": "/"}]
    doc["security"] = [{"bearer": []}, {"session": []}]
    comps = doc.setdefault("components", {})
    comps["securitySchemes"] = {
        "bearer": {
            "type": "http",
            "scheme": "bearer",
            "description": "Token de API do hub (escopo do app), pelo gateway",
        },
        "session": {"type": "apiKey", "in": "cookie", "name": "hub_session"},
    }
    comps.setdefault("schemas", {})["Problem"] = PROBLEM_SCHEMA
    problem_ref = {"$ref": "#/components/schemas/Problem"}
    for item in doc.get("paths", {}).values():
        for op in item.values():
            if isinstance(op, dict):
                op.setdefault("x-hub-destructive", False)
                op.setdefault("responses", {}).setdefault(
                    "4XX",
                    {
                        "description": "Erro (RFC 9457)",
                        "content": {"application/problem+json": {"schema": problem_ref}},
                    },
                )
    return doc


PROBLEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["type", "title", "status"],
    "properties": {
        "type": {"type": "string"},
        "title": {"type": "string"},
        "status": {"type": "integer"},
        "detail": {"type": "string"},
    },
}


def install_api(app: FastAPI, app_id: str, *, title: str, version: str) -> None:
    """Rota ``GET /{app_id}/api/openapi.json`` (e guarda a declaração para o MCP)."""
    cache: dict[str, dict[str, Any]] = {}

    def doc() -> dict[str, Any]:
        if "doc" not in cache:
            cache["doc"] = openapi_of(app, app_id, title, version)
        return cache["doc"]

    app.state.hub_api_doc = doc

    async def openapi_json() -> JSONResponse:
        return JSONResponse(doc())

    app.add_api_route(f"/{app_id}/api/openapi.json", openapi_json, methods=["GET"], include_in_schema=False)
