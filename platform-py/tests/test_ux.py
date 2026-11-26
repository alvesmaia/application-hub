import json
from pathlib import Path

import pytest
from auth_helper import client_for
from fastapi.testclient import TestClient

from hub.app import create_app
from hub.ux import catalog
from hub.ux.store import UxStore, valid_layout
from hub.ux.texts import ago, t, tn

HDR = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(data_dir: Path) -> TestClient:
    return client_for(create_app())


def test_home_renders_widgets_and_checklist(client: TestClient) -> None:
    h = client.get("/").text
    assert "Olá, Administrador" in h
    assert "aplicações disponíveis para você." in h
    order = [
        'data-widget="onboarding"',
        'data-widget="favorites"',
        'data-widget="recents"',
        'data-widget="status"',
    ]
    assert [h.index(o) for o in order] == sorted(h.index(o) for o in order)
    assert 'data-widget="inbox"' not in h and 'data-widget="shortcuts"' not in h  # the owner removed both
    assert 'data-layout-source="default"' in h
    assert h.count("data-step=") == 6 and 'data-step="search"' not in h  # no search in the hub any more
    assert "0/6 concluídos" in h
    # the notification preferences live in My account (the Inbox has none): the step leads there
    step = h.split('data-step="notifications"')[1].split("</li>")[0]
    assert 'href="/account#prefs"' in step and "/inbox#prefs" not in h
    assert "Fixe aplicações com a estrela no catálogo." in h


def test_home_layout_prefs_roundtrip(client: TestClient) -> None:
    r = client.put("/api/me/home", json={"widgets": ["status", "status", "favorites"]}, headers=HDR)
    assert r.json() == {"ok": True, "widgets": ["status", "favorites"]}
    h = client.get("/").text
    assert 'data-layout-source="user"' in h
    assert h.index('data-widget="status"') < h.index('data-widget="favorites"')
    assert client.get("/api/me/prefs").json()["layout"] == ["status", "favorites"]
    assert client.put("/api/me/home", json={"widgets": ["nope"]}, headers=HDR).status_code == 400
    assert client.put("/api/me/home", json={}, headers=HDR).status_code == 400
    reset = client.delete("/api/me/home", headers=HDR).json()
    assert reset["source"] == "default"
    assert len(reset["widgets"]) == 4


def test_favorites_and_catalog(client: TestClient) -> None:
    assert 'aria-pressed="false"' in client.get("/applications").text
    r = client.put("/api/me/favorites/inventory", json={"on": True}, headers=HDR)
    assert r.json() == {"ok": True, "favorites": ["inventory"]}
    page = client.get("/applications").text
    assert 'class="icon-button hub-star on"' in page
    assert "Tirar Gestão de Ativos dos favoritos" in page
    assert 'class="hub-fav" href="/apps/inventory"' in client.get("/").text
    assert client.put("/api/me/favorites/nope", json={"on": True}, headers=HDR).status_code == 403
    assert (
        client.put("/api/me/favorites/inventory", json={"on": False}, headers=HDR).json()["favorites"] == []
    )
    assert client.put("/api/me/favorites/inventory", json={}, headers=HDR).status_code == 400


def test_catalog_cards_show_stack_and_deployed_version(data_dir: Path) -> None:
    (data_dir / "deployments.json").write_text(json.dumps({"inventory": "9.9.9"}))
    page = client_for(create_app()).get("/applications").text
    assert "Versão implantada" in page
    assert 'class="app-version" title="Versão 9.9.9">v9.9.9<' in page
    assert 'class="hub-stack-chip"' in page
    assert "API ligada · MCP ligado" in page


def test_prefs_validation(client: TestClient) -> None:
    assert client.put("/api/me/prefs", json={"theme": "neon"}, headers=HDR).status_code == 400
    assert client.put("/api/me/prefs", json={"lang": "xx"}, headers=HDR).status_code == 400
    assert client.put("/api/me/prefs", json={"theme": "dark"}, headers=HDR).json() == {"ok": True}
    body = client.get("/api/me/prefs").json()
    assert body["theme"] == "dark"
    assert "theme" in body["steps"]
    assert set(body["channels"]) == {"alert", "notification", "approval"}
    assert client.post("/api/me/onboarding/step", json={"key": "search"}, headers=HDR).status_code in (
        404,
        405,
    )
    assert client.put("/api/me/onboarding", json={"tour": "step:3"}, headers=HDR).status_code == 200
    assert client.put("/api/me/onboarding", json={"tour": "xyz"}, headers=HDR).status_code == 400
    assert client.post("/api/me/analytics", json={"feature": "theme_toggle"}, headers=HDR).status_code == 204
    assert client.get("/").text.count('class="done"') == 1  # theme
    # saving the notification channels (My account › Preferences) completes the "notifications" step
    assert (
        client.put("/api/me/prefs", json={"channels": {"alert": {"in_app": True}}}, headers=HDR).status_code
        == 200
    )
    assert "notifications" in client.get("/api/me/prefs").json()["steps"]


