"""Governança › Configurações (/admin): tabs Geral · Aplicações · Segurança e chaves · Uso; one row per app with
two switches instead of two boxes of checkboxes; the keys as a list of rows."""

from __future__ import annotations

from pathlib import Path

import pytest
from admin_helper import make_hub


@pytest.fixture
def page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return str(make_hub(tmp_path, monkeypatch).get("/admin").text)


def _panel(page: str, key: str) -> str:
    return page.split(f'id="p-{key}"', 1)[1].split('role="tabpanel"')[1]  # up to the next panel


def test_settings_are_split_in_tabs(page: str) -> None:
    tabs = page.split('data-ptabs=""', 1)[1].split("</div>", 1)[0]
    for label in (">Geral<", ">Aplicações<", ">Segurança e chaves<", ">Uso<"):
        assert label in tabs, label
    assert "settings-grid" not in page and "check-group" not in page  # the old boxes of checkboxes


def test_general_tab_has_the_platform_fields(page: str) -> None:
    geral = _panel(page, "geral")
    for field in ('name="platform_name"', 'name="brand_color"', 'name="language"', 'name="timezone"'):
        assert field in geral, field


def test_applications_tab_has_one_row_per_app_with_two_switches(page: str) -> None:
    apps = _panel(page, "apps")
    assert 'name="status_public"' in apps and apps.count('name="status_public_app"') >= 2
    assert apps.count('name="catalog_visible_app"') == apps.count('name="status_public_app"')
    assert "<table" in apps and "Na página de status" in apps and "Visível no catálogo" in apps


def test_keys_tab_lists_each_item_in_one_row(page: str) -> None:
    keys = _panel(page, "chaves")
    for item in ("Cofre de segredos", "Login único (SSO)", "Credenciais de backup", "Ids do analytics"):
        assert item in keys, item
    assert keys.count('class="gv-row"') >= 4 and "metric-card" not in keys


def test_usage_tab_has_the_usage_card(page: str) -> None:
    assert "data-usage-card" in _panel(page, "uso")
