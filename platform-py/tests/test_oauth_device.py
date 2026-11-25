"""`hub login`: OAuth device authorization (RFC 8628) for the built-in `hub-cli` client. One approval in the browser
gives the CLI every surface it uses (API v1, deploy, MCP, app APIs) as the person, with the person's RBAC; the
session ends after 30 days unused or 90 days in total."""

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from api_helper import add_user, api_hub, bearer, rpc
from fastapi.testclient import TestClient
from test_oauth import oauth_login

from hub.api import oauth_util
from hub.api.oauth_device import CLI_CLIENT, DEVICE_GRANT

ISS = "http://testserver"


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return api_hub(tmp_path, monkeypatch, env={"HUB_PUBLIC_URL": ISS})


def machine(hub: TestClient) -> TestClient:
    return TestClient(hub.app, follow_redirects=False)


def start(hub: TestClient) -> dict[str, Any]:
    r = machine(hub).post("/oauth/device_authorization", data={"client_id": CLI_CLIENT})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def poll(hub: TestClient, device_code: str) -> Any:
    form = {"grant_type": DEVICE_GRANT, "device_code": device_code, "client_id": CLI_CLIENT}
    return machine(hub).post("/oauth/token", data=form)


def decide(page_client: TestClient, user_code: str, decision: str = "approve") -> Any:
    return page_client.post("/oauth/device", data={"user_code": user_code, "decision": decision})


def login(hub: TestClient, who: TestClient | None = None) -> dict[str, Any]:
    d = start(hub)
    assert decide(who or hub, d["user_code"]).status_code == 200
    r = poll(hub, d["device_code"])
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_device_flow_end_to_end(hub: TestClient) -> None:
    d = start(hub)
    assert (
        re.fullmatch(r"[B-Z]{4}-[B-Z]{4}", d["user_code"]) and d["interval"] == 5 and d["expires_in"] == 600
    )
    assert d["verification_uri"] == f"{ISS}/oauth/device"
    assert d["verification_uri_complete"] == f"{ISS}/oauth/device?user_code={d['user_code']}"
    assert poll(hub, d["device_code"]).json()["error"] == "authorization_pending"
    assert poll(hub, d["device_code"]).json()["error"] == "slow_down"  # polled before the interval
    page = hub.get(f"/oauth/device?user_code={d['user_code']}")
    assert page.status_code == 200 and d["user_code"] in page.text and "data-oauth-approve" in page.text
    assert "Hub CLI" in page.text and "90 dias" in page.text
    assert decide(hub, d["user_code"]).status_code == 200
    t = poll_after_interval(hub, d["device_code"])
    assert t["access_token"].startswith("hubo_") and t["refresh_token"].startswith("hubr_")
    assert set(t["scope"].split()) == {"mcp", "api:read", "api:write", "app-api", "deploy"}
    assert poll(hub, d["device_code"]).json()["error"] == "invalid_grant"  # single use
    c = bearer(hub, t["access_token"])
    assert c.get("/api/v1/apps").status_code == 200
    assert c.get("/api/ops/deployments").status_code == 200
    assert rpc(c, "tools/list").status_code == 200
    titles = [e["title"] for e in hub.get("/api/v1/audit?limit=20").json()["items"]]
    assert "OAuth: login do CLI autorizado" in titles


