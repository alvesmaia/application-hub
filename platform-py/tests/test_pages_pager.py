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
    page = hub.get("/admin/runtime?runtime=podman&per=10&page=2").text
    assert 'class="hub-pager"' in page and "Página 2 de " in page
    assert page.count('class="ops-ct-row"') == 10 and page.count('class="ops-detail"') == 10
    assert "runtime=podman&amp;page=3&amp;per=10" in page and "/run/podman/podman.sock" in page


def test_runtime_without_many_containers_has_no_pager(hub: TestClient) -> None:
    hub.app.state.ops_engine.factory = FakeDocker  # type: ignore[attr-defined]
    assert 'class="hub-pager"' not in hub.get("/admin/runtime").text


def test_backups_paginates_history(hub: TestClient) -> None:
    store = hub.app.state.ops  # type: ignore[attr-defined]
    d = store.read()
    d["backups"] = [
        {"id": i, "app": "ativos", "kind": "Backup", "size": 0, "status": "Falhou", "at": 1_700_000_000_000 + i,
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
