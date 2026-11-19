"""The hub version sits in the header for signed-in people (admin, user, deployer); anonymous pages do not show it
(no version fingerprint for strangers)."""

from __future__ import annotations

from pathlib import Path

import pytest
from admin_helper import add_user, make_hub
from fastapi.testclient import TestClient

from hub import __version__

BADGE = f'data-hub-version="">v{__version__}<'


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return make_hub(tmp_path, monkeypatch)


def test_signed_in_header_shows_the_version(hub: TestClient) -> None:
    assert BADGE in hub.get("/admin/tasks").text
    assert BADGE in add_user(hub, "colab", ["Colaborador"]).get("/applications").text


def test_anonymous_pages_do_not_show_the_version(hub: TestClient) -> None:
    anon = TestClient(hub.app)
    assert "data-hub-version" not in anon.get("/login").text
    assert "data-hub-version" not in anon.get("/developers").text


def test_version_sits_right_after_the_github_icon(hub: TestClient) -> None:
    page = hub.get("/admin/tasks").text
    link_end = page.index("</a>", page.index("hub-source-link"))
    assert page.index("data-hub-version") > link_end
    assert page.index("data-hub-version") - link_end < 200  # next to it, not elsewhere in the header
