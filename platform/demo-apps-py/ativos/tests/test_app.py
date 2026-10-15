"""App over HTTP: without DB (fragments, assets, i18n, flag) and with Postgres (CRUD, 409,
durable import, exports, outbox audit)."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import httpx
import pytest
from hub_sdk.render import thousands

from ativos.importer import generate_xlsx

from .conftest import TOKEN, Running, wait_ready

SECTIONS = ("dashboard", "assets", "movements", "reports", "settings")


async def test_without_db_pages_render_with_notice(running: Running) -> None:
    c = running.client
    r = await c.get("/ativos")
    assert r.status_code == 200 and 'class="app-sidebar"' in r.text
    assert "Conectado como <strong>Ana</strong>" in r.text and "{{HUB_" not in r.text
    assert "<script" not in r.text
    assert r.headers["cache-control"] == "no-store" and r.headers["x-app-version"]
    for s in SECTIONS:
        assert (await c.get(f"/ativos/{s}")).status_code == 200, s
    a = await c.get("/ativos/assets")
    assert "Banco de dados não configurado" in a.text and a.text.index("hub-notice") < a.text.index(
        "app-page-header"
    )
    for path in ("/ativos/_skeleton", "/ativos/assets/app.css", "/ativos/assets/demo.css"):
        assert (await c.get(path)).status_code == 200, path
    assert "--app-primary" in (await c.get("/ativos/assets/app.css")).text
    assert (await c.get("/ativos/assets/x.js")).status_code == 404  # no own JavaScript
    assert (await c.get("/ativos/nada")).status_code == 404
    assert (await c.get("/outro")).status_code == 404
    for p in ("/ativos/itens/1", "/ativos/itens/import", "/ativos/itens/export.xlsx"):
        assert (await c.get(p)).status_code == 404, p
    assert (await c.post("/ativos/assets", data={"tag": "x"})).status_code == 503
    # internal: token required, no jobs without DB
    assert (await c.get("/ativos/_hub/outbox")).status_code == 401
    h = {"x-hub-internal-token": TOKEN}
    assert (await c.get("/ativos/_hub/outbox", headers=h)).json()["durable"] is False
    assert (await c.get("/ativos/_hub/jobs", headers=h)).json() == {"jobs": [], "enabled": False}


async def test_ready_health_and_openapi(running: Running) -> None:
    c = running.client
    r = await c.get("/ativos/_hub/ready")
    assert r.status_code == 200
    doc = (await c.get("/ativos/api/openapi.json")).json()
    ops = {o["operationId"] for p in doc["paths"].values() for o in p.values()}
    assert ops == {
        "assets_list",
        "assets_get",
        "assets_create",
        "assets_update",
        "assets_delete",
        "assets_search",
        "assets_export",
        "assets_export_status",
        "assets_register_documented",
    }
    # the API requires the hub identity
    assert (
        await httpx.AsyncClient(transport=c._transport, base_url="http://t").get("/ativos/api/v1/assets")
    ).status_code == 401


async def test_flag_banner_and_locale(running: Running) -> None:
    c = running.client
    assert "novo-resumo" not in (await c.get("/ativos")).text
    flags = base64.urlsafe_b64encode(json.dumps({"novo-resumo": "on"}).encode()).decode().rstrip("=")
    on = await c.get("/ativos", headers={"x-hub-flags": "novo-resumo=on"})
    assert 'data-flag="novo-resumo"' in on.text, flags
    en = await c.get("/ativos/settings", headers={"x-hub-locale": "en"})
    assert "Settings" in en.text and "Configurações" not in en.text.split("app-sidebar")[0]
    assert 'class="nav-item active"' in en.text
    en = await c.get("/ativos/dashboard", headers={"x-hub-locale": "en"})
    assert "Overview" in en.text and en.headers["content-type"].startswith("text/html")


async def _public_pages(c: httpx.AsyncClient, headers: dict[str, str]) -> list[str]:
    """A static fragment, two views built in Python and the 404 page, all without DB."""
    return [
        (await c.get("/ativos/dashboard", headers=headers)).text,
        (await c.post("/ativos/assets", data={"tag": "x"}, headers=headers)).text,
        (await c.get("/ativos/itens/import/abc", headers=headers)).text,
        (await c.get("/ativos/nada", headers=headers)).text,
    ]


async def test_links_follow_the_public_base(running: Running) -> None:
    alt = await _public_pages(running.client, {"x-hub-base": "/apps/ativos-alt"})
    assert all("/apps/ativos/" not in t for t in alt)
    assert all("/apps/ativos-alt" in t for t in alt)
    assert 'href="/apps/ativos-alt/assets"' in alt[0] and 'action="/apps/ativos-alt/assets"' in alt[1]
    assert 'data-hub-poll="/apps/ativos-alt/itens/import/abc"' in alt[2]
    assert 'href="/apps/ativos-alt"' in alt[3]
    default = await _public_pages(running.client, {})
    assert all("/apps/ativos" in t and "ativos-alt" not in t for t in default)
    # an invalid header falls back to the default base
    bad = await _public_pages(running.client, {"x-hub-base": '/apps/x"><script>'})
    assert all("<script>" not in t and "/apps/ativos" in t for t in bad)


def form(**over: str) -> dict[str, str]:
    base = {
        "tag": "TT-0001",
        "name": "Notebook de teste",
        "category": "Tecnologia",
        "location": "São Paulo",
        "status": "Disponível",
        "responsible": "Ana",
        "department": "TI",
        "acquisition_date": "15/03/2024",
        "acquisition_value_cents": "1.234,56",
        "notes": "",
    }
    base.update(over)
    return base


async def outbox_actions(c: httpx.AsyncClient) -> list[str]:
    r = await c.get("/ativos/_hub/outbox?limit=500", headers={"x-hub-internal-token": TOKEN})
    return [e["action"] for e in r.json()["events"] if e["kind"] == "audit"]


@pytest.mark.db
async def test_crud_conflict_idempotency_and_audit(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    h = {"idempotency-key": "criar-ativo-0001"}
    r1 = await c.post("/ativos/assets", data=form(), headers=h)
    assert r1.status_code == 201 and "Ativo cadastrado" in r1.text and "Patrimônio TT-0001 criado." in r1.text
    r2 = await c.post("/ativos/assets", data=form(), headers=h)
    assert r2.status_code == 201 and r2.headers["idempotent-replay"] == "true"
    dup = await c.post("/ativos/assets", data=form())
    assert dup.status_code == 409 and "Patrimônio já cadastrado" in dup.text
    bad = await c.post("/ativos/assets", data=form(tag="TT-2", status="?"))
    assert bad.status_code == 422 and "Status: valor inválido (use: Disponível, Em uso" in bad.text
    assert (await c.post("/ativos/assets", data=form(tag="TT-3", name=""))).status_code == 422
    listed = await c.get("/ativos/assets?q=TT-0001")
    assert (
        "1 ativos encontrados" in listed.text and "R$ 1.234,56" in listed.text and "15/03/2024" in listed.text
    )
    aid = listed.text.split("edit=")[1].split('"')[0].split("&")[0]
    ok = await c.post(f"/ativos/itens/{aid}", data=form(name="Novo nome", version="1"))
    assert ok.status_code == 200 and "Versão 2 gravada" in ok.text and "Novo nome" in ok.text
    late = await c.post(f"/ativos/itens/{aid}", data=form(name="Atrasado", version="1"))
    assert late.status_code == 409 and "Este ativo foi alterado por outra pessoa" in late.text
    assert "Você editou a versão 1, mas a versão atual é 2" in late.text
    assert (await c.post("/ativos/itens/999999999", data=form(version="1"))).status_code == 404
    stale = await c.post(f"/ativos/itens/{aid}/delete", data={"version": "1"})
    assert stale.status_code == 409
    gone = await c.post(f"/ativos/itens/{aid}/delete", data={"version": "2"})
    assert gone.status_code == 200 and "Ativo excluído" in gone.text and "TT-0001 · Novo nome" in gone.text
    assert (await c.post(f"/ativos/itens/{aid}/delete", data={"version": "2"})).status_code == 404
    panel = await c.get(f"/ativos/assets?edit={aid}")
    assert "Ativo não encontrado" in panel.text
    acts = await outbox_actions(c)
    assert acts.count("Ativo cadastrado") == 1 and acts.count("Ativo atualizado") == 1
    assert acts.count("Ativo excluído") == 1


@pytest.mark.db
async def test_list_filters_sort_and_pager(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    r = await c.get("/ativos/assets?categoria=Veículos&status=Em uso&sort=valor&dir=desc")
    assert "hub-sort active" in r.text and "Valor ↓" in r.text
    assert '<option value="Veículos" selected>' in r.text
    p = await c.get("/ativos/assets?page=2")
    assert "Mostrando 51 a 100 de 500 ativos · página 2 de 10" in p.text
    assert "page=1" in p.text and "page=3" in p.text
    inj = await c.get("/ativos/assets?status='; DROP TABLE assets; --&sort=1;drop")
    assert inj.status_code == 200 and "500 ativos encontrados" in inj.text


@pytest.mark.db
async def test_import_is_durable_and_reports_errors(db_url: str, running: Running, env: Path) -> None:
    c = running.client
    await wait_ready(c)
    xl = env / "imp.xlsx"
    generate_xlsx(xl, 3500, 10, "PY-")
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    files = {"file": ("imp.xlsx", xl.read_bytes(), mime)}
    r = await c.post("/ativos/itens/import", files=files, headers={"idempotency-key": "importar-0000001"})
    assert r.status_code == 202 and "data-hub-poll" in r.text
    job = r.text.split('data-hub-poll="/apps/ativos/itens/import/')[1].split('"')[0]
    for _ in range(300):
        p = await c.get(f"/ativos/itens/import/{job}")
        if "IMPORTAÇÃO CONCLUÍDA" in p.text:
            break
        await asyncio.sleep(0.1)
    assert "IMPORTAÇÃO CONCLUÍDA" in p.text
    errs = await c.get(f"/ativos/itens/import/{job}/erros.csv")
    lines = errs.text.strip().splitlines()
    assert lines[0].endswith("linha;coluna;valor;erro") and len(lines) > 1
    bad_rows = {line.split(";")[0] for line in lines[1:]}
    listed = await c.get("/ativos/assets?q=PY-")
    assert f"{thousands(3500 - len(bad_rows))} ativos encontrados" in listed.text
    # the same spreadsheet again: everything becomes "asset tag already registered" (nothing duplicated)
    r = await c.post("/ativos/itens/import", files=files)
    job2 = r.text.split('data-hub-poll="/apps/ativos/itens/import/')[1].split('"')[0]
    for _ in range(300):
        p = await c.get(f"/ativos/itens/import/{job2}")
        if "IMPORTAÇÃO CONCLUÍDA" in p.text:
            break
        await asyncio.sleep(0.1)
    assert (
        "0 gravadas" in p.text
        and "patrimônio já cadastrado" in (await c.get(f"/ativos/itens/import/{job2}/erros.csv")).text
    )
    acts = await outbox_actions(c)
    assert acts.count("Importação de ativos iniciada") == 2
    assert acts.count("Importação de ativos concluída") == 2
    # upload rejections
    bad = await c.post("/ativos/itens/import", files={"file": ("x.csv", b"a;b", "text/csv")})
    assert bad.status_code == 422 and "Formato não suportado" in bad.text
    fake = await c.post("/ativos/itens/import", files={"file": ("x.xlsx", b"nao e zip", mime)})
    assert fake.status_code == 422 and "O conteúdo não é uma planilha .xlsx." in fake.text


@pytest.mark.db
async def test_exports_csv_stream_xlsx_job_and_429(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    csv = await c.get("/ativos/itens/export.csv?status=Em uso")
    assert csv.status_code == 200 and csv.headers["content-disposition"].startswith("attachment")
    rows = csv.text.strip().splitlines()
    assert rows[0].lstrip("﻿").startswith("Patrimônio;Nome;Categoria") and len(rows) > 10
    assert all(";Em uso;" in r for r in rows[1:])
    # without a heavy-operation slot: 429 + Retry-After
    heavy = running.app.state.ativos.sdk.heavy
    permits = []
    while (p := heavy.try_acquire()) is not None:
        permits.append(p)
    busy = await c.get("/ativos/itens/export.csv")
    assert busy.status_code == 429 and busy.headers["retry-after"] == "10"
    for p in permits:
        p.release()
    r = await c.post("/ativos/itens/export.xlsx", data={"status": "Em uso"})
    assert r.status_code == 202
    job = r.text.split('data-hub-poll="/apps/ativos/itens/export/')[1].split('"')[0]
    for _ in range(300):
        p2 = await c.get(f"/ativos/itens/export/{job}")
        if "EXPORTAÇÃO CONCLUÍDA" in p2.text:
            break
        await asyncio.sleep(0.1)
    dl = await c.get(f"/ativos/itens/export/{job}/ativos.xlsx")
    assert dl.status_code == 200 and dl.content[:2] == b"PK"
    tpl = await c.get("/ativos/itens/modelo.xlsx")
    assert tpl.status_code == 200 and tpl.content[:2] == b"PK"
    acts = await outbox_actions(c)
    assert "Exportação de ativos (CSV)" in acts and "Exportação de ativos (Excel)" in acts


@pytest.mark.db
async def test_search_provider_and_readiness(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    h = {"x-hub-internal-token": TOKEN}
    r = await c.get("/ativos/_hub/search", params={"q": "Notebook", "limit": 5}, headers=h)
    assert r.status_code == 200
    hits = r.json()["results"]
    assert hits and all(x["url"].startswith("/apps/ativos/assets?q=") for x in hits)
    g = await c.get("/ativos/_hub/search", params={"q": "ativos", "limit": 3}, headers=h)
    assert len(g.json()["results"]) == 3
    assert (await c.get("/ativos/_hub/ready")).status_code == 200