def test_app_visit_feeds_recents(client: TestClient) -> None:
    client.get("/apps/inventory/assets")
    client.get("/apps/inventory/assets")  # repeated: a single item
    client.get("/apps/inventory")
    prefs = client.get("/api/me/prefs").json()
    assert [r["path"] for r in prefs["recents"]] == ["/apps/inventory", "/apps/inventory/assets"]
    home = client.get("/").text
    assert "Gestão de Ativos · Inventário de ativos" in home
    assert "agora" in home


def test_status_page(client: TestClient) -> None:
    h = client.get("/status").text
    assert "Todos os sistemas operacionais" in h
    n = h.count('class="status-app"')
    assert n >= 3
    assert h.count("uptime-bar uptime-none") == 30 * n
    assert "Nenhum incidente registrado." in h
    assert "data-incident-new" in h


def test_status_public_only_when_enabled(data_dir: Path) -> None:
    anon = create_app()
    from fastapi.testclient import TestClient as TC

    c = TC(anon, follow_redirects=False)
    assert c.get("/status").status_code == 303
    (data_dir / "admin.json").write_text(
        json.dumps({"settings": {"status_public": True, "status_public_apps": ["inventory"]}})
    )
    r = c.get("/status")
    assert r.status_code == 200
    assert 'class="status-public"' in r.text.replace("rail-hidden ", "")
    assert r.text.count('class="status-app"') == 1


def test_store_persistence_and_limits(tmp_path: Path) -> None:
    s = UxStore(tmp_path / "ux.json")
    for i in range(12):
        s.visit("u", f"/apps/x{i}", f"X{i}", i)
    s.visit("u", "/apps/x11", "again", 99)
    assert len(s.prefs("u").recents) == 8
    assert s.prefs("u").recents[0].path == "/apps/x11"
    for i in range(30):
        s.toggle_favorite("u", f"a{i}", True)
    assert len(s.prefs("u").favorites) == 24
    again = UxStore(tmp_path / "ux.json")
    assert again.prefs("nobody").favorites == []
    assert again.layout_for("u", ["User"])[1] == "default"
    again.set_role_layout("User", ["status"])
    assert UxStore(tmp_path / "ux.json").layout_for("u", ["User"]) == (["status"], "role")
    with pytest.raises(ValueError, match="Widget desconhecido"):
        valid_layout(["x"])
    assert UxStore(tmp_path / "missing" / "ux.json").prefs("u").tour == ""


def test_catalog_helpers_and_texts() -> None:
    assert catalog.recent_path("/apps/a/dashboard") == "/apps/a"
    assert catalog.recent_path("/apps/a/e2e-x") is None
    assert catalog.recent_path("/a/assets?q=1") == "/apps/a/assets"
    assert catalog.section_label("my-new_page") == "My new page"
    assert catalog.section_label("assets") == "Inventário de ativos"
    assert t("nao.existe") == "nao.existe"
    assert t("time.now", "en") == "just now"
    assert tn("home.greeting", "Ana", "es") == "Olá, Ana"
    assert ago(0, 30_000) == "agora"
    assert ago(0, 120_000) == "há 2 min"
    assert ago(0, 7_200_000) == "há 2 h"
    assert ago(0, 3 * 86_400_000) == "há 3 dias"


def test_two_replicas_sharing_ux_json_do_not_lose_writes(tmp_path: Path) -> None:
    """Two replicas on the same volume: each write rereads the file under lock; the read sees what the other one wrote."""
    a, b = UxStore(tmp_path / "ux.json"), UxStore(tmp_path / "ux.json")
    a.update("u1", theme="dark")
    b.update("u2", theme="light")  # b still had the empty version in memory
    b.toggle_favorite("u1", "inventory", True)
    assert UxStore(tmp_path / "ux.json").prefs("u1").theme == "dark"
    assert UxStore(tmp_path / "ux.json").prefs("u1").favorites == ["inventory"]
    assert a.prefs("u2").theme == "light" and a.prefs("u1").favorites == ["inventory"]


def test_catalog_stack_button_only_for_admins(data_dir: Path) -> None:
    # the stack (framework, database, libraries) is technical detail for admins; others keep the version on the card
    app = create_app()
    admin = client_for(app)
    assert 'class="hub-stack-toggle"' in admin.get("/applications").text
    auth = app.state.auth.path
    auth.write_text(auth.read_text(encoding="utf-8").replace('"Administrador"]', '"User"]'), encoding="utf-8")
    page = admin.get("/applications").text
    assert 'data-app="inventory"' in page and 'class="app-version"' in page
    assert "hub-stack-toggle" not in page and "hub-stack-chip" not in page


def test_a_saved_layout_with_removed_widgets_still_loads_and_saves(tmp_path: Path) -> None:
    store = UxStore(tmp_path / "ux.json")
    store.set_role_layout("User", ["status"])
    raw = json.loads((tmp_path / "ux.json").read_text(encoding="utf-8"))
    raw["role_layouts"]["User"] = ["inbox", "status", "shortcuts"]  # saved before the removal
    (tmp_path / "ux.json").write_text(json.dumps(raw), encoding="utf-8")
    layout, _ = UxStore(tmp_path / "ux.json").layout_for("u", ["User"])
    assert layout == ["status"] and valid_layout(layout) == ["status"]
