"""App API (wave 11): **native FastAPI** OpenAPI 3.1 served at the contract path
(``GET /{app}/api/openapi.json``), with only the API routes (``/{app}/api/...``).

Uso::

    app = FastAPI(..., openapi_url=None)            # no default /openapi.json
    @app.get("/contratos/api/v1/contracts", operation_id="contracts_list")
    async def contracts_list(...): ...
    @app.delete("/contratos/api/v1/contracts/{cid}", operation_id="contracts_delete",
                openapi_extra=DESTRUCTIVE)
    async def contracts_delete(...): ...
    install_api(app, "contratos", title="Gestão de Contratos — API", version="1.0.0")

Each operation becomes an MCP tool (``hub_sdk.mcp``); ``openapi_extra=DESTRUCTIVE``
(or ``DELETE``) marks the tool as destructive (the hub asks for confirmation) and
``openapi_extra=NO_MCP`` leaves it out.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

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
    """Hub identity required on the API routes (no ``x-hub-user`` → 401)."""
    ident = parse_identity(request.headers.get("x-hub-user"))
    if ident is None:
        return problem(401, "identidade do hub ausente (x-hub-user)")
    return ident


def openapi_of(app: FastAPI, app_id: str, title: str, version: str) -> dict[str, Any]:
    """OpenAPI document with only the ``/{app_id}/api/`` routes."""
    # All routes, then filter: an ``include_router`` router is one opaque route that only
    # ``get_openapi`` expands, so filtering ``app.routes`` by path would miss it.
    doc = get_openapi(title=title, version=version, openapi_version="3.1.0", routes=app.routes)
    prefix = f"/{app_id}/api/"
    doc["paths"] = {p: v for p, v in doc.get("paths", {}).items() if p.startswith(prefix)}
    _drop_unused_schemas(doc)
    # Wave 12 (2.4): relative server, hub
    # security schemes and typed RFC 9457 errors (``Problem``) on every operation.
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


def _refs(node: Any) -> set[str]:
    """Names of the ``#/components/schemas/<name>`` references anywhere under ``node``."""
    found: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            found.add(ref.rsplit("/", 1)[-1])
        node = list(node.values())
    if isinstance(node, list):
        for v in node:
            found |= _refs(v)
    return found


def _drop_unused_schemas(doc: dict[str, Any]) -> None:
    """Keeps only the schemas reachable from the API paths (not those of page routes)."""
    schemas = doc.get("components", {}).get("schemas", {})
    seen: set[str] = set()
    todo = _refs(doc["paths"])
    while todo:
        name = todo.pop()
        if name not in seen and name in schemas:
            seen.add(name)
            todo |= _refs(schemas[name])
    for name in set(schemas) - seen:
        del schemas[name]


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
    """``GET /{app_id}/api/openapi.json`` route (and stores the declaration for MCP)."""
    cache: dict[str, dict[str, Any]] = {}

    def doc() -> dict[str, Any]:
        if "doc" not in cache:
            cache["doc"] = openapi_of(app, app_id, title, version)
        return cache["doc"]

    app.state.hub_api_doc = doc

    async def openapi_json() -> JSONResponse:
        return JSONResponse(doc())

    app.add_api_route(f"/{app_id}/api/openapi.json", openapi_json, methods=["GET"], include_in_schema=False)
