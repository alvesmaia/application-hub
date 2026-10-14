"""Scenario: MCP client (Copilot CLI, Claude Code...) signs in through the browser with single sign-on via Entra ID.

401 on /mcp -> dynamic registration -> /oauth/authorize without a session -> /login -> Entra (fake, v2 format) -> back to
authorize -> consent -> code + PKCE -> hub token -> /mcp. The Entra token never reaches the client.
"""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import fake_entra
import httpx
import pytest
from api_helper import api_hub, bearer, rpc
from fastapi.testclient import TestClient
from test_oauth import CB, ISS, approve, authz_query, exchange, machine, pkce, register

from hub.auth import entra

AUTH = "https://login.fake"


def test_mcp_login_via_entra_sso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = {
        "HUB_PUBLIC_URL": ISS,
        "HUB_SSO_PROVIDER": "entra",
        "HUB_ENTRA_TENANT_ID": fake_entra.TENANT,
        "HUB_ENTRA_CLIENT_ID": fake_entra.CLIENT,
        "HUB_ENTRA_CLIENT_SECRET": fake_entra.SECRET,
        "HUB_ENTRA_AUTHORITY": AUTH,
    }
    hub = api_hub(tmp_path, monkeypatch, env=env)
    fake = TestClient(fake_entra.make_app(AUTH), follow_redirects=False)
    monkeypatch.setattr(entra, "TRANSPORT", httpx.ASGITransport(app=fake.app))
    fake_entra.OVERRIDES.clear()

    # 1. client without a token: 401 points to discovery
    r = rpc(machine(hub), "tools/list")
    assert r.status_code == 401 and "resource_metadata" in r.headers["www-authenticate"]

    # 2. dynamic registration and opening the browser without a session
    cid = register(hub)["client_id"]
    verifier, challenge = pkce()
    browser = TestClient(hub.app, follow_redirects=False)
    browser.headers["origin"] = ISS
    authorize = "/oauth/authorize?" + authz_query(cid, challenge)
    r = browser.get(authorize)
    assert r.status_code in (302, 303, 307) and r.headers["location"].startswith("/login?next=")
    nxt = parse_qs(urlsplit(r.headers["location"]).query)["next"][0]
    assert nxt.startswith("/oauth/authorize?")
    assert "data-sso-entra" in browser.get(r.headers["location"]).text

    # 3. "Sign in with Microsoft": Entra asks only for openid/profile/email and returns to authorize
    r = browser.get("/auth/entra/login", params={"next": nxt})
    to_entra = r.headers["location"]
    assert to_entra.startswith(f"{AUTH}/{fake_entra.TENANT}/oauth2/v2.0/authorize?")
    assert parse_qs(urlsplit(to_entra).query)["scope"] == ["openid profile email"]
    back = fake.get(to_entra.removeprefix(AUTH))
    r = browser.get(back.headers["location"].removeprefix(ISS))
    assert r.status_code == 303 and r.headers["location"] == nxt

    # 4. consent as the Entra user and code exchange (PKCE)
    page = browser.get(nxt)
    assert page.status_code == 200 and 'name="consent"' in page.text
    nonce = page.text.split('name="consent" value="', 1)[1].split('"', 1)[0]
    r = approve(browser, nonce, scopes=("mcp",))
    assert r.status_code == 303 and r.headers["location"].startswith(CB)
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    tok = exchange(hub, cid, code, verifier)
    assert tok.status_code == 200, tok.text
    body = tok.json()
    assert body["token_type"].lower() == "bearer" and "id_token" not in body  # nothing from Entra leaks

    # 5. the hub token opens /mcp
    r = rpc(bearer(hub, body["access_token"]), "tools/list")
    assert r.status_code == 200 and r.json()["result"]["tools"]
    fake_entra.OVERRIDES.clear()
