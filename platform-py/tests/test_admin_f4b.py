"""F4b: `/admin` (Settings), menu by permission, API Tokens with the OAuth section."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from admin_helper import add_user, make_hub
from fastapi.testclient import TestClient

JSON = {"content-type": "application/json"}


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return make_hub(tmp_path, monkeypatch)


def test_admin_landing_settings_and_menu_by_permission(hub: TestClient) -> None:
    r = hub.get("/admin")
    assert r.status_code == 200 and "Configurações da plataforma" in r.text
    assert 'id="hub-admin-menu"' in r.text and "Cofre de segredos" in r.text
    tech = add_user(hub, "tech", ["Power User"])
    assert tech.get("/admin").status_code == 200
    assert 'id="hub-admin-menu"' in tech.get("/applications").text
    reader = add_user(hub, "leitor", ["User"])
    home = reader.get("/applications")
    assert home.status_code == 200 and 'id="hub-admin-menu"' not in home.text
    for p in ("/admin", "/admin/env", "/admin/users"):
        assert reader.get(p).status_code == 403, p
    for p in ("/api/admin/settings", "/api/admin/users"):
        assert reader.get(p).status_code == 403, p
    assert 'id="hub-admin-menu"' not in reader.get("/admin/env").text


def test_settings_api_validates_and_persists(hub: TestClient) -> None:
    ok = {"platform_name": " Meu Hub ", "brand_color": "#ABCDEF", "status_public": True,
          "status_public_apps": ["inventory", "inventory"], "language": "en", "timezone": "America/Sao_Paulo"}  # fmt: skip
    r = hub.put("/api/admin/settings", json=ok)
    assert r.status_code == 200 and r.json()["platform_name"] == "Meu Hub"
    assert r.json()["brand_color"] == "#abcdef" and r.json()["status_public_apps"] == ["inventory"]
    assert hub.get("/api/admin/settings").json()["language"] == "en"
    assert "--color-accent: #abcdef" in hub.get("/assets/theme.css").text  # white label
    assert 'value="Meu Hub"' in hub.get("/admin").text
    for bad in (
        {"language": "xx"},
        {"brand_color": "red"},
        {"status_public_apps": ["zzz"]},
        {"timezone": "UTC"},
    ):
        assert hub.put("/api/admin/settings", json=ok | bad).status_code == 400
    r = hub.post("/api/admin/vault/rotate-data-key", headers=JSON)
    assert r.status_code == 200 and r.json()["active_data_key"]
    st = hub.get("/api/admin/vault").json()
    assert (
        st["enabled"] is True
        and st["kek_fingerprint"]
        and st["active_data_key"] == r.json()["active_data_key"]
    )
    assert "master" not in json.dumps(st).lower() and st["data_keys"] >= 2
