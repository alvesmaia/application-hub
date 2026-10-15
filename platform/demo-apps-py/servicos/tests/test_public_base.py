"""Links and form actions follow the hub's public base (``x-hub-base``), falling back to
``/apps/servicos`` when the header is missing."""

from __future__ import annotations

from datetime import UTC, datetime

from servicos import views
from servicos.model import PAGE
from servicos.routes.tickets import _opened

from .conftest import Running

ALT = {"x-hub-base": "/apps/servicos-alt"}


async def test_fragment_section_follows_the_base(running: Running) -> None:
    c = running.client
    alt = (await c.get("/servicos/dashboard", headers=ALT)).text
    assert 'href="/apps/servicos-alt/requests"' in alt and 'href="/apps/servicos-alt/reports"' in alt
    assert "/apps/servicos/" not in alt and "{{ base }}" not in alt
    plain = (await c.get("/servicos/dashboard")).text
    assert 'href="/apps/servicos/requests"' in plain and "/apps/servicos-alt" not in plain


async def test_view_pages_follow_the_base(running: Running) -> None:
    c = running.client
    rep = (await c.get("/servicos/reports", headers=ALT)).text
    assert 'action="/apps/servicos-alt/reports"' in rep and "/apps/servicos/" not in rep
    form = (await c.post("/servicos/requests", data={"title": "x"}, headers=ALT)).text
    assert 'action="/apps/servicos-alt/requests"' in form and "/apps/servicos/" not in form
    missing = (await c.get("/servicos/nada", headers=ALT)).text
    assert 'href="/apps/servicos-alt"' in missing
    assert 'action="/apps/servicos/reports"' in (await c.get("/servicos/reports")).text


def test_ticket_opened_follows_the_base() -> None:
    due = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    alt = bytes(_opened("SD-2026-1", due, False, "/apps/servicos-alt").body).decode()
    assert 'href="/apps/servicos-alt/requests?protocolo=SD-2026-1"' in alt
    assert 'href="/apps/servicos-alt/requests?new=1"' in alt and "/apps/servicos/" not in alt
    plain = bytes(_opened("SD-2026-1", due, True, "/apps/servicos").body).decode()
    assert 'href="/apps/servicos/requests?new=1"' in plain


def test_ticket_table_follows_the_base() -> None:
    when = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    t: dict[str, object] = {"id": 7, "protocol": "SD-1", "title": "T", "requester": "Ana"}
    t |= {"category": "Software", "priority": "Alta", "status": "Aberto"}
    t |= {"opened_at": when, "sla_due": when, "version": 1}
    out = views.table([t] * PAGE, "?status=Aberto", True, "/apps/servicos-alt")
    assert 'action="/apps/servicos-alt/requests/7"' in out and "/apps/servicos/" not in out
    assert 'href="/apps/servicos-alt/requests?status=Aberto&antes=7"' in out
