"""Adapting an existing app keeps its own markup and files: the sidebar block takes the original sidebar's
HTML (the hub keeps only its mechanics: collapse, back link, phone drawer), and `asset_file` serves the
app's fonts and images by name (no client path reaches the disk)."""

from pathlib import Path

import pytest
from starlette.requests import Request

from hub_sdk.render import AppInfo, NavItem, Renderer, asset_file

APP = AppInfo(id="demo", name="Demo", nav=(NavItem("dashboard", "Dashboard", "dashboard"),))


def _req() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/demo", "headers": [], "query_string": b""})


def test_the_sidebar_block_keeps_the_apps_own_markup(tmp_path: Path) -> None:
    (tmp_path / "page.html").write_text(
        '{% extends "workspace.html" %}{% block sidebar %}<nav class="orig-nav"><a class="orig-link">Tickets</a>'
        '</nav>{% endblock %}{% block content %}<div class="orig-page">x</div>{% endblock %}',
        encoding="utf-8",
    )
    body = bytes(Renderer(APP, tmp_path).page(_req(), "page.html", "dashboard").body).decode()
    aside = body.split('<aside class="app-sidebar"', 1)[1].split("</aside>", 1)[0]
    assert '<nav class="orig-nav"><a class="orig-link">Tickets</a></nav>' in aside
    assert "sidebar-nav" not in aside  # the default nav is replaced, not duplicated
    assert 'data-hub-menu="collapse"' in aside and 'class="back-link"' in aside  # hub mechanics stay
    assert '<div class="orig-page">x</div>' in body


def test_without_the_block_the_default_sidebar_stays(tmp_path: Path) -> None:
    (tmp_path / "page.html").write_text(
        '{% extends "workspace.html" %}{% block content %}x{% endblock %}', "utf-8"
    )
    body = bytes(Renderer(APP, tmp_path).page(_req(), "page.html", "dashboard").body).decode()
    assert 'class="sidebar-nav"' in body and "nav-item active" in body


def test_asset_file_serves_fonts_and_images_by_name(tmp_path: Path) -> None:
    (tmp_path / "ms.woff2").write_bytes(b"wOF2")
    (tmp_path / "logo.svg").write_text("<svg/>", "utf-8")
    font = asset_file(tmp_path, "ms.woff2")
    assert font.status_code == 200 and font.media_type == "font/woff2" and bytes(font.body) == b"wOF2"
    assert font.headers["x-content-type-options"] == "nosniff"
    assert asset_file(tmp_path, "logo.svg").media_type == "image/svg+xml"


@pytest.mark.parametrize(
    "name", ["../secret.txt", "..%2Fx.woff2", "a/b.woff2", "x.js", "x.html", "", "missing.woff2"]
)
def test_asset_file_refuses_paths_unknown_types_and_missing_files(tmp_path: Path, name: str) -> None:
    (tmp_path.parent / "secret.txt").write_text("s", "utf-8")
    assert asset_file(tmp_path, name).status_code == 404
