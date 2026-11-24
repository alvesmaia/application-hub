"""The platform's default language (Settings) is used when the user has no preference of their own, before the
browser's language; a user's own choice still wins. Never saved: the browser decides, as before."""

from __future__ import annotations

from pathlib import Path

import pytest
from admin_helper import make_hub
from fastapi.testclient import TestClient

EN = {"accept-language": "en"}
SETTINGS = {"platform_name": "", "brand_color": "", "status_public": False, "status_public_apps": [],
            "timezone": "America/Sao_Paulo"}  # fmt: skip


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return make_hub(tmp_path, monkeypatch)


def _lang(c: TestClient, headers: dict[str, str]) -> str:
    html = str(c.get("/applications", headers=headers).text)
    return html.split('<html lang="', 1)[1].split('"', 1)[0]


def test_without_a_saved_default_the_browser_decides(hub: TestClient) -> None:
    assert _lang(hub, EN) == "en"


def test_saved_default_beats_the_browser(hub: TestClient) -> None:
    assert hub.put("/api/admin/settings", json=SETTINGS | {"language": "pt-BR"}).status_code == 200
    assert _lang(hub, EN) == "pt-BR"
    anon = TestClient(hub.app)  # the login page too
    assert 'lang="pt-BR"' in anon.get("/login", headers=EN).text


def test_the_users_own_choice_beats_the_default(hub: TestClient) -> None:
    hub.put("/api/admin/settings", json=SETTINGS | {"language": "pt-BR"})
    assert hub.put("/api/me/prefs", json={"lang": "en"}).status_code == 200
    assert _lang(hub, {}) == "en"


def test_saving_other_settings_does_not_pin_a_language(hub: TestClient) -> None:
    """Never chosen = automatic: a settings round-trip (e.g. only the brand color) keeps the browser deciding."""
    cur = hub.get("/api/admin/settings").json()
    assert cur["language"] == ""
    assert hub.put("/api/admin/settings", json=cur | {"brand_color": "#aa3300"}).status_code == 200
    assert _lang(hub, EN) == "en"
    assert 'value="" selected' in hub.get("/admin").text.replace(
        'selected=""', "selected"
    )  # "Automático" option
