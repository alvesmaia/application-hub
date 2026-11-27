"""Inside an app the header serves that app: its own search box (manifest `search`; the hub only opens the app's page)
and the bell with that app's notifications. The GitHub link and the version sit at the foot of the side menu."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from admin_helper import make_hub
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hub.models import AppManifest, search_error
from hub.ux.inbox import item_of


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    c = make_hub(tmp_path, monkeypatch)
    reg = c.app.state.gateway.registry  # type: ignore[attr-defined]
    inv = reg.get("inventory")
    reg.add(replace(inv, manifest=AppManifest(id="inventory", search="assets?q=")))
    return c


def test_the_manifest_search_is_a_page_of_the_app_with_one_parameter() -> None:
    assert AppManifest(id="x", search="assets?q=").search == "assets?q="
    assert AppManifest(id="x", search="itens/busca?termo=").search == "itens/busca?termo="
    for bad in ("https://evil.example/?q=", "/apps/x/assets?q=", "assets", "assets?q", "../a?q=", "a?q=1&b="):
        with pytest.raises(ValidationError):
            AppManifest(id="x", search=bad)


def test_a_package_with_a_bad_search_is_refused_at_the_manifest_step() -> None:
    assert search_error(None) is None and search_error("assets?q=") is None
    assert "search inválido" in (search_error("https://evil.example/?q=") or "")


def test_an_app_with_search_gets_its_own_box_in_the_header(hub: TestClient) -> None:
    page = hub.get("/apps/inventory").text
    form = page.split('class="hub-app-search"', 1)[1].split("</form>", 1)[0]
    assert (
        'action="/apps/inventory/assets"' in form and 'name="q"' in form and "Buscar em App Inventory" in form
    )
    assert 'class="hub-app-search"' not in hub.get("/apps/collector").text  # no `search`: no box
    assert 'class="hub-app-search"' not in hub.get("/").text  # the hub itself has no search


def test_inside_an_app_the_bell_shows_only_that_apps_notifications(hub: TestClient) -> None:
    inbox = hub.app.state.inbox  # type: ignore[attr-defined]
    inbox.push(item_of("notification", "inventory", "Importação concluída", to_roles=["Administrador"]))
    inbox.push(item_of("notification", "servicedesk", "Chamado novo", to_roles=["Administrador"]))
    home = hub.get("/").text
    assert "Importação concluída" in home and "Chamado novo" in home
    app = hub.get("/apps/inventory").text
    bell = app.split('class="hub-notif-menu"', 1)[1].split("</details>", 1)[0]
    assert "Importação concluída" in bell and "Chamado novo" not in bell
    assert (
        'data-hub-notifications="1"' in app and 'href="/inbox"' in bell
    )  # its count; "see all" opens the Inbox


def test_github_and_version_sit_at_the_foot_of_the_side_menu(hub: TestClient) -> None:
    page = hub.get("/").text
    header = page.split("<header", 1)[1].split("</header>", 1)[0]
    rail = page.split('id="hub-rail"', 1)[1].split("</aside>", 1)[0]
    assert "data-hub-version" not in header and "data-hub-source" not in header
    foot = rail.split('class="rail-footer"', 1)[1]
    assert "data-hub-version" in foot


def test_the_hub_never_overwrites_the_apps_ink_role() -> None:
    # CONTRATO-APP §29: `--app-ink` is the app's text-ink role; the catalog's identity color goes by another name
    js = (Path(__file__).resolve().parents[2] / "platform/assets/shell/hub.js").read_text(encoding="utf-8")
    assert "setProperty('--app-ink'" not in js and "setProperty('--app-identity-ink'" in js


def _hub_css() -> str:
    return (Path(__file__).resolve().parents[2] / "platform/assets/shell/hub.css").read_text(encoding="utf-8")


def test_a_select_can_opt_out_of_the_hubs_chevron() -> None:
    # an app that draws its own arrow marks the select `hub-select-plain` (otherwise two arrows show)
    css = _hub_css()
    chevron = css[
        css.index("--hub-select-chevron)!important") - 300 : css.index("--hub-select-chevron)!important")
    ]
    assert ":not(.hub-select-plain)" in chevron.rsplit("}", 1)[-1]


def test_the_apps_classes_beat_the_hubs_select_style() -> None:
    # zero specificity (`:where`): a utility class of the app (Tailwind `rounded`, Bootstrap `form-select`) wins
    css = _hub_css()
    assert "\nselect:not([multiple])" not in css and ":where(select:not([multiple]))," in css


def test_rem_is_the_browsers_16px_inside_the_app() -> None:
    # Tailwind/Bootstrap size everything in rem: a 13px root shrank apps to 81%; the hub's 13px lives on <body>
    css = _hub_css()
    root = css[css.index(":root{font-family") :].split("}", 1)[0]
    body = css[css.index("\nbody{font-family") :].split("}", 1)[0]
    assert "font-size" not in root and "font-size:13px" in body