def poll_after_interval(hub: TestClient, device_code: str) -> dict[str, Any]:
    oa = hub.app.state.oauth  # type: ignore[attr-defined]
    oa.device_last_poll.clear()  # the test does not wait the 5 s interval
    r = poll(hub, device_code)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_denied_unknown_and_expired_codes(hub: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    d = start(hub)
    assert decide(hub, d["user_code"], "deny").status_code == 200
    hub.app.state.oauth.device_last_poll.clear()  # type: ignore[attr-defined]
    assert poll(hub, d["device_code"]).json()["error"] == "access_denied"
    bad = decide(hub, "BBBB-BBBB")
    assert bad.status_code == 400 and "não encontrado ou vencido" in bad.text
    assert poll(hub, "nope").json()["error"] == "expired_token"
    d = start(hub)
    later = oauth_util.now_ms() + 11 * 60_000
    monkeypatch.setattr("hub.api.oauth_device.now_ms", lambda: later)
    hub.app.state.oauth.device_last_poll.clear()  # type: ignore[attr-defined]
    assert poll(hub, d["device_code"]).json()["error"] == "expired_token"


def test_only_the_cli_client_and_a_signed_in_person(hub: TestClient) -> None:
    r = machine(hub).post("/oauth/device_authorization", data={"client_id": "hubc_other"})
    assert r.status_code == 401 and r.json()["error"] == "invalid_client"
    anon = TestClient(hub.app, follow_redirects=False)
    assert anon.get("/oauth/device").status_code in (302, 303)  # sign in first


def test_cli_login_acts_with_the_persons_permissions(hub: TestClient) -> None:
    user = add_user(hub, "leitor", ["User"])
    t = login(hub, user)
    c = bearer(hub, t["access_token"])
    assert c.get("/api/v1/apps").status_code == 200
    assert c.get("/api/ops/deployments").status_code == 403  # deploy needs editar_configuracoes
    files = {"file": ("x.hubapp", b"PK", "application/zip")}
    assert c.post("/api/ops/packages", files=files).status_code == 403


def test_mcp_client_tokens_stay_on_mcp_only(hub: TestClient) -> None:
    t = oauth_login(hub)
    assert bearer(hub, t["access_token"]).get("/api/v1/apps").status_code == 401


def test_session_ends_after_90_days_even_when_used(hub: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    t = login(hub)
    form = {"grant_type": "refresh_token", "client_id": CLI_CLIENT, "refresh_token": t["refresh_token"]}
    r = machine(hub).post("/oauth/token", data=form)
    assert r.status_code == 200
    later = oauth_util.now_ms() + 91 * oauth_util.DAY_MS
    monkeypatch.setattr("hub.api.oauth_tokens.now_ms", lambda: later)
    form["refresh_token"] = r.json()["refresh_token"]
    r = machine(hub).post("/oauth/token", data=form)
    assert (
        r.status_code == 400
        and r.json()["error"] == "invalid_grant"
        and "hub login" in r.json()["error_description"]
    )


def test_cli_session_is_listed_and_revocable_in_my_account(hub: TestClient) -> None:
    t = login(hub)
    page = hub.get("/account/access").text
    assert "Hub CLI" in page
    oa = hub.app.state.oauth  # type: ignore[attr-defined]
    mine = asyncio.run(oa.grants())
    assert mine and mine[0]["client_id"] == CLI_CLIENT
    assert asyncio.run(oa.revoke_for_user(mine[0]["user"], mine[0]["grant_id"]))
    assert bearer(hub, t["access_token"]).get("/api/v1/apps").status_code == 401


def test_cli_refresh_tolerates_two_processes_refreshing_at_once(
    hub: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`hub mcp` and `hub deploy` may refresh with the same token at the same moment: within 60 s that is not
    theft (both get tokens); later, a reused refresh token still revokes the session."""
    t = login(hub)
    assert t["user"] == "admin@demo.local" or "@" in t["user"]
    form = {"grant_type": "refresh_token", "client_id": CLI_CLIENT, "refresh_token": t["refresh_token"]}
    first, second = machine(hub).post("/oauth/token", data=form), machine(hub).post("/oauth/token", data=form)
    assert first.status_code == 200 and second.status_code == 200, second.text
    later = oauth_util.now_ms() + 61_000
    monkeypatch.setattr("hub.api.oauth_tokens.now_ms", lambda: later)
    third = machine(hub).post("/oauth/token", data=form)
    assert third.status_code == 400 and third.json()["error"] == "invalid_grant"
    assert (
        bearer(hub, second.json()["access_token"]).get("/api/v1/apps").status_code == 401
    )  # session revoked
