"""Administration › Variables page: the "Application" column and its server-side multi-select filter."""

from __future__ import annotations

from pathlib import Path

import pytest
from admin_helper import make_hub
from fastapi.testclient import TestClient

from hub.ux.texts import tr

SECRET = "sk_live_filtro-secreto-987654"


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    c = make_hub(tmp_path, monkeypatch)
    for name, kind, value, apps in (
        ("SO_INVENTORY", "publica", "1", ["inventory"]),
        ("SO_COLLECTOR", "publica", "1", ["collector"]),
        ("SEM_VINCULO", "publica", "1", []),
        ("SEGREDO_INVENTORY", "secreta", SECRET, ["inventory"]),
    ):
        binds = [{"app": a, "env": "producao"} for a in apps]
        body = {"name": name, "value": value, "kind": kind, "bindings": binds}
        assert c.post("/api/admin/env", json=body).status_code == 201
    return c


def shown(html: str, name: str) -> bool:
    return f">{name}</code>" in html


def test_per_application_section_is_gone(hub: TestClient) -> None:
    html = hub.get("/admin/env").text
    assert "Por aplicação" not in html and "data-env-app=" not in html and "ops-cards" not in html
    assert "data-ops-provision" not in html and "data-multi=" not in html


def test_application_column_lists_bound_apps(hub: TestClient) -> None:
    html = hub.get("/admin/env").text
    assert "<th" in html and 'class="env-app-col"' in html and 'name="app"' in html
    row = html.split(">SO_INVENTORY</code>", 1)[1].split("</tr>", 1)[0]
    assert 'data-env-app-chip="inventory"' in row and 'data-env-app-chip="collector"' not in row
    unbound = html.split(">SEM_VINCULO</code>", 1)[1].split("</tr>", 1)[0]
    assert "data-env-unbound" in unbound and "Nenhuma aplicação" in unbound


def test_the_filter_sits_in_the_toolbar_and_the_header_is_plain_text(hub: TestClient) -> None:
    # in the column header it took the header's capitals and looked like a label, not a control
    html = hub.get("/admin/env").text
    toolbar = html.split('class="table-toolbar"', 1)[1].split("</div>", 1)[0]
    assert 'class="env-app-filter"' in toolbar and 'name="app"' in html
    head = html.split("<thead>", 1)[1].split("</thead>", 1)[0]
    assert "env-app-filter" not in head and ">Aplicação</th>" in head


def test_app_chips_wrap_instead_of_being_cut() -> None:
    css = (Path(__file__).resolve().parents[2] / "platform/assets/web/ops.css").read_text(encoding="utf-8")
    rules = [r for r in css.split("}") if ".env-table .env-chips{" in r]
    assert rules and "flex-wrap:wrap" in rules[-1] and "overflow:hidden" not in rules[-1]


def test_filter_by_one_app(hub: TestClient) -> None:
    html = hub.get("/admin/env?app=inventory").text
    assert shown(html, "SO_INVENTORY") and shown(html, "SEGREDO_INVENTORY")
    assert not shown(html, "SO_COLLECTOR") and not shown(html, "SEM_VINCULO")
    assert 'value="inventory" checked' in html and 'value="collector" checked' not in html


def test_filter_by_several_apps(hub: TestClient) -> None:
    html = hub.get("/admin/env?app=inventory&app=collector").text
    assert shown(html, "SO_INVENTORY") and shown(html, "SO_COLLECTOR") and not shown(html, "SEM_VINCULO")


def test_no_filter_shows_everything_including_unbound(hub: TestClient) -> None:
    html = hub.get("/admin/env").text
    assert all(shown(html, n) for n in ("SO_INVENTORY", "SO_COLLECTOR", "SEM_VINCULO", "SEGREDO_INVENTORY"))


def test_unknown_app_is_ignored(hub: TestClient) -> None:
    only_bad = hub.get("/admin/env?app=desconhecido").text
    assert shown(only_bad, "SEM_VINCULO") and shown(only_bad, "SO_COLLECTOR")  # same as no filter
    assert "desconhecido" not in only_bad
    mixed = hub.get("/admin/env?app=desconhecido&app=collector").text
    assert shown(mixed, "SO_COLLECTOR") and not shown(mixed, "SO_INVENTORY")


def test_new_texts_are_translated(hub: TestClient) -> None:
    en = hub.get("/admin/env", headers={"accept-language": "en"}).text
    assert "No application" in en and "Filter by application" in en and "Nenhuma aplicação" not in en
    assert (
        tr("Nenhuma variável para as aplicações escolhidas.", "en")
        == "No variables for the chosen applications."
    )
    assert tr("Nenhuma variável cadastrada.", "en") == "No variables yet."


def test_secret_value_never_rendered(hub: TestClient) -> None:
    for url in ("/admin/env", "/admin/env?app=inventory", "/admin/env?app=inventory&app=collector"):
        assert SECRET not in hub.get(url).text
