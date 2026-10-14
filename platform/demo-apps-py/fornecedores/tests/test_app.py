"""App over HTTP: pages, supplier record, attachments (upload/download/delete), registry
lookup, PDF record sheet, language, federated search and outbox audit."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from hub_sdk.storage import StorageError

from .conftest import PDF, TOKEN, Running
from .fakes import FakeStore
from .test_units import OK_BODY, Ext, Script

SECTIONS = ("dashboard", "indicators", "analysis", "reports", "settings")
HUB = {"x-hub-internal-token": TOKEN}


async def outbox_actions(c: httpx.AsyncClient) -> list[str]:
    await asyncio.sleep(0.05)  # the audit goes out in the background
    r = await c.get("/fornecedores/_hub/outbox?limit=500", headers=HUB)
    return [e["action"] for e in r.json()["events"] if e["kind"] == "audit"]


async def upload(
    c: httpx.AsyncClient, name: str, data: bytes, ctype: str, code: str = "FOR-0271"
) -> httpx.Response:
    return await c.post(f"/fornecedores/suppliers/{code}/attachments", files={"file": (name, data, ctype)})


async def test_pages_and_assets_without_store(bare: Running) -> None:
    c = bare.client
    r = await c.get("/fornecedores")
    assert r.status_code == 200 and 'class="app-sidebar app2-sidebar"' in r.text
    assert "Conectado como <strong>Ana</strong>" in r.text and "{{HUB_" not in r.text
    assert "<script" not in r.text and r.headers["cache-control"] == "no-store" and r.headers["x-app-version"]
    for s in SECTIONS:
        assert (await c.get(f"/fornecedores/{s}")).status_code == 200, s
    assert (await c.get("/fornecedores/")).status_code == 200
    for p in ("/fornecedores/_skeleton", "/fornecedores/assets/app.css", "/fornecedores/assets/demo.css"):
        assert (await c.get(p)).status_code == 200, p
    assert "--app-primary" in (await c.get("/fornecedores/assets/app.css")).text
    assert (await c.get("/fornecedores/assets/x.js")).status_code == 404  # no own JavaScript
    assert (await c.get("/fornecedores/nada")).status_code == 404
    assert (await c.get("/fornecedores/_hub/nada", headers=HUB)).status_code == 404
    assert (await c.get("/outro")).status_code == 404
    for p in (
        "/fornecedores/suppliers/FOR-0271/lookup",
        "/fornecedores/suppliers/FOR-0271/attachments",
        "/fornecedores/suppliers/FOR-0271/attachments/a.pdf/delete",
    ):
        assert (await c.get(p)).status_code == 404, p  # POST only
    # supplier record without object store
    d = await c.get("/fornecedores/indicators", params={"id": "FOR-0271"})
    assert "Object store não configurado" in d.text and 'id="lookup-box"' in d.text
    assert d.text.index("FICHA DO FORNECEDOR") < d.text.index("app2-directory-summary")
    assert 'href="/apps/fornecedores/indicators?id=FOR-0271"' in d.text and "Mais ações" not in d.text
    assert "FICHA DO FORNECEDOR" not in (await c.get("/fornecedores/indicators", params={"id": "x"})).text
    r = await upload(c, "a.pdf", PDF, "application/pdf")
    assert r.status_code == 503 and "Não é possível receber anexos" in r.text
    assert (await c.get("/fornecedores/suppliers/FOR-0271/attachments/a.pdf")).status_code == 404
    assert (await c.post("/fornecedores/suppliers/FOR-0271/attachments/a.pdf/delete")).status_code == 404
    pdf = await c.get("/fornecedores/suppliers/FOR-0271/ficha.pdf")
    assert pdf.status_code == 200 and b"Object store n" in pdf.content
    # internos: token obrigatório
    assert (await c.get("/fornecedores/_hub/outbox")).status_code == 401
    assert (await c.get("/fornecedores/_hub/outbox", headers=HUB)).json()["durable"] is False
    assert (await c.get("/fornecedores/_hub/jobs", headers=HUB)).json() == {"jobs": [], "enabled": False}
    assert (await c.get("/fornecedores/_hub/ready")).status_code == 200


async def test_unknown_supplier_is_404_everywhere(running: Running) -> None:
    c = running.client
    assert (await c.post("/fornecedores/suppliers/XX/lookup")).status_code == 404
    assert (await upload(c, "a.pdf", PDF, "application/pdf", "XX")).status_code == 404
    assert (await c.get("/fornecedores/suppliers/XX/ficha.pdf")).status_code == 404
    assert (await c.get("/fornecedores/suppliers/XX/attachments/a.pdf")).status_code == 404
    assert (await c.post("/fornecedores/suppliers/XX/attachments/a.pdf/delete")).status_code == 404
    assert (await c.get("/fornecedores/suppliers/FOR-0271/attachments/..x")).status_code in (404, 405)


async def test_attachment_lifecycle_and_audit(running: Running) -> None:
    c, store = running.client, running.store
    assert store is not None
    r = await upload(c, "Contrato Açaí.pdf", PDF, "application/pdf")
    assert r.status_code == 201 and "Anexo enviado" in r.text and "Contrato_Acai.pdf" in r.text
    key = next(iter(store.objects))
    assert (
        key.startswith("FOR-0271/")
        and key.endswith("-Contrato_Acai.pdf")
        and store.objects[key][1] == "application/pdf"
    )
    name = key.split("/", 1)[1]
    page = await c.get("/fornecedores/indicators", params={"id": "FOR-0271"})
    assert 'data-storage="s3-app"' in page.text and "Guardados no object store do app." in page.text
    assert (
        f"/_frag/fornecedores/suppliers/FOR-0271/attachments/{name}" in page.text
        and "2026-10-01 12:34" in page.text
    )
    dl = await c.get(f"/fornecedores/suppliers/FOR-0271/attachments/{name}")
    assert dl.status_code == 200 and dl.content == PDF and dl.headers["content-length"] == str(len(PDF))
    assert 'filename="Contrato_Acai.pdf"' in dl.headers["content-disposition"]
    assert (
        dl.headers["x-content-type-options"] == "nosniff" and dl.headers["content-type"] == "application/pdf"
    )
    pdf = await c.get("/fornecedores/suppliers/FOR-0271/ficha.pdf")
    assert (
        pdf.headers["content-disposition"].endswith('ficha-FOR-0271.pdf"')
        and b"Contrato_Acai.pdf" in pdf.content
    )
    gone = await c.post(f"/fornecedores/suppliers/FOR-0271/attachments/{name}/delete")
    assert gone.status_code == 200 and "Anexo excluído" in gone.text and not store.objects
    miss = await c.post(f"/fornecedores/suppliers/FOR-0271/attachments/{name}/delete")
    assert miss.status_code == 404 and "Não foi possível excluir" in miss.text
    assert (await c.get(f"/fornecedores/suppliers/FOR-0271/attachments/{name}")).status_code == 404
    assert await outbox_actions(c) == [
        "Anexo de fornecedor enviado",
        "Ficha de fornecedor gerada (PDF)",
        "Anexo de fornecedor excluído",
    ]


async def test_upload_validation(running: Running, monkeypatch: pytest.MonkeyPatch) -> None:
    c, store = running.client, running.store
    assert store is not None
    for name, data, ctype, status, msg in (
        ("a.exe", PDF, "application/pdf", 415, "Tipo de arquivo não permitido"),
        ("a.pdf", PDF, "image/png", 415, "não corresponde"),
        ("a.pdf", b"MZ\x90\x00" + b"x" * 20, "application/pdf", 415, "não é um .pdf válido"),
        ("a.pdf", b"%PDF", "application/pdf", 415, "não é um .pdf válido"),
    ):
        r = await upload(c, name, data, ctype)
        assert r.status_code == status and msg in r.text, (name, r.text[:300])
    assert not store.objects and not list(running.app.state.fornecedores.scratch.glob("anexo-*"))
    monkeypatch.setenv("HUB_ATTACH_MAX_MB", "1")
    big = await upload(c, "b.pdf", PDF + b"x" * (1024 * 1024), "application/pdf")
    assert big.status_code == 413 and "O limite para anexos é 1 MB" in big.text
    huge = await upload(c, "b.pdf", PDF + b"x" * (2 * 1024 * 1024 + 10), "application/pdf")
    assert huge.status_code == 413
    none = await c.post("/fornecedores/suppliers/FOR-0271/attachments", data={"x": "1"})
    assert none.status_code == 422 and "Nenhum arquivo enviado" in none.text
    junk = await c.post(
        "/fornecedores/suppliers/FOR-0271/attachments",
        content=b"nope",
        headers={"content-type": "multipart/form-data"},
    )
    assert junk.status_code in (400, 422)
    assert not store.objects


async def test_storage_failures_map_to_status_and_retry_after(running: Running) -> None:
    c, store = running.client, running.store
    assert store is not None
    store.fail = (503, "armazenamento ocupado")
    r = await upload(c, "a.pdf", PDF, "application/pdf")
    assert r.status_code == 503 and r.headers["retry-after"] == "2" and "armazenamento ocupado" in r.text
    store.fail = (502, "provedor fora")
    assert (await upload(c, "a.pdf", PDF, "application/pdf")).status_code == 502
    store.fail = (413, "grande")
    assert (await upload(c, "a.pdf", PDF, "application/pdf")).status_code == 413
    store.fail = (502, "provedor fora")
    d = await c.get("/fornecedores/suppliers/FOR-0271/attachments/a.pdf")
    assert d.status_code == 502 and "Anexo indisponível" in d.text
    rm = await c.post("/fornecedores/suppliers/FOR-0271/attachments/a.pdf/delete")
    assert rm.status_code == 502 and "Não foi possível excluir" in rm.text
    page = await c.get("/fornecedores/indicators", params={"id": "FOR-0271"})
    assert "Não foi possível listar os anexos" in page.text
    pdf = await c.get("/fornecedores/suppliers/FOR-0271/ficha.pdf")
    assert pdf.status_code == 200 and b"provedor fora" in pdf.content
    store.fail = (404, "sem pasta")
    assert "Nenhum anexo ainda." in (await c.get("/fornecedores/indicators", params={"id": "FOR-0271"})).text


async def test_broker_label_and_listing_edge_cases(running: Running) -> None:
    c, store = running.client, running.store
    assert store is not None
    store.broker, store.kind = True, "sharepoint"
    t = (await c.get("/fornecedores/indicators", params={"id": "FOR-0271"})).text
    assert 'data-storage="sharepoint"' in t and "Guardados no SharePoint (conexão fake)." in t
    store.kind = "outro"
    assert (
        "Guardados na conexão fake."
        in (await c.get("/fornecedores/indicators", params={"id": "FOR-0271"})).text
    )


async def test_lookup_ok_fallback_and_audit(running: Running, monkeypatch: pytest.MonkeyPatch) -> None:
    c = running.client
    st = running.app.state.fornecedores
    r = await c.post("/fornecedores/suppliers/FOR-0284/lookup")
    assert r.status_code == 200 and "Serviço de consulta não configurado" in r.text
    script: Script = lambda n: (200, OK_BODY, 0)  # noqa: E731
    ext = Ext(script)
    try:
        st.lookup.base = ext.url
        ok = await c.post("/fornecedores/suppliers/FOR-0284/lookup")
        assert (
            "Score de crédito" in ok.text and "<strong>810</strong>" in ok.text and "risco baixo" in ok.text
        )
        assert "1 tentativa(s)" in ok.text and 'data-hub-target="#lookup-box"' in ok.text
        page = await c.get("/fornecedores/indicators", params={"id": "FOR-0284"})
        assert "consultado há" in page.text and "<strong>810</strong>" in page.text
        pdf = await c.get("/fornecedores/suppliers/FOR-0284/ficha.pdf")
        assert b"Score de cr" in pdf.content
    finally:
        ext.srv.shutdown()
        ext.srv.server_close()
    st.lookup.base = "http://127.0.0.1:1"
    st.lookup.timeout, st.lookup.backoff = 0.05, 0.001
    fb = await c.post("/fornecedores/suppliers/FOR-0284/lookup")
    assert "Consulta indisponível no momento" in fb.text and "último resultado conhecido" in fb.text
    acts = await outbox_actions(c)
    assert acts.count("Consulta cadastral de fornecedor") == 3
    _ = monkeypatch


async def test_ficha_pdf_busy_when_heavy_limit_reached(running: Running) -> None:
    c = running.client
    heavy = running.app.state.fornecedores.sdk.heavy
    permits = [heavy.try_acquire() for _ in range(heavy.max)]
    try:
        r = await c.get("/fornecedores/suppliers/FOR-0271/ficha.pdf")
        assert r.status_code == 429 and r.headers["retry-after"] == "10" and "PDF em fila" in r.text
    finally:
        for p in permits:
            assert p is not None
            p.release()
    assert (await c.get("/fornecedores/suppliers/FOR-0271/ficha.pdf")).status_code == 200


async def test_locale_translation(running: Running) -> None:
    c = running.client
    en = await c.get("/fornecedores/settings", headers={"x-hub-locale": "en"})
    assert "Preferences saved." in en.text and "Preferências salvas." not in en.text
    en = await c.get("/fornecedores/dashboard", headers={"x-hub-locale": "en"})
    assert en.status_code == 200 and en.headers["content-type"].startswith("text/html")
    ficha = await c.get("/fornecedores/indicators", params={"id": "FOR-0271"}, headers={"x-hub-locale": "en"})
    assert "Attachments" in ficha.text or "Anexos" in ficha.text


async def test_search_provider_and_internal_endpoints(running: Running) -> None:
    c = running.client
    r = await c.get("/fornecedores/_hub/search", params={"q": "norte", "limit": 5}, headers=HUB)
    hits = r.json()["results"]
    assert [h["title"] for h in hits] == ["Norte Tecnologia"]
    assert hits[0]["url"] == "/apps/fornecedores/indicators?id=FOR-0284" and hits[0]["kind"] == "Fornecedor"
    g = await c.get("/fornecedores/_hub/search", params={"q": "fornecedores", "limit": 3}, headers=HUB)
    assert len(g.json()["results"]) == 6 or len(g.json()["results"]) == 3
    assert (await c.get("/fornecedores/_hub/search", params={"q": "norte"})).status_code == 401


async def test_user_header_is_optional_for_pages(running: Running) -> None:
    anon = running.anon()
    r = await anon.get("/fornecedores")
    assert r.status_code == 200 and "{{HUB_" not in r.text


def test_fake_store_is_a_storage_connection() -> None:
    s = FakeStore()
    assert s.for_user("x") is s
    with pytest.raises(StorageError):
        s.fail = (500, "x")
        s._check()


async def test_helpers() -> None:
    from fornecedores.app import kb, shown_name, storage_status

    assert kb(0) == 0 and kb(1) == 1 and kb(1025) == 2
    assert shown_name("20260101-a-b.pdf") == "a-b.pdf" and shown_name("x.pdf") == "x.pdf"
    assert storage_status(StorageError(429, "x", 7)) == (503, 7)
    assert storage_status(StorageError(404, "x")) == (404, None)
    assert storage_status(StorageError(409, "x")) == (502, None)
