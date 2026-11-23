"""Links and form actions follow the hub's public base (``x-hub-base``), falling back to
``/apps/servicedesk`` when the header is missing."""

from __future__ import annotations

from datetime import UTC, datetime

from servicedesk import views
from servicedesk.model import PAGE
from servicedesk.routes.tickets import _opened

from .conftest import Running

ALT = {"x-hub-base": "/apps/servicedesk-alt"}


async def test_fragment_section_follows_the_base(running: Running) -> None:
    c = running.client
    alt = (await c.get("/servicedesk/dashboard", headers=ALT)).text
    assert 'href="/apps/servicedesk-alt/requests"' in alt and 'href="/apps/servicedesk-alt/reports"' in alt
    assert "/apps/servicedesk/" not in alt and "{{ base }}" not in alt
    plain = (await c.get("/servicedesk/dashboard")).text
    assert 'href="/apps/servicedesk/requests"' in plain and "/apps/servicedesk-alt" not in plain


async def test_view_pages_follow_the_base(running: Running) -> None:
    c = running.client
    rep = (await c.get("/servicedesk/reports", headers=ALT)).text
    assert 'action="/apps/servicedesk-alt/reports"' in rep and "/apps/servicedesk/" not in rep
    form = (await c.post("/servicedesk/requests", data={"title": "x"}, headers=ALT)).text
    assert 'action="/apps/servicedesk-alt/requests"' in form and "/apps/servicedesk/" not in form
    missing = (await c.get("/servicedesk/nada", headers=ALT)).text
    assert 'href="/apps/servicedesk-alt"' in missing
    assert 'action="/apps/servicedesk/reports"' in (await c.get("/servicedesk/reports")).text


def test_ticket_opened_follows_the_base() -> None:
    due = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    alt = bytes(_opened("SD-2026-1", due, False, "/apps/servicedesk-alt").body).decode()
    assert 'href="/apps/servicedesk-alt/requests?protocolo=SD-2026-1"' in alt
    assert 'href="/apps/servicedesk-alt/requests?new=1"' in alt and "/apps/servicedesk/" not in alt
    plain = bytes(_opened("SD-2026-1", due, True, "/apps/servicedesk").body).decode()
    assert 'href="/apps/servicedesk/requests?new=1"' in plain


def test_ticket_table_follows_the_base() -> None:
    when = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    t: dict[str, object] = {"id": 7, "protocol": "SD-1", "title": "T", "requester": "Ana"}
    t |= {"category": "Software", "priority": "Alta", "status": "Aberto"}
    t |= {"opened_at": when, "sla_due": when, "version": 1}
    out = views.table([t] * PAGE, "?status=Aberto", True, "/apps/servicedesk-alt")
    assert 'action="/apps/servicedesk-alt/requests/7"' in out and "/apps/servicedesk/" not in out
    assert 'href="/apps/servicedesk-alt/requests?status=Aberto&antes=7"' in out
