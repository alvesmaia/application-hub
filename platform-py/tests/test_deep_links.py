"""A deep link of an app (`/apps/<id>/veiculos/12`, `/apps/<id>/veiculos/12/editar`) opens the app page like a
section does: existing apps use nested routes, and a reload or a bookmark must not land on a 404."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from admin_helper import make_hub
from fastapi.testclient import TestClient


def upstream(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, stream=httpx.ByteStream(b"<p>ok</p>"))


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with make_hub(tmp_path, monkeypatch, upstream) as c:
        yield c


@pytest.mark.parametrize("path", ["/apps/inventory/veiculos/12", "/apps/inventory/veiculos/12/editar"])
def test_nested_app_paths_open_the_app_page(hub: TestClient, path: str) -> None:
    r = hub.get(path)
    assert (
        r.status_code == 200
        and 'data-hub-app="inventory"' in r.text
        and 'data-hub-section="veiculos"' in r.text
    )


@pytest.mark.parametrize("path", ["/apps/inventory/veiculos/a%20b", "/apps/inventory/x/" + "y" * 300])
def test_odd_segments_are_still_not_found(hub: TestClient, path: str) -> None:
    assert hub.get(path).status_code == 404


def test_api_and_staging_keep_their_routes(hub: TestClient) -> None:
    assert hub.get("/apps/inventory/api/v1/x").text == "<p>ok</p>"
    assert hub.get("/apps/inventory/veiculos/12", follow_redirects=False).status_code == 200
