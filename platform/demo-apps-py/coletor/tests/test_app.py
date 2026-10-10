"""Telas e fluxos do Coletor sobre o app real (lifespan). Sem banco: as telas dizem que o
banco não está configurado; com ``HUB_DB_TEST_URL``: Escanear (leitura, idempotência da
fila offline, modo da câmera), Folha de teste com Leituras ao vivo e API."""

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

SECTIONS = ("escanear", "folha", "diagnostico")
SAMPLE = Path(__file__).resolve().parents[3] / "samples" / "src" / "coletor" / "openapi.json"


def idem() -> dict[str, str]:
    return {"idempotency-key": f"t-{uuid.uuid4()}"}


async def test_pages_render_without_db(running: Running) -> None:
    for s in SECTIONS:
        r = await running.client.get(f"/coletor/{s}")
        assert r.status_code == 200, s
        assert "<script" not in r.text.lower(), "app sem JavaScript próprio (CONTRATO-APP §13)"
        assert '="/coletor' not in r.text, f"{s}: link sem /apps"
    r = await running.client.get("/coletor/escanear")
    assert "Banco de dados não configurado" in r.text


async def test_sidebar_has_only_the_three_screens(running: Running) -> None:
    page = (await running.client.get("/coletor/escanear")).text
    for s in SECTIONS:
        assert f'href="/apps/coletor/{s}"' in page
    for old in ("contagem", "recebimento", "consulta", "insumos"):
        assert f'href="/apps/coletor/{old}"' not in page


async def test_old_screens_show_scan_or_sheet(running: Running) -> None:
    for old in ("contagem", "recebimento", "consulta"):
        r = await running.client.get(f"/coletor/{old}")
        assert r.status_code == 200 and 'action="/apps/coletor/escanear/ler"' in r.text, old
    r = await running.client.get("/coletor/insumos")
    assert r.status_code == 200 and 'data-col-step="itens"' in r.text


async def test_device_behaviors_are_declared(running: Running) -> None:
    diag = (await running.client.get("/coletor/diagnostico")).text
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
    assert 'data-hub-install="/apps/coletor"' in diag and "data-hub-install-ios" in diag
    scan = (await running.client.get("/coletor/escanear")).text
    assert "data-hub-scan-continuous" in scan and "data-hub-wedge" in scan and "autofocus" in scan
    assert (
        'data-hub-scan-formats="ean_13,ean_8,upc_a,upc_e,code_128,code_39,itf|qr_code,data_matrix"' in scan
    ), "grupos da alternância dentro da câmera"
    assert 'data-hub-scan-mode="barras"' in scan and 'data-hub-scan-modes="barras|qr"' in scan, (
        "padrão: barras"
    )
    # Tipo de código / modo do leitor / pausa só DENTRO da câmera: a página só leva os valores.
    assert "col-seg" not in scan and 'id="pausa"' not in scan and "scan-mode-field" not in scan
    assert "/coletor/escanear/prefs" in scan and 'data-hub-scan-cooldowns="0|1000|2000|3000|5000"' in scan
    assert 'data-hub-ws="/apps/coletor/ws/live?view=escanear"' in scan and 'id="col-session"' in scan
    assert 'data-hub-scan-reader="interativo"' in scan, "leitor: interativo"
    assert 'data-hub-scan-format-target="#formato"' in scan and "data-hub-scan-wrong-hint=" in scan
    assert "data-hub-offline-queue" in scan and 'data-hub-feedback="beep vibrate flash"' in scan


