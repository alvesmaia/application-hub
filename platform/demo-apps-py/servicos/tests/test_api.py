"""API v1, OpenAPI (igual ao contrato empacotado em samples/), MCP interno e idempotência."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from .conftest import ANA, HUB, Running, wait_ready

PLATFORM = Path(__file__).resolve().parents[3]
BASE = "/servicos/api/v1"
OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"
YEAR = str(datetime.now(UTC).year)


def ops(doc: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(m.upper(), p): o for p, item in doc["paths"].items() for m, o in item.items()}


async def test_openapi_has_the_same_operations_as_the_packaged_contract(running: Running) -> None:
    py = (await running.client.get("/servicos/api/openapi.json")).json()
    packaged = json.loads((PLATFORM / "samples/src/servicos/openapi.json").read_text("utf-8"))
    a, b = ops(packaged), ops(py)
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
    assert py["openapi"] == packaged["openapi"] and py["servers"] == packaged["servers"]
    assert py["security"] == packaged["security"] and "Problem" in py["components"]["schemas"]
    body = py["components"]["schemas"]["TicketIn"]
    assert set(body["required"]) == {"title", "category", "priority"}
    assert "Aguardando" in py["components"]["schemas"]["StatusIn"]["properties"]["status"]["enum"]


def test_packaged_openapi_matches_the_code(env: Path) -> None:
    from servicos.app import create_app

    assert json.loads(OPENAPI.read_text("utf-8")) == create_app().state.hub_api_doc()


async def test_mcp_tools_come_from_the_openapi(running: Running) -> None:
    r = await running.client.post(
        "/servicos/_hub/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=HUB
    )
    assert r.status_code == 200, r.text
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert set(tools) == {
        "tickets_list",
        "tickets_get",
        "tickets_open",
        "tickets_set_status",
        "reports_summary",
        "approvals_pending",
    }
    assert tools["tickets_set_status"]["annotations"]["destructiveHint"] is True
    assert not tools["tickets_list"]["annotations"].get("destructiveHint")
    call = await running.client.post(
        "/servicos/_hub/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "tickets_list", "arguments": {}},
        },
        headers={**HUB, "x-hub-user": ANA},
    )
    assert "banco de dados" in call.text  # sem banco: 503 problem, mas a ferramenta responde


async def test_api_requires_identity_and_db(running: Running) -> None:
    anon = running.anon()
    good = {"title": "t", "category": "Acessos", "priority": "Alta"}
    for method, path, body in (
        ("GET", "/tickets", None),
        ("GET", "/tickets/1", None),
        ("POST", "/tickets", good),
        ("PUT", "/tickets/1/status", {"status": "Aberto", "version": 1}),
        ("GET", "/reports/summary", None),
        ("GET", "/approvals", None),
    ):
        r = await anon.request(method, BASE + path, json=body)
        assert r.status_code == 401 and r.headers["content-type"].startswith("application/problem+json"), path
    c = running.client
    for method, path, body in (
        ("GET", "/tickets", None),
        ("GET", "/tickets/1", None),
        ("POST", "/tickets", {"title": "t", "category": "Acessos", "priority": "Alta"}),
        ("PUT", "/tickets/1/status", {"status": "Aberto", "version": 1}),
        ("GET", "/reports/summary", None),
        ("GET", "/approvals", None),
    ):
        r = await c.request(method, BASE + path, json=body)
        assert r.status_code == 503 and r.headers["retry-after"] == "2", path
    bad = await c.post(f"{BASE}/tickets", json={"title": "t"})
    assert bad.status_code == 422 and "category" in bad.json()["detail"]
    # fora da API a validação do FastAPI segue o padrão
    assert (await c.post("/servicos/requests/1")).status_code in (404, 503)


@pytest.mark.db
async def test_tickets_crud_and_status_conflicts(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    lst = (await c.get(f"{BASE}/tickets", params={"limit": "3"})).json()
    assert lst["total"] == 3 and lst["limit"] == 3 and lst["items"][0]["id"] == 600
    assert lst["items"][0]["protocol"] == f"SD-{YEAR}-0000600"
    assert (await c.get(f"{BASE}/tickets", params={"limit": "x"})).json()["limit"] == 25
    assert (await c.get(f"{BASE}/tickets", params={"limit": "9999"})).json()["limit"] == 100
    assert (await c.get(f"{BASE}/tickets", params={"status": "Nada"})).json()["total"] == 25
    assert (await c.get(f"{BASE}/tickets", params={"protocol": f"SD-{YEAR}-0000600"})).json()["total"] == 1
    assert (
        await c.get(f"{BASE}/tickets", params={"category": "Software", "status": "Fechado"})
    ).status_code == 200
    one = await c.get(f"{BASE}/tickets/1")
    assert one.status_code == 200 and one.json()["version"] == 1
    nf = await c.get(f"{BASE}/tickets/12345678")
    assert nf.status_code == 404 and nf.json()["detail"] == "chamado não encontrado"

    inv = await c.post(f"{BASE}/tickets", json={"title": " ", "category": "Acessos", "priority": "Alta"})
    assert inv.status_code == 422 and "obrigatórios" in inv.json()["detail"]
    inv2 = await c.post(f"{BASE}/tickets", json={"title": "x", "category": "Nada", "priority": "Alta"})
    assert inv2.status_code == 422
    new = await c.post(
        f"{BASE}/tickets",
        json={"title": "Novo via API", "category": "Software", "priority": "Crítica", "description": "d"},
    )
    j = new.json()
    assert new.status_code == 201 and j["status"] == "Aberto" and j["approval_requested"] is False
    assert j["protocol"].startswith(f"SD-{YEAR}-") and j["id"] >= 10_000_000
    got = (await c.get(f"{BASE}/tickets/{j['id']}")).json()
    assert got["requester"] == "Ana" and got["priority"] == "Crítica"

    st = await c.put(f"{BASE}/tickets/{j['id']}/status", json={"status": "Resolvido", "version": 1})
    assert st.status_code == 200 and st.json()["status"] == "Resolvido" and st.json()["version"] == 2
    stale = await c.put(f"{BASE}/tickets/{j['id']}/status", json={"status": "Fechado", "version": 1})
    assert stale.status_code == 409
    assert (
        await c.put(f"{BASE}/tickets/{j['id']}/status", json={"status": "Nada", "version": 2})
    ).status_code == 422
    assert (
        await c.put(f"{BASE}/tickets/{j['id']}/status", json={"status": "Aberto", "version": 2**40})
    ).status_code == 409
    assert (
        await c.put(f"{BASE}/tickets/12345678/status", json={"status": "Aberto", "version": 1})
    ).status_code == 409
    assert (await c.put(f"{BASE}/tickets/{j['id']}/status", json={"status": "Aberto"})).status_code == 422
    await asyncio.sleep(0.1)
    out = (await c.get("/servicos/_hub/outbox?limit=500", headers=HUB)).json()
    acts = [e["action"] for e in out["events"] if e["kind"] == "audit"]
    assert "Chamado aberto (API)" in acts and "Chamado atualizado (API)" in acts


@pytest.mark.db
async def test_idempotency_key_replays_the_creation(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    body = {"title": "Só uma vez", "category": "Acessos", "priority": "Baixa"}
    h = {"idempotency-key": "k-servicos-123456"}
    first = await c.post(f"{BASE}/tickets", json=body, headers=h)
    second = await c.post(f"{BASE}/tickets", json=body, headers=h)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert (await c.get(f"{BASE}/tickets", params={"limit": "5"})).json()["items"][0]["title"] == "Só uma vez"


@pytest.mark.db
async def test_summary_report_and_approval_request(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    s = (
        await c.get(f"{BASE}/reports/summary", params={"de": "2024-01-01", "ate": today, "agrupar": "semana"})
    ).json()
    assert s["group_by"] == "semana" and s["to"] == today and s["rows"]
    row = s["rows"][0]
    assert set(row) == {"category", "period", "total", "resolved", "avg_hours", "within_sla"}
    assert sum(r["total"] for r in s["rows"]) == 600
    only = (await c.get(f"{BASE}/reports/summary", params={"categoria": "Acessos", "agrupar": "x"})).json()
    assert only["group_by"] == "mes" and {r["category"] for r in only["rows"]} <= {"Acessos"}
    r = await c.post(
        f"{BASE}/tickets",
        json={"title": "Pede aval", "category": "Compras", "priority": "Normal", "approval": True},
    )
    assert r.json()["status"] == "Aguardando" and r.json()["approval_requested"] is True
    pend = (await c.get(f"{BASE}/approvals")).json()
    assert pend["total"] == 1 and pend["items"][0]["title"] == "Pede aval"


async def test_internal_token_is_required(running: Running) -> None:
    r = await running.client.post(
        "/servicos/_hub/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert r.status_code == 401
