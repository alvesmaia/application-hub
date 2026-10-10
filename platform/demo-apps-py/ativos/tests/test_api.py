"""API v1, OpenAPI (paridade com o do app Rust), MCP interno e a saga "cadastro documentado"."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from .conftest import ANA, TOKEN, Running, wait_ready

PLATFORM = Path(__file__).resolve().parents[3]
BASE = "/ativos/api/v1"
OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"


def ops(doc: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(m.upper(), p): o for p, item in doc["paths"].items() for m, o in item.items()}


async def test_openapi_has_the_same_operations_as_the_rust_app(running: Running) -> None:
    py = (await running.client.get("/ativos/api/openapi.json")).json()
    rust = json.loads((PLATFORM / "samples/src/ativos/openapi.json").read_text("utf-8"))
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
    assert {"AssetFields", "AssetUpdate", "AssetRow", "Registration", "Enqueued", "Problem"} <= set(
        py["components"]["schemas"]
    )
    assert py["security"] == rust["security"]


async def test_mcp_tools_come_from_the_openapi(running: Running) -> None:
    h = {"x-hub-internal-token": TOKEN}
    r = await running.client.post(
        "/ativos/_hub/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=h
    )
    assert r.status_code == 200, r.text
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert len(tools) >= 4 and "assets_list" in tools
    assert tools["assets_delete"]["annotations"]["destructiveHint"] is True


async def test_api_requires_identity_and_db(running: Running) -> None:
    c = running.client
    anon = httpx.AsyncClient(transport=c._transport, base_url="http://t")
    r = await anon.get(f"{BASE}/assets")
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/problem+json")
    r = await c.get(f"{BASE}/assets")
    assert r.status_code == 503 and r.headers["retry-after"] == "2"
    bad = await c.post(f"{BASE}/assets", json={"tag": 5})
    assert bad.status_code == 422


@pytest.mark.db
async def test_api_crud_search_export(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    new = {"tag": "API-1", "name": "Notebook API", "category": "Tecnologia", "location": "Recife", "status": "Em uso",
           "acquisition_value_cents": "1.234,56", "acquisition_date": "2024-03-15"}  # fmt: skip
    r = await c.post(f"{BASE}/assets", json=new, headers={"idempotency-key": "api-criar-000001"})
    assert r.status_code == 201
    a = r.json()
    assert a["tag"] == "API-1" and a["version"] == 1 and a["acquisition_value_cents"] == "123456"
    assert a["acquisition_date"] == "2024-03-15"
    assert (await c.post(f"{BASE}/assets", json=new)).status_code == 409
    assert (await c.post(f"{BASE}/assets", json={"tag": "X"})).status_code == 422
    assert (await c.get(f"{BASE}/assets/{a['id']}")).json()["name"] == "Notebook API"
    assert (await c.get(f"{BASE}/assets/99999999")).status_code == 404
    lst = (await c.get(f"{BASE}/assets", params={"q": "API-1"})).json()
    assert lst["total"] == 1 and lst["limit"] == 50 and lst["items"][0]["acquisition_value_cents"] == 123456
    # campos ausentes mantêm o valor atual (inclusive valor e data)
    up = await c.put(f"{BASE}/assets/{a['id']}", json={"version": 1, "fields": {"name": "Renomeado"}})
    assert up.status_code == 200
    j = up.json()
    assert j["version"] == 2 and j["name"] == "Renomeado" and j["acquisition_value_cents"] == "123456"
    assert (await c.put(f"{BASE}/assets/{a['id']}", json={"version": 1, "fields": {}})).status_code == 409
    assert (await c.put(f"{BASE}/assets/{a['id']}", json={"fields": {}})).status_code == 422
    assert (await c.get(f"{BASE}/search", params={"q": "Renomeado"})).json()["total"] == 1
    assert (await c.get(f"{BASE}/search", params={"q": " "})).status_code == 400
    ex = await c.post(f"{BASE}/exports", json={"q": "API-"})
    assert ex.status_code == 202
    job = ex.json()["job"]
    for _ in range(300):
        s = (await c.get(f"{BASE}/exports/{job}")).json()
        if s["status"] == "done":
            break
        await asyncio.sleep(0.1)
    assert s["status"] == "done" and s["download"].endswith("/ativos.xlsx")
    assert (await c.delete(f"{BASE}/assets/{a['id']}", params={"version": 1})).status_code == 409
    d = await c.delete(f"{BASE}/assets/{a['id']}", params={"version": 2})
    assert d.status_code == 200 and d.json()["tag"] == "API-1"
    assert (await c.delete(f"{BASE}/assets/{a['id']}", params={"version": 2})).status_code == 404
    out = await c.get("/ativos/_hub/outbox?limit=500", headers={"x-hub-internal-token": TOKEN})
    acts = [e["action"] for e in out.json()["events"] if e["kind"] == "audit"]
    assert {"Ativo cadastrado (API)", "Ativo atualizado (API)", "Ativo excluído (API)"} <= set(acts)


async def _wait_job(c: httpx.AsyncClient, job: str) -> dict[str, Any]:
    h = {"x-hub-internal-token": TOKEN}
    for _ in range(300):
        jobs = (await c.get("/ativos/_hub/jobs", headers=h)).json()["jobs"]
        j = next(x for x in jobs if x["id"] == job)
        if j["status"] in ("done", "failed", "dead"):
            return j  # type: ignore[no-any-return]
        await asyncio.sleep(0.1)
    raise AssertionError("tarefa não terminou")


@pytest.mark.db
async def test_saga_creates_notifies_and_compensates(
    db_url: str, running: Running, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = running.client
    await wait_ready(c)
    ok = await c.post(f"{BASE}/assets/registrations", json={"tag": "SG-1", "name": "Saga ok"})
    assert ok.status_code == 202
    j = await _wait_job(c, ok.json()["job"])
    assert j["status"] == "done" and j["result"]["saga"] == "completed"
    assert (await c.get(f"{BASE}/assets", params={"q": "SG-1"})).json()["total"] == 1
    # falha injetada no último passo: o ativo criado é desfeito
    monkeypatch.setenv("HUB_DEMO_SAGA_FAULTS", "1")
    bad = await c.post(
        f"{BASE}/assets/registrations", json={"tag": "SG-2", "name": "Saga falha", "falhar_em": "notificar"}
    )
    j2 = await _wait_job(c, bad.json()["job"])
    assert j2["status"] == "failed" and "falha simulada em notificar" in j2["error"]
    assert (await c.get(f"{BASE}/assets", params={"q": "SG-2"})).json()["total"] == 0
    # documento sem a conexão ativos-docs vinculada: falha definitiva e compensa
    nodoc = await c.post(
        f"{BASE}/assets/registrations",
        json={"tag": "SG-3", "name": "Sem docs", "upload": "upl_x", "file_name": "a.pdf"},
    )
    j3 = await _wait_job(c, nodoc.json()["job"])
    assert j3["status"] == "failed" and "ativos-docs" in j3["error"]
    assert (await c.get(f"{BASE}/assets", params={"q": "SG-3"})).json()["total"] == 0
    assert (await c.post(f"{BASE}/assets/registrations", json={"tag": " ", "name": "x"})).status_code == 422


_ = ANA


async def test_packaged_openapi_matches_the_code(running: Running) -> None:
    text = await asyncio.to_thread(OPENAPI.read_text, "utf-8")
    on_disk = json.loads(text)
    assert on_disk == running.app.state.hub_api_doc(), "regenere: python -m ativos openapi > openapi.json"


def test_fragments_css_and_catalog_are_the_rust_apps() -> None:
    """Paridade de markup das seções estáticas: os arquivos são os do app Rust (sem JS próprio)."""
    here = Path(__file__).resolve().parents[1] / "src" / "ativos"
    rust = PLATFORM / "demo-apps"
    for f in (rust / "fragments" / "ativos").iterdir():
        assert (here / "fragments" / f.name).read_bytes() == f.read_bytes(), f.name
    assert (here / "assets" / "app.css").read_bytes() == (rust / "assets" / "ativos.css").read_bytes()
    assert (here / "i18n.json").read_bytes() == (rust / "i18n.json").read_bytes()
