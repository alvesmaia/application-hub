"""Contract status from the due date at render time: the mockup's stored text ("Vigente") goes
stale as days pass, so the page derives it from ``VENCIMENTO`` on every view."""

from __future__ import annotations

import datetime as dt
import re

from hub_sdk import locale

MONTHS = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
RENEWAL_DAYS = 30
_ROW = re.compile(
    r'(<strong>(\d{2}) ([a-z]{3}) (\d{4})</strong>.*?)<span class="app2-status[^"]*">[^<]*</span>', re.S
)


def today() -> dt.date:
    return dt.datetime.now(locale.zone()).date()


def contract_status(due: dt.date, now: dt.date) -> tuple[str, str]:
    """(label, CSS modifier): past → expired, within 30 days → renewal, else active."""
    if due < now:
        return "Vencido", "docs"
    if (due - now).days <= RENEWAL_DAYS:
        return "Renovação próxima", "review"
    return "Vigente", ""


def _replace(m: re.Match[str], now: dt.date) -> str:
    if m.group(3) not in MONTHS:
        return m.group(0)
    due = dt.date(int(m.group(4)), MONTHS.index(m.group(3)) + 1, int(m.group(2)))
    label, css = contract_status(due, now)
    return f'{m.group(1)}<span class="app2-status {css}">{label}</span>'


def restatus(page: str, now: dt.date) -> str:
    """Rewrites the status of each contract row (``dd mmm yyyy`` due date, then its status pill)."""
    return _ROW.sub(lambda m: _replace(m, now), page)
