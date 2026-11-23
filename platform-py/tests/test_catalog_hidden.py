"""Apps hidden from the catalog (Settings): only those with access to the app still see it; the others no longer
find it under "Other applications"."""

from pathlib import Path

import pytest
from admin_helper import add_user, make_hub
from fastapi.testclient import TestClient

BASE = {"platform_name": "", "brand_color": "", "status_public": False, "status_public_apps": [], "language": "pt-BR",
        "timezone": "America/Sao_Paulo"}  # fmt: skip


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return make_hub(tmp_path, monkeypatch)


def test_a_hidden_app_shows_only_to_users_with_access(hub: TestClient) -> None:
    hub.post("/api/admin/roles", json={"name": "Só Ativos", "level": "Padrão"})
    hub.put("/api/admin/roles/Só Ativos/permissions/0", json={"granted": True})
    hub.put("/api/admin/roles/Só Ativos/apps", json={"apps": ["inventory"]})
    ana = add_user(hub, "ana", ["Só Ativos"])
    assert 'data-app="suppliers"' in ana.get("/applications").text  # under "Other applications"
    r = hub.put("/api/admin/settings", json=BASE | {"catalog_hidden_apps": ["suppliers", "inventory"]})
    assert r.status_code == 200 and r.json()["catalog_hidden_apps"] == ["inventory", "suppliers"]
    page = ana.get("/applications").text
    assert 'data-app="suppliers"' not in page and 'data-app="inventory"' in page  # hidden, but she has access
    assert 'data-app="suppliers"' in hub.get("/applications").text  # the admin has access to every app
    assert 'name="catalog_hidden_app" value="suppliers" checked' in hub.get("/admin").text
    assert hub.put("/api/admin/settings", json=BASE | {"catalog_hidden_apps": ["zzz"]}).status_code == 400
