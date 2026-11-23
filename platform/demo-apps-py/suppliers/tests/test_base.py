"""Links and forms follow the hub's public base (``x-hub-base``), with ``/apps/suppliers`` as fallback."""

from __future__ import annotations

from .conftest import PDF, Running

ALT = {"x-hub-base": "/apps/suppliers-alt"}


async def test_fragment_sections_follow_public_base(running: Running) -> None:
    c = running.client
    alt = (await c.get("/suppliers/dashboard", headers=ALT)).text
    assert 'href="/apps/suppliers-alt/analysis"' in alt and "/apps/suppliers/" not in alt
    plain = (await c.get("/suppliers/dashboard")).text
    assert 'href="/apps/suppliers/analysis"' in plain and "{{ base }}" not in plain
    nf = (await c.get("/suppliers/nada", headers=ALT)).text
    assert 'href="/apps/suppliers-alt"' in nf


async def test_supplier_record_follows_public_base(running: Running) -> None:
    c = running.client
    d = (await c.get("/suppliers/indicators", params={"id": "FOR-0271"}, headers=ALT)).text
    assert 'action="/apps/suppliers-alt/suppliers/FOR-0271/lookup"' in d
    assert 'action="/apps/suppliers-alt/suppliers/FOR-0271/attachments"' in d
    assert 'href="/apps/suppliers-alt/indicators?id=FOR-0271"' in d and "/apps/suppliers/" not in d
    files = {"file": ("a.pdf", PDF, "application/pdf")}
    up = (await c.post("/suppliers/suppliers/FOR-0271/attachments", files=files, headers=ALT)).text
    assert 'action="/apps/suppliers-alt/suppliers/FOR-0271/attachments/' in up
    assert "/apps/suppliers/" not in up
    lk = (await c.post("/suppliers/suppliers/FOR-0271/lookup", headers=ALT)).text
    assert 'action="/apps/suppliers-alt/suppliers/FOR-0271/lookup"' in lk
    plain = (await c.get("/suppliers/indicators", params={"id": "FOR-0271"})).text
    assert 'action="/apps/suppliers/suppliers/FOR-0271/lookup"' in plain
