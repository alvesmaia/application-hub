import pytest
from auth_helper import client_for
from fastapi.testclient import TestClient

import hub
from hub.app import create_app


@pytest.fixture
def client() -> TestClient:
    return client_for(create_app())


def test_healthz_readyz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok", "version": hub.__version__}
    assert client.get("/readyz").json() == {"status": "ready", "version": hub.__version__}


def test_readyz_fails_without_data(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path / "data"))
    c = client_for(create_app())
    monkeypatch.setattr(os, "access", lambda *_a, **_k: False)
    r = c.get("/readyz")
    assert r.status_code == 503 and r.json()["problems"] == ["dados"]


@pytest.mark.parametrize(
    ("path", "ctype"),
    [
        ("/assets/hub.css", "text/css"),
        ("/assets/tokens.css", "text/css"),
        ("/assets/hub-ux.js", "javascript"),
        ("/assets/ux.js", "javascript"),
        ("/assets/logo.svg", "image/svg+xml"),
        ("/assets/icon-192.png", "image/png"),
        ("/assets/fonts/nunito-sans-latin-wght-normal.woff2", "font/woff2"),
        ("/hub-sw.js", "javascript"),
        ("/manifest.webmanifest", "application/manifest+json"),
    ],
)
def test_assets_content_type(client: TestClient, path: str, ctype: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert ctype in r.headers["content-type"]


def test_asset_missing_and_traversal(client: TestClient) -> None:
    assert client.get("/assets/nope.css").status_code == 404
    assert client.get("/assets/%2e%2e/%2e%2e/pyproject.toml").status_code == 404


def test_shell_renders(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    h = r.text
    assert "Content-Security-Policy" in r.headers or "content-security-policy" in r.headers
    for needle in (
        'class="platform-header"',
        'class="brand-name">Application Hub',
        "data-hub-palette-open",
        'class="hub-notif-menu"',
        'class="hub-user-menu"',
        'class="platform-rail"',
        'class="rail-item active" href="/"',
        'href="/applications"',
        'href="/status"',
        "Olá, Administrador",
        'href="/assets/hub.css"',
        'src="/assets/hub-ux.js"',
        "data-hub-drawer-toggle",
    ):
        assert needle in h, needle
    assert h.count('class="rail-group"') == 4


def test_pwa_manifest_start_shortcuts_and_offline(client: TestClient) -> None:
    """F7a: `?start=` (only `/` or a registered app), app shortcuts and public `/offline`."""
    ids = [e.id for e in client.app.state.gateway.registry.entries()]  # type: ignore[attr-defined]
    app_id = ids[0]
    m = client.get(f"/manifest.webmanifest?start=/apps/{app_id}").json()
    assert (
        m["start_url"] == f"/apps/{app_id}" and m["display"] == "standalone" and 1 <= len(m["shortcuts"]) <= 4
    )
    assert m["shortcuts"][0]["url"].startswith("/apps/")
    assert client.get(f"/manifest.webmanifest?start=/{app_id}").json()["start_url"] == f"/apps/{app_id}"
    for bad in ("https://evil.example", "/nao-existe", "//evil"):
        assert client.get("/manifest.webmanifest", params={"start": bad}).json()["start_url"] == "/"
    off = client.get("/offline", follow_redirects=False)
    assert off.status_code == 200 and "hub-offline-page" in off.text


def test_assets_cache_policy_and_revalidation(client: TestClient) -> None:
    """F7a: assets revalidate (ETag -> 304) instead of `no-store`; fonts and vendor files stay cached."""
    r = client.get("/assets/hub.css")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache" and r.headers.get("etag")
    assert r.headers["cdn-cache-control"] == "no-store"
    assert client.get("/assets/hub.css", headers={"if-none-match": r.headers["etag"]}).status_code == 304
    font = client.get("/assets/fonts/nunito-sans-latin-wght-normal.woff2")
    assert font.headers["cache-control"] == "public, max-age=604800"


def test_credential_screens_use_noopener_allow_popups(client: TestClient) -> None:
    """F7a: credential screens (login) open in their own navigation group; the others stay same-origin."""
    anon = TestClient(client.app)  # no session: /login does not redirect
    assert anon.get("/login").headers["cross-origin-opener-policy"] == "noopener-allow-popups"
    assert client.get("/healthz").headers["cross-origin-opener-policy"] == "same-origin"
