"""Server-side pagination (`hub.ux.pager`) of the Containers, Backups and Users tables."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from admin_helper import add_user, make_hub
from fastapi.testclient import TestClient
from test_ops import FakeDocker, upstream


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with make_hub(tmp_path, monkeypatch, upstream) as c:
        yield c


def test_runtime_paginates_containers_and_keeps_query(hub: TestClient) -> None:
    fake = FakeDocker()
    fake.names += [f"app-hub-plat{i:02d}" for i in range(30)]  # platform containers: the list that grows
    hub.app.state.ops_engine.factory = lambda: fake  # type: ignore[attr-defined]
    page = hub.get("/admin/runtime?ordem=x&per=10&page=2").text
    assert 'class="hub-pager"' in page and "Página 2 de " in page
    assert page.count('class="ops-ct-row"') == 10 and page.count('class="ops-detail"') == 10
    assert "ordem=x&amp;page=3&amp;per=10" in page


def test_runtime_without_many_containers_has_no_pager(hub: TestClient) -> None:
    hub.app.state.ops_engine.factory = FakeDocker  # type: ignore[attr-defined]
    assert 'class="hub-pager"' not in hub.get("/admin/runtime").text


def test_backups_paginates_history(hub: TestClient) -> None:
    store = hub.app.state.ops  # type: ignore[attr-defined]
    d = store.read()
    d["backups"] = [
        {"id": i, "app": "inventory", "kind": "Backup", "size": 0, "status": "Falhou", "at": 1_700_000_000_000 + i,
         "detail": f"falha-{i:02d}"}
        for i in range(30)
    ]  # fmt: skip
    store.write(d)
    page = hub.get("/admin/backups?per=10&page=3").text
    assert 'class="hub-pager"' in page and "21–30 de 30" in page
    assert "falha-20" in page and "falha-29" in page and "falha-19" not in page


def test_users_paginates_above_ten(hub: TestClient) -> None:
    assert 'class="hub-pager"' not in hub.get("/admin/users").text  # only the seed admin: no pager
    for i in range(10):
        add_user(hub, f"pg{i:02d}", ["Colaborador"])
    page = hub.get("/admin/users?per=10&page=2").text
    assert 'class="hub-pager"' in page and "11–11 de 11" in page and page.count('data-user-id="') == 1


def test_audit_shows_10_per_page_with_the_size_selector_last(hub: TestClient) -> None:
    for i in range(15):
        assert (
            hub.post(
                "/api/admin/env", json={"name": f"V{i:02d}", "value": "1", "kind": "publica", "bindings": []}
            ).status_code
            == 201
        )
    page = hub.get("/admin/audit").text
    assert 'class="hub-pager"' in page and "Página 1 de " in page
    assert '<option value="10" selected>' in page  # the audit's default
    pager = page.split('class="hub-pager"', 1)[1].split("</nav>", 1)[0]
    # previous/next in the middle, "items per page" on the right
    assert pager.index("hub-pager-nav") < pager.index("hub-pager-per")


def test_every_paged_table_defaults_to_10_per_page() -> None:
    from starlette.requests import Request

    from hub.ux.pager import paginate

    rows, info = paginate(Request({"type": "http", "query_string": b""}), list(range(30)))
    assert info["per"] == 10 and rows == list(range(10)) and info["pages"] == 3
