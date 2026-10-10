"""API v1, OpenAPI (paridade com o do app Rust), MCP interno e outbox durável."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from .conftest import PDF, TOKEN, Running
from .test_units import OK_BODY, Ext

PLATFORM = Path(__file__).resolve().parents[3]
BASE = "/fornecedores/api/v1"
OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"
HUB = {"x-hub-internal-token": TOKEN}


def ops(doc: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(m.upper(), p): o for p, item in doc["paths"].items() for m, o in item.items()}


async def test_openapi_has_the_same_operations_as_the_rust_app(running: Running) -> None:
    py = (await running.client.get("/fornecedores/api/openapi.json")).json()
    rust = json.loads((PLATFORM / "samples/src/fornecedores/openapi.json").read_text("utf-8"))
    a, b = ops(rust), ops(py)
    assert set(a) == set(b)
    for k, ro in a.items():
        po = b[k]
        assert po["operationId"] == ro["operationId"], k
        assert po.get("tags") == ro.get("tags"), k
        assert po["x-hub-destructive"] == ro["x-hub-destructive"], k
        pp = {(x["name"], x["in"], x.get("required", False)) for x in po.get("parameters", [])}
        rp = {(x["name"], x["in"], x.get("required", False)) for x in ro.get("parameters", [])}
        assert pp == rp, k
        assert ("requestBody" in po) == ("requestBody" in ro), k
        assert "4XX" in po["responses"]
    assert py["openapi"] == rust["openapi"] and py["servers"] == rust["servers"]
    assert py["security"] == rust["security"] and "Problem" in py["components"]["schemas"]


def test_packaged_openapi_matches_the_code(env: Path) -> None:
    from fornecedores.app import create_app

    assert json.loads(OPENAPI.read_text("utf-8")) == create_app().state.hub_api_doc()


async def test_mcp_tools_come_from_the_openapi(running: Running) -> None:
    r = await running.client.post(
        "/fornecedores/_hub/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=HUB
    )
    assert r.status_code == 200, r.text
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert set(tools) == {
        "suppliers_list",
        "suppliers_get",
        "suppliers_lookup",
        "attachments_list",
        "attachments_delete",
    }
    assert tools["attachments_delete"]["annotations"]["destructiveHint"] is True
    call = await running.client.post(
        "/fornecedores/_hub/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "suppliers_get", "arguments": {"code": "FOR-0284"}},
        },
        headers={**HUB, "x-hub-user": running.client.headers["x-hub-user"]},
    )
    assert "Norte Tecnologia" in call.text


async def test_api_requires_identity(running: Running) -> None:
    anon = running.anon()
    for method, path in (
        ("GET", "/suppliers"),
        ("GET", "/suppliers/FOR-0284"),
        ("POST", "/suppliers/FOR-0284/lookup"),
        ("GET", "/suppliers/FOR-0284/attachments"),
        ("DELETE", "/suppliers/FOR-0284/attachments/a.pdf"),
    ):
        r = await anon.request(method, BASE + path)
        assert r.status_code == 401 and r.headers["content-type"].startswith("application/problem+json"), path


async def test_suppliers_list_and_get(running: Running) -> None:
    c = running.client
    allr = (await c.get(f"{BASE}/suppliers")).json()
    assert allr["total"] == 6 and allr["items"][0]["code"] == "FOR-0284"
    assert (await c.get(f"{BASE}/suppliers", params={"q": "são paulo"})).json()["total"] == 2
    assert (await c.get(f"{BASE}/suppliers", params={"status": "aprovado"})).json()["total"] == 3
    assert (await c.get(f"{BASE}/suppliers", params={"q": "casa", "status": "Aprovado"})).json()["total"] == 1
    one = await c.get(f"{BASE}/suppliers/FOR-0271")
    assert one.json()["name"] == "Casa & Forma"
    nf = await c.get(f"{BASE}/suppliers/XX")
    assert nf.status_code == 404 and nf.json()["detail"] == "fornecedor não encontrado"


async def test_lookup_attachments_and_delete(running: Running) -> None:
    c, store = running.client, running.store
    assert store is not None
    st = running.app.state.fornecedores
    assert (await c.post(f"{BASE}/suppliers/XX/lookup")).status_code == 404
    fb = (await c.post(f"{BASE}/suppliers/FOR-0284/lookup")).json()
    assert fb["result"] == "fallback" and fb["attempts"] == 0 and fb["cached"] is None
    ext = Ext(lambda n: (200, OK_BODY, 0))
    try:
        st.lookup.base = ext.url
        ok = (await c.post(f"{BASE}/suppliers/FOR-0284/lookup")).json()
        assert ok["result"] == "ok" and ok["info"]["score"] == 810 and ok["attempts"] == 1
    finally:
        ext.srv.shutdown()
        ext.srv.server_close()
    st.lookup.base = None
    cached = (await c.post(f"{BASE}/suppliers/FOR-0284/lookup")).json()
    assert cached["cached"]["info"]["score"] == 810

    assert (await c.get(f"{BASE}/suppliers/XX/attachments")).status_code == 404
    assert (await c.get(f"{BASE}/suppliers/FOR-0271/attachments")).json() == {"total": 0, "items": []}
    await store.upload("FOR-0271/20261001000000-a.pdf", PDF, "application/pdf")
    lst = (await c.get(f"{BASE}/suppliers/FOR-0271/attachments")).json()
    assert (
        lst["total"] == 1
        and lst["items"][0]["name"] == "20261001000000-a.pdf"
        and lst["items"][0]["size"] == len(PDF)
    )
    store.fail = (404, "sem pasta")
    assert (await c.get(f"{BASE}/suppliers/FOR-0271/attachments")).json()["total"] == 0
    store.fail = (502, "fora")
    assert (await c.get(f"{BASE}/suppliers/FOR-0271/attachments")).status_code == 502
    assert (await c.delete(f"{BASE}/suppliers/FOR-0271/attachments/a.pdf")).status_code == 502
    store.fail = None
    assert (await c.delete(f"{BASE}/suppliers/FOR-0271/attachments/..x")).status_code == 404
    assert (await c.delete(f"{BASE}/suppliers/XX/attachments/a.pdf")).status_code == 404
    gone = await c.delete(f"{BASE}/suppliers/FOR-0271/attachments/20261001000000-a.pdf")
    assert gone.json() == {"deleted": "20261001000000-a.pdf"} and not store.objects
    await asyncio.sleep(0.05)
    out = await c.get("/fornecedores/_hub/outbox?limit=500", headers=HUB)
    acts = [e["action"] for e in out.json()["events"] if e["kind"] == "audit"]
    assert "Anexo de fornecedor excluído (API)" in acts


async def test_api_without_store(bare: Running) -> None:
    c = bare.client
    r = await c.get(f"{BASE}/suppliers/FOR-0271/attachments")
    assert r.status_code == 503 and "não configurado" in r.json()["detail"]
    assert (await c.delete(f"{BASE}/suppliers/FOR-0271/attachments/a.pdf")).status_code == 503


@pytest.mark.db
async def test_durable_outbox_and_readiness_with_postgres(db_url: str, running: Running) -> None:
    c = running.client
    end = asyncio.get_running_loop().time() + 30
    while asyncio.get_running_loop().time() < end:
        if (await c.get("/fornecedores/_hub/ready")).status_code == 200:
            break
        await asyncio.sleep(0.2)
    else:
        raise AssertionError("banco não ficou pronto")
    assert (await c.post("/fornecedores/suppliers/FOR-0284/lookup")).status_code == 200
    await asyncio.sleep(0.3)
    out = (await c.get("/fornecedores/_hub/outbox?limit=50", headers=HUB)).json()
    assert out["durable"] is True
    assert any(e["action"] == "Consulta cadastral de fornecedor" for e in out["events"])
    # sem tabelas próprias: só as do SDK
    import asyncpg

    conn = await asyncpg.connect(db_url)
    try:
        names = {
            r["tablename"]
            for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        }
    finally:
        await conn.close()
    assert "hub_outbox" in names and "assets" not in names