async def test_sheet_groups_and_open_app_qr(running: Running, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLETOR_PUBLIC_URL", "https://hub.example")
    ins = (await running.client.get("/coletor/folha", headers={"x-hub-base": "/apps/coletor"})).text
    assert "data-col-open-app" in ins and "https://hub.example/apps/coletor" in ins
    assert "Não é um código de teste" in ins
    pos = [ins.index(f'data-col-step="{s}"') for s in ("itens", "locais", "pedidos", "formatos")]
    assert ins.index("data-col-open-app") < pos[0] and pos == sorted(pos)
    assert "LOC-A01" in ins[pos[1] : pos[2]] and "REC-1001" in ins[pos[2] : pos[3]]
    for fmt in ("ean_13", "ean_8", "upc_a", "code_128", "code_39", "qr_code", "data_matrix"):
        assert f'data-sample-format="{fmt}"' in ins
    assert "data-hub-print" in ins and ins.count("<svg") >= 20


async def test_assets_and_not_found(running: Running) -> None:
    r = await running.client.get("/coletor/assets/app.css")
    assert r.status_code == 200 and "--app-primary" in r.text and ':root[data-theme="dark"]' in r.text
    assert (await running.client.get("/coletor/assets/x.js")).status_code == 404
    assert (await running.client.get("/coletor/nada")).status_code == 404


async def test_ready_route(running: Running) -> None:
    r = await running.client.get("/coletor/_hub/ready")
    assert r.status_code in (200, 503)


async def test_openapi_matches_package(running: Running) -> None:
    res = await running.client.get("/coletor/api/openapi.json")
    assert res.status_code == 200
    doc = res.json()
    assert {p for p in doc["paths"]} >= {
        "/coletor/api/v1/items",
        "/coletor/api/v1/reads",
        "/coletor/api/v1/orders/{code}",
    }
    assert "/coletor/api/v1/counts" not in doc["paths"]
    want = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert doc == want, "regenere: python -m coletor openapi > platform/samples/src/coletor/openapi.json"


def test_format_guess_and_urls() -> None:
    from coletor.model import guess_format, is_url

    assert guess_format("7890000000017") == "ean_13"
    assert guess_format("9638507") == "code_128" or guess_format("96385075") == "ean_8"
    assert guess_format("036000291452") == "upc_a"
    assert guess_format("LOC-A01") == "code_128"
    assert guess_format("https://x/apps/coletor") == "qr_code"
    for v in ("https://hub.example.com/coletor", "HTTP://x/y", " https://h/apps/coletor "):
        assert is_url(v), v
    for v in ("LOC-A01", "REC-1001", "7891234567895", ""):
        assert not is_url(v), v


# ---- com banco -------------------------------------------------------------------------
@pytest.mark.db
async def test_scan_classifies_and_is_idempotent(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    await c.post("/coletor/folha/limpar", headers=idem())
    # Automático sem pausa: cada leitura diferente é aceita na hora (leitor físico).
    await c.post("/coletor/escanear/leitor", data={"leitor": "automatico", "pausa": "0"}, headers=idem())
    key = idem()
    item = await c.post(
        "/coletor/escanear/ler", data={"codigo": "7890000000017", "formato": "ean_13"}, headers=key
    )
    assert item.status_code == 200 and 'data-col-last="item"' in item.text
    assert 'data-hub-feedback-result="ok"' in item.text and "EAN-13" in item.text and "câmera" in item.text
    again = await c.post(
        "/coletor/escanear/ler", data={"codigo": "7890000000017", "formato": "ean_13"}, headers=key
    )
    assert again.text == item.text, "fila offline: mesma chave → mesma resposta, sem gravar de novo"
    loc = await c.post("/coletor/escanear/ler", data={"codigo": "loc-a01"}, headers=idem())
    assert "Local: LOC-A01" in loc.text and "coletor físico" in loc.text and "Code 128" in loc.text
    ped = await c.post(
        "/coletor/escanear/ler", data={"codigo": "REC-1001", "formato": "qr_code"}, headers=idem()
    )
    assert "Pedido REC-1001" in ped.text and "QR Code" in ped.text
    url = await c.post(
        "/coletor/escanear/ler", data={"codigo": "https://hub.example.com/apps/coletor"}, headers=idem()
    )
    assert "Link para o app — abre o Coletor" in url.text and 'data-hub-feedback-result="warn"' in url.text
    unk = await c.post("/coletor/escanear/ler", data={"codigo": "NADA-9"}, headers=idem())
    assert "Código desconhecido" in unk.text and 'data-hub-feedback-result="error"' in unk.text
    empty = await c.post("/coletor/escanear/ler", data={"codigo": " "}, headers=idem())
    assert empty.status_code == 422
    live = await c.get("/coletor/folha/leituras")
    assert live.status_code == 200 and 'data-col-count="5"' in live.text
    first = live.text.index("data-col-read=")
    assert "Código desconhecido" in live.text[first : first + 400], "mais nova primeiro"
    scan = (await c.get("/coletor/escanear")).text
    assert (
        scan.count('class="col-read ') == 5
        and "data-hub-feedback-result" not in scan.split("data-col-reads")[0]
    )
    cleared = await c.post("/coletor/folha/limpar", headers=idem())
    assert 'data-col-count="0"' in cleared.text


@pytest.mark.db
async def test_scan_mode_is_remembered_per_user(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    r = await c.post("/coletor/escanear/prefs", data={"modo": "qr"}, headers=idem())
    assert r.status_code == 204
    page = (await c.get("/coletor/escanear")).text
    assert 'data-hub-scan-mode="qr"' in page and 'data-hub-scan-reader="interativo"' in page
    # Tudo junto (como a camada da câmera manda): tipo, leitor e pausa.
    form = {"modo": "barras", "leitor": "automatico", "pausa": "3000"}
    assert (await c.post("/coletor/escanear/prefs", data=form, headers=idem())).status_code == 204
    page = (await c.get("/coletor/escanear")).text
    assert 'data-hub-scan-mode="barras"' in page and 'data-hub-scan-reader="automatico"' in page
    assert 'data-hub-scan-cooldown="3000"' in page
    assert "Modo código de barras — troque para QR|Modo QR — troque para Código de barras" in page
    await c.post("/coletor/escanear/prefs", data={"leitor": "interativo", "pausa": "0"}, headers=idem())


@pytest.mark.db
async def test_search_and_api(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    a = await c.get("/coletor/api/v1/items/COL-0002")
    assert a.status_code == 200 and a.json()["code128"] == "COL-0002"
    assert (await c.get("/coletor/api/v1/items/x")).status_code == 404
    o = (await c.get("/coletor/api/v1/orders/REC-1002")).json()
    assert len(o["lines"]) == 6
    p = await c.post("/coletor/api/v1/reads", json={"value": "LOC-B01"}, headers=idem())
    assert p.status_code == 201 and p.json()["kind"] == "local" and p.json()["source"] == "leitor"
    lst = (await c.get("/coletor/api/v1/reads?limit=1")).json()["items"]
    assert lst[0]["value"] == "LOC-B01"
    s = await c.get(
        "/coletor/_hub/search?q=COL-0003&limit=5",
        headers={"x-hub-internal-token": "coletor-token-de-teste-1234567"},
    )
    assert s.status_code == 200 and s.json()["results"][0]["url"].startswith("/apps/coletor/escanear?codigo=")
    look = (await c.get("/coletor/escanear?codigo=COL-0003")).text
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
    await c.post("/coletor/escanear/leitor", data={"leitor": "automatico", "pausa": "0"}, headers=idem())
    start = (await c.get("/coletor/folha")).text
    assert "data-col-start" in start and "data-col-step=" in start and "data-col-queue" not in start
    r = await c.post("/coletor/sessao/iniciar", data={"tela": "folha", "auto": "1"}, headers=idem())
    assert "data-col-queue" in r.text and "1 de 24" in r.text and "data-col-open-app" not in r.text
    assert 'data-col-step="itens"' not in r.text, "na fila, um código por vez"
    order = _order(r.text)
    exp = _expected(r.text)
    # Erro: não avança; o celular recebe o cartão vermelho.
    bad = await c.post("/coletor/escanear/ler", data={"codigo": "NADA-404"}, headers=idem())
    assert 'data-col-outcome="erro"' in bad.text and 'data-hub-feedback-result="error"' in bad.text
    st = (await c.get("/coletor/folha/estado")).text
    assert 'data-col-flag="erro"' in st and "1 de 24" in st
    # Acerto com avanço automático.
    ok = await c.post("/coletor/escanear/ler", data={"codigo": exp}, headers=idem())
    assert 'data-col-outcome="ok"' in ok.text
    st = (await c.get("/coletor/folha/estado")).text
    assert "2 de 24" in st and _expected(st) == order[1]
    phone = (await c.get("/coletor/escanear/sessao")).text
    assert 'data-col-pos="2"' in phone and f'data-col-expected="{order[1]}"' in phone
    # Parar / Continuar (pelo celular), Próximo = pulado.
    p = await c.post("/coletor/sessao/parar", data={"tela": "escanear"}, headers=idem())
    assert 'data-col-session="paused"' in p.text and 'data-col-action="continuar"' in p.text
    ign = await c.post("/coletor/escanear/ler", data={"codigo": order[1]}, headers=idem())
    assert "data-col-outcome" not in ign.text, "parada não conta"
    await c.post("/coletor/sessao/continuar", data={"tela": "escanear"}, headers=idem())
    nx = await c.post("/coletor/sessao/proximo", data={"tela": "folha"}, headers=idem())
    assert "3 de 24" in nx.text and nx.text.count('data-col-result="pulado"') == 1
    assert nx.text.count('data-col-result="erro"') == 1 and nx.text.count('data-col-result="ok"') == 1
    # Sem avanço automático: certo fica marcado até Próximo.
    await c.post("/coletor/sessao/auto", data={"tela": "folha"}, headers=idem())
    hold = await c.post("/coletor/escanear/ler", data={"codigo": order[2]}, headers=idem())
    assert 'data-col-outcome="ok"' in hold.text
    st = (await c.get("/coletor/folha/estado")).text
    assert "3 de 24" in st and 'data-col-flag="ok"' in st
    # Reiniciar: nova ordem, resultados zerados.
    rs = await c.post("/coletor/sessao/reiniciar", data={"tela": "folha"}, headers=idem())
    assert "1 de 24" in rs.text and "data-col-result=" not in rs.text
    assert _order(rs.text) != order and sorted(_order(rs.text)) == sorted(order)
    # Cancelar: volta ao início (QR do app de novo, sem fila).
    cn = await c.post("/coletor/sessao/cancelar", data={"tela": "folha"}, headers=idem())
    assert "data-col-start" in cn.text and "data-col-queue" not in cn.text
    assert 'data-col-session="cancelled"' in (await c.get("/coletor/escanear/sessao")).text


@pytest.mark.db
async def test_session_finishes_with_summary(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    await c.post("/coletor/escanear/leitor", data={"leitor": "automatico", "pausa": "0"}, headers=idem())
    r = await c.post("/coletor/sessao/iniciar", data={"tela": "folha", "auto": "1"}, headers=idem())
    for i, code in enumerate(_order(r.text)):
        if i % 2:
            await c.post("/coletor/sessao/proximo", data={"tela": "folha"}, headers=idem())
        else:
            await c.post("/coletor/escanear/ler", data={"codigo": code}, headers=idem())
    st = (await c.get("/coletor/folha/estado")).text
    assert 'data-col-state="done"' in st and "data-col-summary" in st
    assert re.search(r"data-col-acertos>12<", st) and re.search(r"data-col-pulados>12<", st)
    assert "data-col-start" in st, "nova sessão a partir do resumo"


@pytest.mark.db
async def test_reader_modes_gate_reads(db_url: str, running: Running) -> None:
    c = running.client
    await wait_ready(c)
    r = await c.post("/coletor/escanear/leitor", data={"leitor": "interativo"}, headers=idem())
    assert r.status_code == 204
    a = await c.post("/coletor/escanear/ler", data={"codigo": "COL-0001"}, headers=idem())
    assert "data-hub-scan-next" in a.text and 'data-hub-feedback-result="ok"' in a.text
    b = await c.post("/coletor/escanear/ler", data={"codigo": "COL-0002"}, headers=idem())
    assert 'data-col-gate="aguardando"' in b.text and "COL-0002" not in b.text.split("data-col-reads")[0]
    n = await c.post("/coletor/escanear/proximo", headers=idem())
    assert "data-hub-scan-next" not in n.text
    d = await c.post("/coletor/escanear/ler", data={"codigo": "COL-0002"}, headers=idem())
    assert 'data-col-last="item"' in d.text and 'data-col-gate="aguardando"' not in d.text
    await c.post("/coletor/escanear/proximo", headers=idem())
    au = await c.post(
        "/coletor/escanear/leitor", data={"leitor": "automatico", "pausa": "2000"}, headers=idem()
    )
    assert au.status_code == 204
    e = await c.post("/coletor/escanear/ler", data={"codigo": "COL-0003"}, headers=idem())
    assert 'data-hub-scan-cooldown="2000"' in e.text and "data-hub-scan-next" not in e.text
    f = await c.post("/coletor/escanear/ler", data={"codigo": "COL-0004"}, headers=idem())
    assert 'data-col-gate="pausa"' in f.text
    page = (await c.get("/coletor/escanear")).text
    assert 'data-hub-scan-reader="automatico"' in page and 'data-hub-scan-cooldown="2000"' in page, "lembrado"
    assert 'data-hub-scan-dedup="5000"' in page


@pytest.mark.db
async def test_websocket_pushes_state_per_user(db_url: str, env: Path) -> None:
    from starlette.testclient import TestClient

    from coletor.app import create_app

    def run() -> None:
        with TestClient(create_app(), headers={"x-hub-user": ANA}) as tc:
            for _ in range(150):
                if "Local: LOC-C04" in tc.get("/coletor/escanear?codigo=LOC-C04").text:
                    break
                time.sleep(0.2)
            tc.post("/coletor/escanear/leitor", data={"leitor": "automatico", "pausa": "0"}, headers=idem())
            with (
                tc.websocket_connect("/coletor/ws/live?view=folha") as ws,
                tc.websocket_connect("/coletor/ws/live?view=escanear") as phone,
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

                r = tc.post("/coletor/sessao/iniciar", data={"tela": "folha", "auto": "1"}, headers=idem())
                until(ws, "1 de 24")
                until(phone, 'data-col-pos="1"')
                tc.post("/coletor/escanear/ler", data={"codigo": _expected(r.text)}, headers=idem())
                until(ws, "2 de 24")
                until(phone, 'data-col-pos="2"')
        with TestClient(create_app()) as anon, pytest.raises(WebSocketDisconnect):  # noqa: SIM117
            with anon.websocket_connect("/coletor/ws/live") as s:
                s.receive_json()

    await asyncio.to_thread(run)
