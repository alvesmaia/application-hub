"""Unidades: tipos de anexo, nomes, PDF, consulta cadastral (retry, disjuntor, timeout), busca."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from hub_sdk.search import SearchQuery
from hub_sdk.storage import Item
from typing_extensions import override

from fornecedores import lookup as lk
from fornecedores.app import generic_term, s3_config, search_hits
from fornecedores.ficha import ficha_pdf
from fornecedores.pdf import Pdf, pdf_string
from fornecedores.suppliers import (
    SUPPLIERS,
    attach_max_bytes,
    check_type,
    safe_name,
    supplier,
    valid_object_name,
)

OK_BODY = json.dumps(
    {
        "cnpj": "12345678000190",
        "razao_social": "X",
        "situacao": "ATIVA",
        "score": 810,
        "risco": "baixo",
        "consultado_em": "2026-10-01T10:00:00Z",
    }
).encode()
Script = Callable[[int], tuple[int, bytes, float]]


def test_type_check_uses_extension_declared_type_and_magic() -> None:
    assert check_type("a.pdf", "application/pdf", b"%PDF-1.7\n....") == "application/pdf"
    assert check_type("a.PNG", "", b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert check_type("a.jpg", "application/octet-stream", b"\xff\xd8\xff\xe0") == "image/jpeg"
    for name, declared, head in (
        ("a.exe", "application/pdf", b"%PDF-"),
        ("a.pdf", "image/png", b"%PDF-"),
        ("a.pdf", "application/pdf", b"MZ\x90\x00"),
        ("a.webp", "image/webp", b"RIFF1234WAVE"),
    ):
        with pytest.raises(ValueError):
            check_type(name, declared, head)
    assert check_type("a.webp", "image/webp", b"RIFF1234WEBP") == "image/webp"


def test_names_are_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    assert safe_name("../../Contrato Açaí (v2).pdf") == "Contrato_Acai__v2_.pdf"
    assert safe_name("...") == "arquivo"
    assert valid_object_name("20260926-contrato.pdf")
    assert not valid_object_name("../x") and not valid_object_name(".hidden") and not valid_object_name("")
    assert supplier("FOR-0271") is SUPPLIERS[2] and supplier("x") is None
    monkeypatch.setenv("HUB_ATTACH_MAX_MB", "3")
    assert attach_max_bytes() == 3 * 1024 * 1024
    monkeypatch.setenv("HUB_ATTACH_MAX_MB", "x")
    assert attach_max_bytes() == 20 * 1024 * 1024


def test_pdf_is_valid_looking_and_escapes() -> None:
    p = Pdf()
    p.rect(0, 0, 595, 60, (0.1, 0.35, 0.51))
    p.text(40, 40, 18, True, (1, 1, 1), "Ficha (São Paulo) \\ ação")
    p.new_page()
    p.text(40, 40, 10, False, (0, 0, 0), "página 2 — 😀")
    b = p.finish()
    s = b.decode("latin-1")
    assert b.startswith(b"%PDF-1.4") and "/Count 2" in s and s.rstrip().endswith("%%EOF")
    assert pdf_string("(São)") == "(\\(S\\343o\\))" and "\\227 ?" in s
    xref = int(s.rsplit("startxref\n", 1)[1].splitlines()[0])
    assert b[xref:].startswith(b"xref")


def test_ficha_pdf_variants() -> None:
    b = ficha_pdf(SUPPLIERS[2], "ana", None, [], "sem object store")
    assert b.startswith(b"%PDF-1.4") and b"Casa & Forma" in b and b"sem object store" in b
    info = lk.CnpjInfo("1", "Razão", "ATIVA", 700, "médio", "2026-10-01T10:00:00Z")
    items = [Item(path=f"a/{i}", name=f"2026-doc{i}.pdf", size=2048) for i in range(60)]
    items.append(Item(path="a/dir", name="dir", folder=True))
    big = ficha_pdf(SUPPLIERS[0], "ana", (info, 5), items, None)
    assert big.count(b"/Type /Page ") >= 2 and b"doc0.pdf" in big and b"Score de cr" in big
    assert b"Nenhum anexo." in ficha_pdf(SUPPLIERS[0], "ana", None, [], None)


def sq(q: str, loc: str = "pt-BR", limit: int = 10) -> SearchQuery:
    return SearchQuery(q=q, limit=limit, identity=None, locale=loc)


def test_search_hits_and_generic_terms() -> None:
    assert [h.title for h in search_hits(sq("norte"))] == ["Norte Tecnologia"]
    assert search_hits(sq("FOR-0271"))[0].url == "/apps/fornecedores/indicators?id=FOR-0271"
    assert len(search_hits(sq("fornecedores"))) == 6 and generic_term("Proveedores")
    assert not generic_term("for") and search_hits(sq("zzzz")) == []
    assert search_hits(sq("norte", "en"))[0].kind == "Supplier"
    assert search_hits(sq("norte", "es"))[0].kind == "Proveedor"
    assert len(search_hits(sq("são paulo"))) == 2  # cidade (peso 0,6)


def test_s3_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("HUB_S3_ENDPOINT", "HUB_S3_BUCKET", "HUB_S3_ACCESS_KEY", "HUB_S3_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert s3_config() is None
    for k, v in (
        ("HUB_S3_ENDPOINT", "http://minio:9000"),
        ("HUB_S3_BUCKET", "fornecedores"),
        ("HUB_S3_ACCESS_KEY", "k"),
        ("HUB_S3_SECRET_KEY", "s"),
    ):
        monkeypatch.setenv(k, v)
    cfg = s3_config()
    assert cfg is not None and cfg.region == "us-east-1" and cfg.bucket == "fornecedores"


class Ext:
    """Serviço externo falso: ``script`` decide (status, corpo, atraso) por chamada."""

    def __init__(self, script: Script) -> None:
        self.hits = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.hits += 1
                status, body, delay = script(outer.hits)
                time.sleep(delay)
                try:
                    self.send_response(status)
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            @override
            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


@pytest.fixture
def ext_factory() -> Iterator[Callable[[Script], Ext]]:
    made: list[Ext] = []

    def make(script: Script) -> Ext:
        e = Ext(script)
        made.append(e)
        return e

    yield make
    for e in made:
        e.srv.shutdown()
        e.srv.server_close()


async def test_retries_transient_failures_then_succeeds(ext_factory: Callable[[Script], Ext]) -> None:
    ext = ext_factory(lambda n: (503, b"", 0) if n < 3 else (200, OK_BODY, 0))
    lo = lk.Lookup(ext.url, 0.5, 0.005)
    o = await lo.lookup("12.345.678/0001-90")
    assert o.info is not None and o.attempts == 3 and o.info.score == 810
    assert lo.last_good("12345678000190") is not None and lo.open_for() == 0


async def test_timeouts_open_the_breaker_and_recover(ext_factory: Callable[[Script], Ext]) -> None:
    ext = ext_factory(lambda n: (200, OK_BODY, 0.4))
    lo = lk.Lookup(ext.url, 0.05, 0.001)
    for _ in range(lk.OPEN_AFTER):
        o = await lo.lookup("12345678000190")
        assert o.info is None and o.attempts == 3 and "sem resposta" in o.reason
    before = ext.hits
    o = await lo.lookup("12345678000190")
    assert o.attempts == 0 and "suspensas" in o.reason and ext.hits == before and lo.open_for() > 0
    lo.open_until = time.monotonic() - 1  # meio-aberto: a consulta de teste decide
    lo.timeout = 2.0
    ok = await lo.lookup("12345678000190")
    assert ok.info is not None and lo.failures == 0


async def test_client_errors_connection_errors_and_bad_bodies(ext_factory: Callable[[Script], Ext]) -> None:
    ext = ext_factory(lambda n: (400, b"{}", 0))
    o = await lk.Lookup(ext.url, 0.5, 0.001).lookup("1")
    assert o.attempts == 1 and "recusada (400)" in o.reason and ext.hits == 1
    bad = ext_factory(lambda n: (200, b"{nope", 0))
    assert "resposta inválida" in (await lk.Lookup(bad.url, 0.5).lookup("1")).reason
    r = await lk.Lookup("http://127.0.0.1:1", 0.2, 0.001).lookup("1")
    assert r.attempts == 3 and (
        "falha de conexão" in r.reason or "sem resposta" in r.reason
    )  # Windows: SYN sem RST
    none = await lk.Lookup(None).lookup("1")
    assert none.attempts == 0 and "não configurado" in none.reason


def test_lookup_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_EXTAPI_URL", "http://x:1/")
    monkeypatch.setenv("HUB_EXTAPI_TIMEOUT_MS", "250")
    lo = lk.Lookup.from_env("fornecedores")
    assert lo.base == "http://x:1" and lo.timeout == 0.25
    monkeypatch.setenv("HUB_EXTAPI_TIMEOUT_MS", "abc")
    assert lk.Lookup.from_env("fornecedores").timeout == 1.5
