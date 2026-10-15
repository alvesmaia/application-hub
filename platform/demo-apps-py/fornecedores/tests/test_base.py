"""Links and forms follow the hub's public base (``x-hub-base``), with ``/apps/fornecedores`` as fallback."""

from __future__ import annotations

from .conftest import PDF, Running

ALT = {"x-hub-base": "/apps/fornecedores-alt"}


async def test_fragment_sections_follow_public_base(running: Running) -> None:
    c = running.client
    alt = (await c.get("/fornecedores/dashboard", headers=ALT)).text
    assert 'href="/apps/fornecedores-alt/analysis"' in alt and "/apps/fornecedores/" not in alt
    plain = (await c.get("/fornecedores/dashboard")).text
    assert 'href="/apps/fornecedores/analysis"' in plain and "{{ base }}" not in plain
    nf = (await c.get("/fornecedores/nada", headers=ALT)).text
    assert 'href="/apps/fornecedores-alt"' in nf


async def test_supplier_record_follows_public_base(running: Running) -> None:
    c = running.client
    d = (await c.get("/fornecedores/indicators", params={"id": "FOR-0271"}, headers=ALT)).text
    assert 'action="/apps/fornecedores-alt/suppliers/FOR-0271/lookup"' in d
    assert 'action="/apps/fornecedores-alt/suppliers/FOR-0271/attachments"' in d
    assert 'href="/apps/fornecedores-alt/indicators?id=FOR-0271"' in d and "/apps/fornecedores/" not in d
    files = {"file": ("a.pdf", PDF, "application/pdf")}
    up = (await c.post("/fornecedores/suppliers/FOR-0271/attachments", files=files, headers=ALT)).text
    assert 'action="/apps/fornecedores-alt/suppliers/FOR-0271/attachments/' in up
    assert "/apps/fornecedores/" not in up
    lk = (await c.post("/fornecedores/suppliers/FOR-0271/lookup", headers=ALT)).text
    assert 'action="/apps/fornecedores-alt/suppliers/FOR-0271/lookup"' in lk
    plain = (await c.get("/fornecedores/indicators", params={"id": "FOR-0271"})).text
    assert 'action="/apps/fornecedores/suppliers/FOR-0271/lookup"' in plain
