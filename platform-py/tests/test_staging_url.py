"""Staging has its own URL: `/staging/apps/<id>/…` (and `/staging/_frag/…` for the shell). The path picks the
environment, so a staging link opens staging in any tab and a production link never lands in staging because of a
forgotten cookie; the cookie only sends `/apps/<id>` to the staging URL. A "STAGING" badge sits in the header."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from admin_helper import add_user, make_hub
from fastapi.testclient import TestClient

SEEN: list[dict[str, str]] = []


def upstream(request: httpx.Request) -> httpx.Response:
    SEEN.append({"host": request.url.host, "env": request.headers.get("x-hub-env", ""),
                 "base": request.headers.get("x-hub-base", ""), "path": request.url.path})  # fmt: skip
    if request.url.path.endswith("/redir"):
        return httpx.Response(302, headers={"location": "/inventory/y"}, stream=httpx.ByteStream(b""))
    return httpx.Response(200, stream=httpx.ByteStream(b"<p>ok</p>"))


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    SEEN.clear()
    with make_hub(tmp_path, monkeypatch, upstream) as c:
        c.app.state.gateway.registry.staging["inventory"] = "http://stg:8080"  # type: ignore[attr-defined]
        yield c


def test_the_staging_path_forwards_to_staging_with_its_own_base(hub: TestClient) -> None:
    assert hub.get("/staging/_frag/inventory/inventory/x").status_code == 200
    assert SEEN[-1] == {
        "host": "stg",
        "env": "staging",
        "base": "/staging/apps/inventory",
        "path": "/inventory/inventory/x",
    }
    hub.get("/_frag/inventory/inventory/x", headers={"cookie": f"{hub.headers['cookie']}; hub_env=staging"})
    prod = SEEN[-1]  # the path wins over the cookie; the upstream path is the same
    assert (prod["env"], prod["base"], prod["path"]) == (
        "producao",
        "/apps/inventory",
        "/inventory/inventory/x",
    )


def test_the_staging_page_loads_fragments_from_staging(hub: TestClient) -> None:
    page = hub.get("/staging/apps/inventory/inventory").text
    assert 'data-hub-frag="/staging/_frag"' in page and 'data-hub-base="/staging/apps/inventory"' in page
    assert "data-hub-staging-bar" in page and "data-hub-env-badge" in page
    assert "data-hub-env-badge" not in hub.get("/apps/inventory").text


def test_production_url_with_the_staging_cookie_goes_to_the_staging_url(hub: TestClient) -> None:
    r = hub.get(
        "/apps/inventory/inventory?a=1", headers={"cookie": f"{hub.headers['cookie']}; hub_env=staging"}
    )
    assert r.status_code == 303 and r.headers["location"] == "/staging/apps/inventory/inventory?a=1"


def test_switching_environment_keeps_the_app_page(hub: TestClient) -> None:
    to_stg = hub.get("/hub-env?to=staging&next=/apps/inventory/inventory")
    assert to_stg.headers["location"] == "/staging/apps/inventory/inventory"
    assert (
        hub.get("/hub-env?to=producao&next=/staging/apps/inventory").headers["location"] == "/apps/inventory"
    )


def test_staging_url_needs_the_staging_permission(hub: TestClient) -> None:
    colab = add_user(hub, "colab", ["User"])
    assert colab.get("/staging/apps/inventory").status_code == 403
    assert colab.get("/staging/_frag/inventory/inventory/x").status_code == 403
    assert colab.get("/apps/inventory").status_code == 200


def test_staging_websocket_needs_the_staging_permission(hub: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    colab = add_user(hub, "colab", ["User"])
    with (
        pytest.raises(WebSocketDisconnect) as e,
        colab.websocket_connect("/staging/apps/inventory/ws", headers={"origin": "http://testserver"}),
    ):
        pass
    assert e.value.code == 1008


def test_an_app_redirect_stays_in_staging(hub: TestClient) -> None:
    nav = {"sec-fetch-mode": "navigate"}  # the browser itself (a download link): the public URL
    assert (
        hub.get("/staging/_frag/inventory/inventory/redir", headers=nav).headers["location"]
        == "/staging/apps/inventory/y"
    )
    assert hub.get("/_frag/inventory/inventory/redir", headers=nav).headers["location"] == "/apps/inventory/y"


def test_a_fragment_redirect_stays_a_fragment(hub: TestClient) -> None:
    """hub.js fetches fragments: a 303 after a POST (Post/Redirect/Get) must lead to the next fragment, not to the
    whole hub page (which ended up embedded inside the app's screen)."""
    for mode in ({}, {"sec-fetch-mode": "cors"}, {"sec-fetch-mode": "same-origin"}):
        assert (
            hub.get("/_frag/inventory/inventory/redir", headers=mode).headers["location"]
            == "/_frag/inventory/y"
        )
        staged = hub.get("/staging/_frag/inventory/inventory/redir", headers=mode).headers["location"]
        assert staged == "/staging/_frag/inventory/y"


def test_the_trailing_slash_redirect_stays_in_staging(hub: TestClient) -> None:
    # the router's slash redirect is built from the path without the staging prefix
    r = hub.get("/staging/apps/inventory/", follow_redirects=False)
    assert r.status_code in (307, 308) and r.headers["location"].endswith("/staging/apps/inventory")


def test_the_kit_requests_of_a_staging_page_go_to_staging(hub: TestClient) -> None:
    # hub-kit.js (data-hub-get/-post, SSE) loads before hub.js: it needs the staging prefix on its own tag,
    # or its requests go to /_frag/… and the page's CSP refuses them
    page = hub.get("/staging/apps/inventory/inventory").text
    assert '<script src="/assets/hub-kit.js" data-hub-kit="" data-hub-frag="/staging/_frag">' in page


def test_app_files_at_the_public_base_reach_the_app(hub: TestClient) -> None:
    # `<img src="{{ base }}/assets/files/logo.svg">`: the page's address is /apps/<id>/…, so its files are too
    assert hub.get("/apps/inventory/assets/files/logo.svg").status_code == 200
    assert SEEN[-1]["path"] == "/inventory/assets/files/logo.svg" and SEEN[-1]["env"] == "producao"
    assert hub.get("/staging/apps/inventory/assets/app.css").status_code == 200
    assert SEEN[-1]["path"] == "/inventory/assets/app.css" and SEEN[-1]["host"] == "stg"


def test_an_app_page_named_assets_still_opens_in_the_shell(hub: TestClient) -> None:
    page = hub.get("/apps/inventory/assets/12").text
    assert 'data-hub-base="/apps/inventory"' in page and 'data-hub-section="assets"' in page
