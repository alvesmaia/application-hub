"""Contract status derived from the due date at render time (never the stored text)."""

from __future__ import annotations

import datetime as dt

from suppliers.contracts import contract_status, restatus, today

from .conftest import Running

TODAY = dt.date(2026, 11, 24)


def test_status_from_the_due_date() -> None:
    assert contract_status(dt.date(2026, 10, 18), TODAY) == ("Vencido", "docs")
    assert contract_status(TODAY, TODAY) == ("Renovação próxima", "review")
    assert contract_status(dt.date(2026, 12, 24), TODAY) == ("Renovação próxima", "review")
    assert contract_status(dt.date(2026, 12, 25), TODAY) == ("Vigente", "")


def row(due: str, status: str) -> str:
    return (
        '<article class="app2-contract-row"><div class="app2-contract-due"><small>VENCIMENTO</small>'
        f'<strong>{due}</strong></div><div class="app2-contract-value"><strong>R$ 1</strong></div>'
        f'<span class="app2-status ">{status}</span></article>'
    )


def test_restatus_rewrites_each_row_status() -> None:
    page = row("18 out 2026", "Vigente") + row("14 jan 2027", "Vigente") + row("07 dez 2026", "Vigente")
    out = restatus(page, TODAY)
    assert '<span class="app2-status docs">Vencido</span>' in out
    assert '<span class="app2-status ">Vigente</span>' in out
    assert '<span class="app2-status review">Renovação próxima</span>' in out
    assert restatus("<p>18 xyz 2026</p>", TODAY) == "<p>18 xyz 2026</p>"


async def test_reports_page_shows_the_computed_status(running: Running) -> None:
    r = await running.client.get("/suppliers/reports")
    art = r.text.split("CTR-2026-041")[1].split("</article>")[0]
    assert f">{contract_status(dt.date(2026, 10, 18), today())[0]}</span>" in art
