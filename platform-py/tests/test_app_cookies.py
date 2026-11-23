"""An existing app's own cookies (e.g. a one-shot "flash" message after Post/Redirect/Get) go both ways through the
gateway: only `hub_*` names and the `Domain` attribute are dropped."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from admin_helper import make_hub
from fastapi.testclient import TestClient


def upstream(request: httpx.Request) -> httpx.Response:
    if request.method == "POST":
        headers = {
            "location": "/inventory/lista",
            "set-cookie": "flash=Ve%C3%ADculo%20salvo.; Path=/; HttpOnly",
        }
        return httpx.Response(303, headers=headers, stream=httpx.ByteStream(b""))
    return httpx.Response(200, stream=httpx.ByteStream(request.headers.get("cookie", "").encode()))


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with make_hub(tmp_path, monkeypatch, upstream) as c:
        yield c


def test_flash_cookie_set_on_post_reaches_the_next_page(hub: TestClient) -> None:
    r = hub.post("/_frag/inventory/salvar", data={"a": "1"}, headers={"origin": "http://testserver"})
    assert r.status_code == 303 and r.headers["location"] == "/_frag/inventory/lista"
    assert "flash=" in r.headers["set-cookie"]
    page = hub.get(r.headers["location"])  # the client keeps the cookie, as the browser does
    assert "flash=Ve%C3%ADculo%20salvo." in page.text and "hub_session" not in page.text
