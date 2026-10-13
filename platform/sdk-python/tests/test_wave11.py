"""Onda 11: OpenAPI no caminho do contrato e MCP interno gerado dele."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from hub_sdk import mcp
from hub_sdk.api import DESTRUCTIVE, NO_MCP, install_api, problem, require_user
from hub_sdk.identity import Identity

TOKEN = "0123456789abcdef0123"


def make_app() -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.get("/demo/api/v1/items", operation_id="items_list", summary="Lista")
    async def items(req: Request, q: str = "") -> Response:
        if isinstance(u := require_user(req), JSONResponse):
            return u
        return JSONResponse({"q": q, "user": u.user, "sig": req.headers.get("x-hub-user-sig")})

    @app.delete("/demo/api/v1/items/{iid}", operation_id="items_delete", summary="Exclui")
    async def delete(iid: int) -> Response:
        return JSONResponse({"deleted": iid})

    @app.post("/demo/api/v1/items/{iid}/archive", operation_id="items_archive", openapi_extra=DESTRUCTIVE)
    async def archive(iid: int) -> Response:
        return problem(409, "já arquivado")

    @app.get("/demo/api/v1/items/{iid}/file", operation_id="items_file", openapi_extra=NO_MCP)
    async def file(iid: int) -> Response:
        return Response(b"x")

    @app.get("/demo/fragment")
    async def frag() -> Response:
        return Response("<section></section>")

    install_api(app, "demo", title="Demo", version="1.0.0")
    app.include_router(mcp.router("demo", TOKEN, lambda: app, app.state.hub_api_doc, "1.0.0"))
    return app


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def rpc(c: httpx.AsyncClient, method: str, params: Any = None, token: str = TOKEN) -> httpx.Response:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return await c.post(
        "/demo/_hub/mcp",
        json=body,
        headers={
            "x-hub-internal-token": token,
            "x-hub-user": Identity(user="ana@x").encode(),
            "x-hub-user-sig": "v1.1.abc",
        },
    )


async def test_openapi_is_served_at_the_contract_path_only_with_api_routes() -> None:
    async with client(make_app()) as c:
        r = await c.get("/demo/api/openapi.json")
        assert r.status_code == 200
        d = r.json()
        assert d["openapi"] == "3.1.0"
        assert set(d["paths"]) == {
            "/demo/api/v1/items",
            "/demo/api/v1/items/{iid}",
            "/demo/api/v1/items/{iid}/archive",
            "/demo/api/v1/items/{iid}/file",
        }
        assert d["paths"]["/demo/api/v1/items/{iid}/archive"]["post"]["x-hub-destructive"] is True
        assert d["paths"]["/demo/api/v1/items"]["get"]["x-hub-destructive"] is False
        # Sem identidade: 401 problem+json.
        r = await c.get("/demo/api/v1/items")
        assert r.status_code == 401
        assert r.headers["content-type"].startswith("application/problem+json")
        assert r.json()["status"] == 401


async def test_mcp_tools_from_openapi_and_in_process_calls() -> None:
    async with client(make_app()) as c:
        assert (await rpc(c, "tools/list", token="x" * 20)).status_code == 401
        r = await rpc(c, "initialize", {"protocolVersion": "2025-06-18"})
        assert r.json()["result"]["protocolVersion"] == "2025-06-18"
        tools = {t["name"]: t for t in (await rpc(c, "tools/list")).json()["result"]["tools"]}
        assert set(tools) == {"items_list", "items_delete", "items_archive"}
        assert tools["items_delete"]["annotations"]["destructiveHint"] is True
        assert tools["items_archive"]["annotations"]["destructiveHint"] is True
        assert tools["items_list"]["annotations"]["readOnlyHint"] is True
        r = (await rpc(c, "tools/call", {"name": "items_list", "arguments": {"q": "a b"}})).json()["result"]
        assert r["isError"] is False
        assert r["structuredContent"] == {"q": "a b", "user": "ana@x", "sig": "v1.1.abc"}
        r = (await rpc(c, "tools/call", {"name": "items_delete", "arguments": {"iid": 7}})).json()["result"]
        assert r["structuredContent"] == {"deleted": 7}
        r = (await rpc(c, "tools/call", {"name": "items_archive", "arguments": {"iid": 7}})).json()["result"]
        assert r["isError"] is True
        e = (await rpc(c, "tools/call", {"name": "nada"})).json()
        assert e["error"]["code"] == -32602
        e = (await rpc(c, "x")).json()
        assert e["error"]["code"] == -32601
        # N7: a operação esperada pelo hub (do pacote) diverge da do app no ar → recusa.
        body = {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "items_list", "arguments": {}},
        }
        h = {"x-hub-internal-token": TOKEN, "x-hub-user": Identity(user="ana@x").encode()}
        e = (
            await c.post(
                "/demo/_hub/mcp", json=body, headers={**h, "x-hub-mcp-op": "DELETE /demo/api/v1/items/{iid}"}
            )
        ).json()
        assert e["error"]["code"] == -32602
        ok = (
            await c.post("/demo/_hub/mcp", json=body, headers={**h, "x-hub-mcp-op": "GET /demo/api/v1/items"})
        ).json()
        assert ok["result"]["isError"] is False


def test_mutations_are_destructive_by_default() -> None:
    """I3: leitura só GET/HEAD ou ``x-hub-readonly``; toda mutação pede aprovação e
    ``x-hub-destructive: false`` não dispensa (só ``mcp.confirm_exempt`` no manifesto)."""
    from hub_sdk.mcp import tools_from_openapi

    r = {"200": {"description": "ok"}}
    doc = {
        "openapi": "3.1.0",
        "info": {"title": "d", "version": "1"},
        "paths": {
            "/d/api/v1/a": {
                "get": {"operationId": "a_list", "responses": r},
                "post": {"operationId": "a_create", "responses": r},
                "put": {"operationId": "a_put", "responses": r, "x-hub-destructive": False},
                "patch": {"operationId": "a_search", "responses": r, "x-hub-readonly": True},
                "delete": {"operationId": "a_delete", "responses": r},
            }
        },
    }
    t = {x.name: x for x in tools_from_openapi(doc, "d")}
    assert t["a_list"].read_only and not t["a_list"].destructive
    assert t["a_create"].destructive and not t["a_create"].read_only
    assert t["a_put"].destructive
    assert t["a_delete"].destructive
    assert t["a_search"].read_only and not t["a_search"].destructive


async def test_mcp_can_be_disabled(monkeypatch: Any) -> None:
    monkeypatch.setenv("HUB_MCP_ENABLED", "0")
    async with client(make_app()) as c:
        assert (await rpc(c, "tools/list")).status_code == 404


def test_every_platform_openapi_document_is_valid() -> None:
    """Hub (``docs/openapi/hub-v1.json``) e os apps (``demo-apps-py/<app>/openapi.json``),
    validados com um validador de especificação OpenAPI."""
    import json
    from pathlib import Path

    from openapi_spec_validator import validate

    platform = Path(__file__).resolve().parents[2]
    docs = [
        platform / "docs" / "openapi" / "hub-v1.json",
        *sorted(platform.glob("demo-apps-py/*/openapi.json")),
    ]
    assert len(docs) > 1
    for p in docs:
        validate(json.loads(p.read_text("utf-8")))


def test_openapi_has_hub_conventions_and_typed_problem() -> None:
    """Onda 12 (2.4): pydantic tipa corpo/resposta; o SDK acrescenta servidor, esquemas
    de segurança e ``Problem`` (RFC 9457) em toda operação."""
    from hub_sdk.api import openapi_of

    app = make_app()
    doc = openapi_of(app, "demo", "Demo", "1.0.0")
    assert doc["servers"] == [{"url": "/"}]
    assert set(doc["components"]["securitySchemes"]) == {"bearer", "session"}
    assert doc["components"]["schemas"]["Problem"]["required"] == ["type", "title", "status"]
    for item in doc["paths"].values():
        for op in item.values():
            ref = op["responses"]["4XX"]["content"]["application/problem+json"]["schema"]
            assert ref == {"$ref": "#/components/schemas/Problem"}
