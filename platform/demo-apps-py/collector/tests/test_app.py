"""Coletor screens and flows over the real app (lifespan). Without DB: the screens say that
the DB is not configured; with ``HUB_DB_TEST_URL``: Scan (reading, offline queue idempotency,
camera mode), Test sheet with Live readings, and API."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.conftest import ANA, Running, wait_ready

SECTIONS = ("scan", "sheet", "diagnostics")
SAMPLE = Path(__file__).resolve().parents[3] / "samples" / "src" / "collector" / "openapi.json"


def idem() -> dict[str, str]:
    return {"idempotency-key": f"t-{uuid.uuid4()}"}


async def test_pages_render_without_db(running: Running) -> None:
    for s in SECTIONS:
        r = await running.client.get(f"/collector/{s}")
        assert r.status_code == 200, s
        assert "<script" not in r.text.lower(), "app sem JavaScript próprio (CONTRATO-APP §13)"
        assert '="/collector' not in r.text, f"{s}: link sem /apps"
    r = await running.client.get("/collector/scan")
    assert "Banco de dados não configurado" in r.text


async def test_sidebar_has_only_the_three_screens(running: Running) -> None:
    page = (await running.client.get("/collector/scan")).text
    for s in SECTIONS:
        assert f'href="/apps/collector/{s}"' in page
    for old in ("count", "receiving", "lookup", "supplies"):
        assert f'href="/apps/collector/{old}"' not in page


async def test_old_screens_show_scan_or_sheet(running: Running) -> None:
    for old in ("count", "receiving", "lookup"):
        r = await running.client.get(f"/collector/{old}")
        assert r.status_code == 200 and 'action="/apps/collector/scan/read"' in r.text, old
    r = await running.client.get("/collector/supplies")
    assert r.status_code == 200 and 'data-col-step="itens"' in r.text


async def test_device_behaviors_are_declared(running: Running) -> None:
    diag = (await running.client.get("/collector/diagnostics")).text
    for check in (
        "secure",
        "online",
        "pwa",
        "camera",
        "torch",
        "scan",
        "wedge",
        "beep",
        "vibrate",
        "capture",
        "storage",
    ):
        assert f'data-hub-diag-check="{check}"' in diag
    assert 'data-hub-install="/apps/collector"' in diag and "data-hub-install-ios" in diag
    scan = (await running.client.get("/collector/scan")).text
    assert "data-hub-scan-continuous" in scan and "data-hub-wedge" in scan and "autofocus" in scan
    assert (
        'data-hub-scan-formats="ean_13,ean_8,upc_a,upc_e,code_128,code_39,itf|qr_code,data_matrix"' in scan
    ), "grupos da alternância dentro da câmera"
    assert 'data-hub-scan-mode="barras"' in scan and 'data-hub-scan-modes="barras|qr"' in scan, (
        "padrão: barras"
    )
    # Code type / reader mode / pause only INSIDE the camera: the page only carries the values.
    assert "col-seg" not in scan and 'id="pausa"' not in scan and "scan-mode-field" not in scan
    assert "/collector/scan/prefs" in scan and 'data-hub-scan-cooldowns="0|1000|2000|3000|5000"' in scan
    assert 'data-hub-ws="/apps/collector/ws/live?view=scan"' in scan and 'id="col-session"' in scan
    assert 'data-hub-scan-reader="interativo"' in scan, "leitor: interativo"
    assert 'data-hub-scan-format-target="#formato"' in scan and "data-hub-scan-wrong-hint=" in scan
    assert "data-hub-offline-queue" in scan and 'data-hub-feedback="beep vibrate flash"' in scan


async def test_sheet_groups_and_open_app_qr(running: Running, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLLECTOR_PUBLIC_URL", "https://hub.example")
    ins = (await running.client.get("/collector/sheet", headers={"x-hub-base": "/apps/collector"})).text
    assert "data-col-open-app" in ins and "https://hub.example/apps/collector" in ins
    assert "Não é um código de teste" in ins
    pos = [ins.index(f'data-col-step="{s}"') for s in ("itens", "locais", "pedidos", "formatos")]
    assert ins.index("data-col-open-app") < pos[0] and pos == sorted(pos)
    assert "LOC-A01" in ins[pos[1] : pos[2]] and "REC-1001" in ins[pos[2] : pos[3]]
    for fmt in ("ean_13", "ean_8", "upc_a", "code_128", "code_39", "qr_code", "data_matrix"):
        assert f'data-sample-format="{fmt}"' in ins
    assert "data-hub-print" in ins and ins.count("<svg") >= 20


async def test_assets_and_not_found(running: Running) -> None:
    r = await running.client.get("/collector/assets/app.css")
    assert r.status_code == 200 and "--app-primary" in r.text and ':root[data-theme="dark"]' in r.text
    assert (await running.client.get("/collector/assets/x.js")).status_code == 404
    assert (await running.client.get("/collector/nada")).status_code == 404


async def test_ready_route(running: Running) -> None:
    r = await running.client.get("/collector/_hub/ready")
    assert r.status_code in (200, 503)


async def test_openapi_matches_package(running: Running) -> None:
    res = await running.client.get("/collector/api/openapi.json")
    assert res.status_code == 200
    doc = res.json()
    assert {p for p in doc["paths"]} >= {
        "/collector/api/v1/items",
        "/collector/api/v1/reads",
        "/collector/api/v1/orders/{code}",
    }
    assert "/collector/api/v1/counts" not in doc["paths"]
    want = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert doc == want, "regenere: python -m collector openapi > platform/samples/src/collector/openapi.json"


def test_format_guess_and_urls() -> None:
    from collector.model import guess_format, is_url

    assert guess_format("7890000000017") == "ean_13"
    assert guess_format("9638507") == "code_128" or guess_format("96385075") == "ean_8"
    assert guess_format("036000291452") == "upc_a"
    assert guess_format("LOC-A01") == "code_128"
    assert guess_format("https://x/apps/collector") == "qr_code"
    for v in ("https://hub.example.com/collector", "HTTP://x/y", " https://h/apps/collector "):
        assert is_url(v), v
    for v in ("LOC-A01", "REC-1001", "7891234567895", ""):
        assert not is_url(v), v


# ---- with DB -------------------------------------------------------------------------
@pytest.mark.db
async def test_scan_classifies_and_is_idempotent(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    await c.post("/collector/sheet/clear", headers=idem())
    # Automatic without pause: each different reading is accepted right away (physical reader).
    await c.post("/collector/scan/reader", data={"leitor": "automatico", "pausa": "0"}, headers=idem())
    key = idem()
    item = await c.post(
        "/collector/scan/read", data={"codigo": "7890000000017", "formato": "ean_13"}, headers=key
    )
    assert item.status_code == 200 and 'data-col-last="item"' in item.text
    assert 'data-hub-feedback-result="ok"' in item.text and "EAN-13" in item.text and "câmera" in item.text
    again = await c.post(
        "/collector/scan/read", data={"codigo": "7890000000017", "formato": "ean_13"}, headers=key
    )
    assert again.text == item.text, "fila offline: mesma chave → mesma resposta, sem gravar de novo"
    loc = await c.post("/collector/scan/read", data={"codigo": "loc-a01"}, headers=idem())
    assert "Local: LOC-A01" in loc.text and "coletor físico" in loc.text and "Code 128" in loc.text
    ped = await c.post(
        "/collector/scan/read", data={"codigo": "REC-1001", "formato": "qr_code"}, headers=idem()
    )
    assert "Pedido REC-1001" in ped.text and "QR Code" in ped.text
    url = await c.post(
        "/collector/scan/read", data={"codigo": "https://hub.example.com/apps/collector"}, headers=idem()
    )
    assert "Link para o app — abre o Coletor" in url.text and 'data-hub-feedback-result="warn"' in url.text
    unk = await c.post("/collector/scan/read", data={"codigo": "NADA-9"}, headers=idem())
    assert "Código desconhecido" in unk.text and 'data-hub-feedback-result="error"' in unk.text
    empty = await c.post("/collector/scan/read", data={"codigo": " "}, headers=idem())
    assert empty.status_code == 422
    live = await c.get("/collector/sheet/reads")
    assert live.status_code == 200 and 'data-col-count="5"' in live.text
    first = live.text.index("data-col-read=")
    assert "Código desconhecido" in live.text[first : first + 400], "mais nova primeiro"
    scan = (await c.get("/collector/scan")).text
    assert (
        scan.count('class="col-read ') == 5
        and "data-hub-feedback-result" not in scan.split("data-col-reads")[0]
    )
    cleared = await c.post("/collector/sheet/clear", headers=idem())
    assert 'data-col-count="0"' in cleared.text


@pytest.mark.db
async def test_scan_mode_is_remembered_per_user(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    r = await c.post("/collector/scan/prefs", data={"modo": "qr"}, headers=idem())
    assert r.status_code == 204
    page = (await c.get("/collector/scan")).text
    assert 'data-hub-scan-mode="qr"' in page and 'data-hub-scan-reader="interativo"' in page
    # All together (as the camera layer sends them): type, reader and pause.
    form = {"modo": "barras", "leitor": "automatico", "pausa": "3000"}
    assert (await c.post("/collector/scan/prefs", data=form, headers=idem())).status_code == 204
    page = (await c.get("/collector/scan")).text
    assert 'data-hub-scan-mode="barras"' in page and 'data-hub-scan-reader="automatico"' in page
    assert 'data-hub-scan-cooldown="3000"' in page
    assert "Modo código de barras — troque para QR|Modo QR — troque para Código de barras" in page
    await c.post("/collector/scan/prefs", data={"leitor": "interativo", "pausa": "0"}, headers=idem())


@pytest.mark.db
async def test_search_and_api(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    a = await c.get("/collector/api/v1/items/COL-0002")
    assert a.status_code == 200 and a.json()["code128"] == "COL-0002"
    assert (await c.get("/collector/api/v1/items/x")).status_code == 404
    o = (await c.get("/collector/api/v1/orders/REC-1002")).json()
    assert len(o["lines"]) == 6
    p = await c.post("/collector/api/v1/reads", json={"value": "LOC-B01"}, headers=idem())
    assert p.status_code == 201 and p.json()["kind"] == "local" and p.json()["source"] == "leitor"
    lst = (await c.get("/collector/api/v1/reads?limit=1")).json()["items"]
    assert lst[0]["value"] == "LOC-B01"
    s = await c.get(
        "/collector/_hub/search?q=COL-0003&limit=5",
        headers={"x-hub-internal-token": "collector-token-de-teste-1234567"},
    )
    assert s.status_code == 200 and s.json()["results"][0]["url"].startswith("/apps/collector/scan?codigo=")
    look = (await c.get("/collector/scan?codigo=COL-0003")).text
    assert "data-col-lookup" in look and "Item:" in look


def _expected(html: str) -> str:
    m = re.search(r'data-col-queue[^>]*data-col-expected="([^"]+)"', html) or re.search(
        r'data-col-expected="([^"]+)"', html
    )
    assert m, html[:400]
    return str(m.group(1))


def _order(html: str) -> list[str]:
    m = re.search(r'data-col-order="([^"]+)"', html)
    assert m
    return str(m.group(1)).split(",")


@pytest.mark.db
async def test_session_queue_over_http(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    await c.post("/collector/scan/reader", data={"leitor": "automatico", "pausa": "0"}, headers=idem())
    start = (await c.get("/collector/sheet")).text
    assert "data-col-start" in start and "data-col-step=" in start and "data-col-queue" not in start
    r = await c.post("/collector/session/start", data={"tela": "sheet", "auto": "1"}, headers=idem())
    assert "data-col-queue" in r.text and "1 de 24" in r.text and "data-col-open-app" not in r.text
    assert 'data-col-step="itens"' not in r.text, "na fila, um código por vez"
    order = _order(r.text)
    exp = _expected(r.text)
    # Error: does not advance; the phone gets the red card.
    bad = await c.post("/collector/scan/read", data={"codigo": "NADA-404"}, headers=idem())
    assert 'data-col-outcome="erro"' in bad.text and 'data-hub-feedback-result="error"' in bad.text
    st = (await c.get("/collector/sheet/status")).text
    assert 'data-col-flag="erro"' in st and "1 de 24" in st
    # Correct, with auto-advance.
    ok = await c.post("/collector/scan/read", data={"codigo": exp}, headers=idem())
    assert 'data-col-outcome="ok"' in ok.text
    st = (await c.get("/collector/sheet/status")).text
    assert "2 de 24" in st and _expected(st) == order[1]
    phone = (await c.get("/collector/scan/session")).text
    assert 'data-col-pos="2"' in phone and f'data-col-expected="{order[1]}"' in phone
    # Stop / Continue (from the phone), Next = skipped.
    p = await c.post("/collector/session/stop", data={"tela": "scan"}, headers=idem())
    assert 'data-col-session="paused"' in p.text and 'data-col-action="resume"' in p.text
    ign = await c.post("/collector/scan/read", data={"codigo": order[1]}, headers=idem())
    assert "data-col-outcome" not in ign.text, "parada não conta"
    await c.post("/collector/session/resume", data={"tela": "scan"}, headers=idem())
    nx = await c.post("/collector/session/next", data={"tela": "sheet"}, headers=idem())
    assert "3 de 24" in nx.text and nx.text.count('data-col-result="pulado"') == 1
    assert nx.text.count('data-col-result="erro"') == 1 and nx.text.count('data-col-result="ok"') == 1
    # Without auto-advance: the correct one stays marked until Next.
    await c.post("/collector/session/auto", data={"tela": "sheet"}, headers=idem())
    hold = await c.post("/collector/scan/read", data={"codigo": order[2]}, headers=idem())
    assert 'data-col-outcome="ok"' in hold.text
    st = (await c.get("/collector/sheet/status")).text
    assert "3 de 24" in st and 'data-col-flag="ok"' in st
    # Reiniciar: nova ordem, resultados zerados.
    rs = await c.post("/collector/session/restart", data={"tela": "sheet"}, headers=idem())
    assert "1 de 24" in rs.text and "data-col-result=" not in rs.text
    assert _order(rs.text) != order and sorted(_order(rs.text)) == sorted(order)
    # Cancel: back to the start (app QR again, no queue).
    cn = await c.post("/collector/session/cancel", data={"tela": "sheet"}, headers=idem())
    assert "data-col-start" in cn.text and "data-col-queue" not in cn.text
    assert 'data-col-session="cancelled"' in (await c.get("/collector/scan/session")).text


@pytest.mark.db
async def test_session_finishes_with_summary(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    await c.post("/collector/scan/reader", data={"leitor": "automatico", "pausa": "0"}, headers=idem())
    r = await c.post("/collector/session/start", data={"tela": "sheet", "auto": "1"}, headers=idem())
    for i, code in enumerate(_order(r.text)):
        if i % 2:
            await c.post("/collector/session/next", data={"tela": "sheet"}, headers=idem())
        else:
            await c.post("/collector/scan/read", data={"codigo": code}, headers=idem())
    st = (await c.get("/collector/sheet/status")).text
    assert 'data-col-state="done"' in st and "data-col-summary" in st
    assert re.search(r"data-col-acertos>12<", st) and re.search(r"data-col-pulados>12<", st)
    assert "data-col-start" in st, "nova sessão a partir do resumo"


@pytest.mark.db
async def test_reader_modes_gate_reads(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    r = await c.post("/collector/scan/reader", data={"leitor": "interativo"}, headers=idem())
    assert r.status_code == 204
    a = await c.post("/collector/scan/read", data={"codigo": "COL-0001"}, headers=idem())
    assert "data-hub-scan-next" in a.text and 'data-hub-feedback-result="ok"' in a.text
    b = await c.post("/collector/scan/read", data={"codigo": "COL-0002"}, headers=idem())
    assert 'data-col-gate="aguardando"' in b.text and "COL-0002" not in b.text.split("data-col-reads")[0]
    n = await c.post("/collector/scan/next", headers=idem())
    assert "data-hub-scan-next" not in n.text
    d = await c.post("/collector/scan/read", data={"codigo": "COL-0002"}, headers=idem())
    assert 'data-col-last="item"' in d.text and 'data-col-gate="aguardando"' not in d.text
    await c.post("/collector/scan/next", headers=idem())
    au = await c.post(
        "/collector/scan/reader", data={"leitor": "automatico", "pausa": "2000"}, headers=idem()
    )
    assert au.status_code == 204
    e = await c.post("/collector/scan/read", data={"codigo": "COL-0003"}, headers=idem())
    assert 'data-hub-scan-cooldown="2000"' in e.text and "data-hub-scan-next" not in e.text
    f = await c.post("/collector/scan/read", data={"codigo": "COL-0004"}, headers=idem())
    assert 'data-col-gate="pausa"' in f.text
    page = (await c.get("/collector/scan")).text
    assert 'data-hub-scan-reader="automatico"' in page and 'data-hub-scan-cooldown="2000"' in page, "lembrado"
    assert 'data-hub-scan-dedup="5000"' in page


@pytest.mark.db
async def test_websocket_pushes_state_per_user(db_url: str, env: Path) -> None:
    from starlette.testclient import TestClient

    from collector.app import create_app

    def run() -> None:
        with TestClient(create_app(), headers={"x-hub-user": ANA}) as tc:
            for _ in range(150):
                if "Local: LOC-C04" in tc.get("/collector/scan?codigo=LOC-C04").text:
                    break
                time.sleep(0.2)
            tc.post("/collector/scan/reader", data={"leitor": "automatico", "pausa": "0"}, headers=idem())
            with (
                tc.websocket_connect("/collector/ws/live?view=sheet") as ws,
                tc.websocket_connect("/collector/ws/live?view=scan") as phone,
            ):
                first = ws.receive_json()
                assert first["target"] == "#col-folha-state" and "data-col-start" in first["html"]
                assert phone.receive_json()["target"] == "#col-session"

                def until(sock: Any, mark: str) -> str:
                    for _ in range(5):
                        h = str(sock.receive_json()["html"])
                        if mark in h:
                            return h
                    raise AssertionError(mark)

                r = tc.post("/collector/session/start", data={"tela": "sheet", "auto": "1"}, headers=idem())
                until(ws, "1 de 24")
                until(phone, 'data-col-pos="1"')
                tc.post("/collector/scan/read", data={"codigo": _expected(r.text)}, headers=idem())
                until(ws, "2 de 24")
                until(phone, 'data-col-pos="2"')
        with TestClient(create_app()) as anon, pytest.raises(WebSocketDisconnect):  # noqa: SIM117
            with anon.websocket_connect("/collector/ws/live") as s:
                s.receive_json()

    await asyncio.to_thread(run)
