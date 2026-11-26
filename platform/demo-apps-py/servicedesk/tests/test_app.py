"""App over HTTP: without DB (fragments, nav, assets, i18n, notices) and with Postgres (open, list,
paginate, change status with 409, Inbox approvals, report, CSV, seed)."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

import asyncpg
import httpx
import pytest

from .conftest import ANA, BOSS, HUB, Running, wait_ready

SECTIONS = ("dashboard", "requests", "approvals", "knowledge", "settings")
YEAR = str(datetime.now(UTC).year)


async def test_without_db_pages_render_with_notice_and_nav(running: Running) -> None:
    c = running.client
    r = await c.get("/servicedesk")
    assert r.status_code == 200 and 'class="app-sidebar app3-sidebar"' in r.text
    assert "Conectado como <strong>Ana</strong>" in r.text and "{{HUB_" not in r.text
    assert "<script" not in r.text
    assert r.headers["cache-control"] == "no-store" and r.headers["x-app-version"]
    for s in SECTIONS:
        page = await c.get(f"/servicedesk/{s}")
        assert page.status_code == 200 and 'href="/apps/servicedesk/reports"' in page.text, s
    req = await running.anon().get("/servicedesk/requests", headers={"x-hub-user": ANA})
    assert "Banco de dados não configurado" in req.text
    assert req.text.index("hub-notice") < req.text.index("app3-request-stats")
    assert "svc_sid=" in req.headers["set-cookie"] and "HttpOnly" in req.headers["set-cookie"]
    again = await c.get("/servicedesk/requests", headers={"cookie": "svc_sid=abc123"})
    assert "set-cookie" not in again.headers
    for path in ("/servicedesk/_skeleton", "/servicedesk/assets/app.css", "/servicedesk/assets/demo.css"):
        assert (await c.get(path)).status_code == 200, path
    assert (
        "--app3" in (await c.get("/servicedesk/assets/app.css")).text
        or "app3" in (await c.get("/servicedesk/assets/app.css")).text
    )
    assert (await c.get("/servicedesk/assets/x.js")).status_code == 404  # no own JavaScript
    assert (await c.get("/servicedesk/nada")).status_code == 404
    assert (await c.get("/outro")).status_code == 404
    assert (await c.get("/servicedesk/requests/5")).status_code == 404
    assert (await c.post("/servicedesk/requests/abc", data={"status": "Aberto"})).status_code == 404
    assert (await c.post("/servicedesk/requests", data={"title": "x"})).status_code == 503
    assert (await c.post("/servicedesk/requests/5", data={"status": "Aberto"})).status_code == 503
    assert (await c.get("/servicedesk/reports/export.csv")).status_code == 503


async def test_reports_page_without_db(running: Running) -> None:
    c = running.client
    r = await c.get("/servicedesk/reports")
    assert 'class="nav-item active" href="/apps/servicedesk/reports"' in r.text
    assert 'class="nav-item active" href="/apps/servicedesk/requests"' not in r.text
    assert "Escolha o período" in r.text and re.search(r"Cache de sessões: \d+ de 2\.000", r.text)
    r2 = await c.get("/servicedesk/reports?de=2026-01-01")
    assert "Banco de dados não configurado" in r2.text


async def test_internals_ready_locale_and_openapi(running: Running) -> None:
    c = running.client
    assert (await c.get("/servicedesk/_hub/ready")).status_code == 200
    assert (await c.get("/servicedesk/_hub/outbox")).status_code == 401
    assert (await c.get("/servicedesk/_hub/outbox", headers=HUB)).json()["durable"] is False
    en = await c.get("/servicedesk/settings", headers={"x-hub-locale": "en"})
    assert "Conectado como" not in en.text and en.status_code == 200
    doc = (await c.get("/servicedesk/api/openapi.json")).json()
    ops = {o["operationId"] for p in doc["paths"].values() for o in p.values()}
    assert len(ops) == 6
    deny = await c.post(
        "/servicedesk/_hub/approvals/sd-1", json={"decision": "approve"}, headers={**HUB, "x-hub-user": BOSS}
    )
    assert deny.status_code == 503  # no DB: nothing to decide now
    bad = await c.post(
        "/servicedesk/_hub/approvals/sd-x", json={"decision": "approve"}, headers={**HUB, "x-hub-user": BOSS}
    )
    assert bad.status_code == 404
    nodb = await c.post(
        "/servicedesk/_hub/approvals/sd-9", json={"decision": "approve"}, headers={**HUB, "x-hub-user": BOSS}
    )
    assert nodb.status_code == 503


# ---- with Postgres ----------------------------------------------------------------------------
async def open_ticket(c: httpx.AsyncClient, **over: str) -> httpx.Response:
    data = {"title": "Preciso de VPN", "category": "Acessos", "priority": "Alta", "description": "d"}
    data.update(over)
    return await c.post("/servicedesk/requests", data=data)


@pytest.mark.db
async def test_seed_counters_and_current_year_protocols(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    r = await c.get("/servicedesk/requests")
    assert r.status_code == 200 and f"SD-{YEAR}-0000600" in r.text
    assert "25 chamados nesta página" in r.text and "Mais antigos" in r.text
    # counters in the units (1 in 50 of each state in recent days), not thousands
    stats = r.text.split('class="app3-request-stats"')[1].split("</div></div>")[0]
    assert "<strong>" in stats
    # idempotent load: nothing new; an old database gets re-dated
    conn = await asyncpg.connect(db_url)
    try:
        from servicedesk.model import REDATE_SQL
        from servicedesk.seed import seed_into

        await seed_into(conn, 600)
        assert await conn.fetchval("SELECT count(*) FROM tickets") == 600
        await conn.execute("UPDATE tickets SET status = 'Aguardando', protocol = 'SD-1-' || id")
        await conn.execute(REDATE_SQL.replace("{TOTAL}", "600"))
        assert await conn.fetchval("SELECT count(*) FROM tickets WHERE status = 'Aguardando'") < 600
        assert await conn.fetchval("SELECT protocol FROM tickets WHERE id = 600") == f"SD-{YEAR}-0000600"
        assert (await conn.fetchval("SELECT last_value FROM tickets_id_seq")) >= 10_000_000
    finally:
        await conn.close()


@pytest.mark.db
async def test_dashboard_kpis_use_the_status_counts(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    stats = (await c.get("/servicedesk/requests")).text.split('class="app3-request-stats"')[1]
    opened = stats.split("<strong>")[1].split("</strong>")[0]
    kpis = (await c.get("/servicedesk")).text.split('class="app3-kpi-row"')[1].split("</div>")[0]
    assert f"<strong>{opened}</strong>" in kpis and "<strong>38</strong>" not in kpis
    assert kpis.count("(dados ilustrativos)") == 1  # only the average resolution time stays static


@pytest.mark.db
async def test_open_list_filter_paginate_and_update(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    bad = await open_ticket(c, title="", category="Nada")
    assert bad.status_code == 422 and "Verifique os campos" in bad.text
    assert (await open_ticket(c, title="a\x01b")).status_code == 422
    ok = await open_ticket(c)
    proto = f"SD-{YEAR}-10000001"  # the app's ids start after 10 million
    assert ok.status_code == 201 and f"Protocolo {proto}" in ok.text and "Ver chamado" in ok.text
    found = await c.get("/servicedesk/requests", params={"protocolo": proto.lower()})
    assert "Preciso de VPN" in found.text and "Ana" in found.text and "app3-priority alta" in found.text
    assert "1 chamados nesta página" in found.text
    opened = await c.get("/servicedesk/requests?new=1")
    assert 'id="ticket-new"' in opened.text
    filt = await c.get(
        "/servicedesk/requests", params={"status": "Aberto", "categoria": "Acessos", "prioridade": "Alta"}
    )
    assert filt.status_code == 200 and "selected>Alta" in filt.text
    empty = await c.get("/servicedesk/requests", params={"protocolo": "SD-NADA"})
    assert "Nenhum chamado com esses filtros." in empty.text
    page2 = await c.get("/servicedesk/requests", params={"antes": "600", "status": "Resolvido"})
    assert "« Mais recentes" in page2.text
    assert (await c.get("/servicedesk/requests", params={"antes": "9" * 30})).status_code == 200

    tid = (await c.get("/servicedesk/api/v1/tickets", params={"protocol": proto})).json()["items"][0]["id"]
    upd = await c.post(f"/servicedesk/requests/{tid}", data={"status": "Resolvido", "version": "1"})
    assert upd.status_code == 200 and "Atualizado" in upd.text and "Versão 2" in upd.text
    assert "app3-request-status done" in upd.text and "—" in upd.text
    stale = await c.post(f"/servicedesk/requests/{tid}", data={"status": "Fechado", "version": "1"})
    assert (
        stale.status_code == 409
        and "alterado por outra pessoa" in stale.text
        and "versão atual é 2" in stale.text.replace("a atual é 2", "versão atual é 2")
    )
    assert (
        await c.post(f"/servicedesk/requests/{tid}", data={"status": "Nada", "version": "2"})
    ).status_code == 422
    assert (
        await c.post("/servicedesk/requests/999999999", data={"status": "Aberto", "version": "1"})
    ).status_code == 404
    assert (await c.post(f"/servicedesk/requests/{tid}", data={"status": "Aberto"})).status_code == 409
    await asyncio.sleep(0.1)
    out = (await c.get("/servicedesk/_hub/outbox?limit=500", headers=HUB)).json()
    acts = [e["action"] for e in out["events"] if e["kind"] == "audit"]
    assert out["durable"] is True and "Chamado aberto" in acts and "Chamado atualizado" in acts


@pytest.mark.db
async def test_approval_flow_through_the_inbox(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    ok = await open_ticket(c, title="Comprar monitor", approval="1")
    assert ok.status_code == 201 and "aguardando aprovação na Inbox" in ok.text
    pend = (await c.get("/servicedesk/api/v1/approvals")).json()
    item = pend["items"][0]
    assert item["inbox_item"] == f"sd-{item['ticket_id']}" and item["requested_by"] == "Ana"
    url = f"/servicedesk/_hub/approvals/{item['inbox_item']}"
    hdr = {**HUB, "x-hub-user": BOSS}
    # the requester does not decide their own request
    own = await c.post(
        url, json={"decision": "approve"}, headers={**HUB, "x-hub-user": _ana_with_permission()}
    )
    assert own.status_code == 403
    nope = await c.post(
        url, json={"decision": "approve"}, headers={**HUB, "x-hub-user": _anon_no_permission()}
    )
    assert nope.status_code == 403
    done = await c.post(url, json={"decision": "approve", "comment": "ok"}, headers=hdr)
    assert done.status_code == 200 and done.json() == {"status": "approved"}
    again = await c.post(url, json={"decision": "reject"}, headers=hdr)
    assert again.status_code == 409 and again.json()["status"] == "approved"
    assert (
        await c.post("/servicedesk/_hub/approvals/sd-424242", json={"decision": "reject"}, headers=hdr)
    ).status_code == 404
    got = (await c.get(f"/servicedesk/api/v1/tickets/{item['ticket_id']}")).json()
    assert got["status"] == "Em atendimento" and got["version"] == 2
    assert (await c.get("/servicedesk/api/v1/approvals")).json()["total"] == 0
    # recusa → Fechado
    ok2 = await open_ticket(c, title="Comprar cadeira", approval="1")
    assert ok2.status_code == 201
    tid2 = (await c.get("/servicedesk/api/v1/approvals")).json()["items"][0]["ticket_id"]
    rej = await c.post(f"/servicedesk/_hub/approvals/sd-{tid2}", json={"decision": "reject"}, headers=hdr)
    assert rej.json() == {"status": "rejected"}
    assert (await c.get(f"/servicedesk/api/v1/tickets/{tid2}")).json()["status"] == "Fechado"
    await asyncio.sleep(0.1)
    ev = (await c.get("/servicedesk/_hub/outbox?limit=500", headers=HUB)).json()["events"]
    kinds = {e["kind"] for e in ev}
    assert {"approval_request", "notification", "audit"} <= kinds
    req = next(e for e in ev if e["kind"] == "approval_request")
    assert req["meta"]["to_roles"] == ["Administrador"] and req["target"].startswith("sd-")
    note = next(e for e in ev if e["kind"] == "notification")
    assert note["meta"]["to_users"] == ["ana@demo.local"]


def _ana_with_permission() -> str:
    from hub_sdk.identity import Identity

    return Identity("ana@demo.local", name="Ana", permissions=("aprovar_solicitacoes",)).encode()


def _anon_no_permission() -> str:
    from hub_sdk.identity import Identity

    return Identity("x@demo.local").encode()


@pytest.mark.db
async def test_reports_cache_csv_and_busy(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    q = {
        "de": "2024-01-01",
        "ate": datetime.now(UTC).strftime("%Y-%m-%d"),
        "agrupar": "mes",
        "categoria": "Acessos",
    }
    cold = await c.get("/servicedesk/reports", params=q)
    assert cold.status_code == 200 and "linhas · " in cold.text and "(cache da sessão)" not in cold.text
    assert "Exportar CSV" in cold.text and "/_frag/servicedesk/reports/export.csv?de=2024-01-01" in cold.text
    cookie = {"cookie": cold.headers["set-cookie"].split(";")[0]}
    warm = await c.get("/servicedesk/reports", params=q, headers=cookie)
    assert "(cache da sessão)" in warm.text
    csv = await c.get("/servicedesk/reports/export.csv", params=q)
    assert csv.status_code == 200 and csv.content.startswith(b"\xef\xbb\xbfperiodo;")
    assert "attachment" in csv.headers["content-disposition"] and b";Acessos;" in csv.content
    assert (
        await c.get("/servicedesk/reports", params={**q, "de": "2099-01-01", "ate": "2099-01-02"})
    ).text.count("Sem chamados")
    # no heavy-operation slot: 429 with Retry-After
    heavy = running.app.state.servicedesk.sdk.heavy
    permits = [heavy.try_acquire() for _ in range(heavy.max)]
    try:
        busy = await c.get("/servicedesk/reports", params={**q, "agrupar": "dia"})
        assert busy.status_code == 429 and "retry-after" in busy.headers and "Relatório em fila" in busy.text
        exp = await c.get("/servicedesk/reports/export.csv", params=q)
        assert exp.status_code == 429 and "Exportação em fila" in exp.text
        api = await c.get("/servicedesk/api/v1/reports/summary", params=q)
        assert api.status_code == 429 and api.headers["retry-after"] == "5"
    finally:
        for p in permits:
            assert p is not None
            p.release()
    await asyncio.sleep(0.1)
    out = (await c.get("/servicedesk/_hub/outbox?limit=500", headers=HUB)).json()
    assert any(e["action"] == "Relatório de chamados exportado" for e in out["events"])


@pytest.mark.db
async def test_tables_are_compatible_with_the_rust_schema(db_url: str, running: Running) -> None:
    await wait_ready(running.client)
    conn = await asyncpg.connect(db_url)
    try:
        cols = {
            r["column_name"]
            for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'tickets'"
            )
        }
        assert cols == {
            "id", "protocol", "title", "requester", "category", "priority", "status", "assignee",
            "description", "opened_at", "updated_at", "resolved_at", "sla_due", "version",
        }  # fmt: skip
        idx = {
            r["indexname"]
            for r in await conn.fetch("SELECT indexname FROM pg_indexes WHERE tablename = 'tickets'")
        }
        assert {"tickets_status", "tickets_category", "tickets_report", "tickets_protocol_key"} <= idx
        assert await conn.fetchval("SELECT to_regclass('ticket_approvals')") is not None
    finally:
        await conn.close()


@pytest.mark.db
async def test_db_unavailable_paths(db_url: str, running: Running) -> None:
    """DB goes down after ready: errors become notices (not a blank 500)."""
    c = running.client
    await wait_ready(c)
    st = running.app.state.servicedesk
    await st.sdk.pool.close()
    page = await c.get("/servicedesk/requests")
    assert page.status_code == 200 and "Erro ao consultar os chamados" in page.text
    rep = await c.get("/servicedesk/reports", params={"de": "2024-01-01"})
    assert "Erro ao gerar o relatório" in rep.text
    assert (await c.get("/servicedesk/reports/export.csv")).status_code == 503
    r = await open_ticket(c)
    assert r.status_code in (503, 500)
    up = await c.post("/servicedesk/requests/1", data={"status": "Aberto", "version": "1"})
    assert up.status_code in (503, 500)


@pytest.mark.db
async def test_seed_command(db_url: str, env: object, capsys: pytest.CaptureFixture[str]) -> None:
    from servicedesk.seed import run_seed

    assert await run_seed() == 0
    conn = await asyncpg.connect(db_url)
    try:
        assert await conn.fetchval("SELECT count(*) FROM tickets") == 600
        assert await run_seed() == 0
        assert await conn.fetchval("SELECT count(*) FROM tickets") == 600
    finally:
        await conn.close()


async def test_seed_command_needs_a_database(env: object) -> None:
    from servicedesk.seed import run_seed

    assert await run_seed() == 2
