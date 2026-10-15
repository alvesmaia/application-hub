"""Inbox page: the filter is a labeled `<select>` (GET `?filter=`), not a row of chips."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_inbox import approval, make_client

from hub.ux.inbox import item_of


def _notices(client: TestClient, n: int) -> None:
    for i in range(n):
        item = item_of(
            "notification", "servicos", f"Aviso N{i:02d}", source=f"s:n{i}", to_roles=["Administrador"]
        )
        client.app.state.inbox.push(item)  # type: ignore[attr-defined]


def _list(html: str) -> str:
    return html.split('class="panel hub-inbox"')[1].split("</section>")[0]


def _selected(html: str) -> list[str]:
    return [str(v) for v in re.findall(r'<option value="(\w+)" selected', html)]


def test_filter_select_renders_with_label_and_current_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    html = make_client(tmp_path, monkeypatch).get("/inbox?filter=approvals").text
    assert '<label for="inbox-filter">' in html
    assert '<select id="inbox-filter" name="filter" data-hub-autosubmit="">' in html
    assert '<option value="all">Todas</option>' in html
    assert _selected(html) == ["approvals"]
    assert "<noscript>" in html and 'method="get" action="/inbox"' in html
    assert "filter-chip" not in html and 'href="/inbox?filter=' not in html


def test_filter_select_defaults_to_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _selected(make_client(tmp_path, monkeypatch).get("/inbox").text) == ["all"]


def test_filter_param_filters_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = make_client(tmp_path, monkeypatch)
    approval(c)
    _notices(c, 1)
    # Only the list section: the shell's bell also shows recent items on every page.
    shown = {
        f: _list(c.get(f"/inbox?filter={f}").text) for f in ("all", "approvals", "notifications", "alerts")
    }
    assert "Aprovar chamado SD-1" in shown["all"] and "Aviso N00" in shown["all"]
    assert "Aprovar chamado SD-1" in shown["approvals"] and "Aviso N00" not in shown["approvals"]
    assert "Aviso N00" in shown["notifications"] and "Aprovar chamado SD-1" not in shown["notifications"]
    assert "Nenhum item neste filtro." in shown["alerts"]


def test_unknown_filter_selects_nothing_and_lists_like_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Deliberate: an unknown value keeps the old fallback (non-archived items), no 422.
    c = make_client(tmp_path, monkeypatch)
    approval(c)
    html = c.get("/inbox?filter=bogus").text
    assert _selected(html) == [] and "Aprovar chamado SD-1" in html


def test_inbox_list_is_paginated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = make_client(tmp_path, monkeypatch)
    _notices(c, 12)
    first = c.get("/inbox?per=10").text
    assert 'class="hub-pager"' in first and _list(first).count('class="hub-inbox-item') == 10
    second = _list(c.get("/inbox?per=10&page=2").text)
    assert second.count('class="hub-inbox-item') == 2


def test_pager_keeps_the_filter_and_filter_form_keeps_per(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_client(tmp_path, monkeypatch)
    _notices(c, 12)
    approval(c)
    html = c.get("/inbox?filter=notifications&per=10&page=2").text
    assert _list(html).count('class="hub-inbox-item') == 2 and "Aprovar chamado SD-1" not in _list(html)
    assert 'href="?filter=notifications&amp;page=1&amp;per=10"' in html
    assert '<input type="hidden" name="filter" value="notifications"/>' in html  # per selector keeps it
    filter_form = html.split('action="/inbox"')[1].split("</form>")[0]
    assert '<input type="hidden" name="per" value="10"/>' in filter_form and 'name="page"' not in filter_form
