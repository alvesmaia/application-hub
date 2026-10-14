import asyncio
import json
from collections.abc import Iterator
from typing import Any

import fake_entra
import httpx
import pytest
from auth_helper import SESSION, client_for
from fastapi.testclient import TestClient

from hub.app import create_app
from hub.auth import entra

ISS = "https://login.fake"


@pytest.fixture
def sso(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    for k, v in {
        "HUB_SSO_PROVIDER": "entra",
        "HUB_ENTRA_TENANT_ID": fake_entra.TENANT,
        "HUB_ENTRA_CLIENT_ID": fake_entra.CLIENT,
        "HUB_ENTRA_CLIENT_SECRET": fake_entra.SECRET,
        "HUB_ENTRA_AUTHORITY": ISS,
        "HUB_ENTRA_ROLE_MAP": '{"g-admins": "Administrador"}',
    }.items():
        monkeypatch.setenv(k, v)
    fake = fake_entra.make_app(ISS)
    monkeypatch.setattr(entra, "TRANSPORT", httpx.ASGITransport(app=fake))
    fake_entra.OVERRIDES.clear()
    yield TestClient(fake, follow_redirects=False)
    fake_entra.OVERRIDES.clear()


def anon(app_client: TestClient) -> TestClient:
    app_client.headers.pop("cookie", None)
    app_client.cookies.clear()
    return app_client


def flow(client: TestClient, fake: TestClient) -> Any:
    r = client.get("/auth/entra/login?next=/applications")
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"{ISS}/{fake_entra.TENANT}/oauth2/v2.0/authorize?")
    assert "code_challenge_method=S256" in r.headers["location"]
    back = fake.get(r.headers["location"].removeprefix(ISS))
    return client.get(back.headers["location"].removeprefix("http://testserver"))


def users(client: TestClient) -> list[dict[str, Any]]:
    return json.loads(client.app.state.auth.path.read_text(encoding="utf-8"))["users"]  # type: ignore[attr-defined]


def test_login_page_without_sso_hides_button() -> None:
    client = anon(client_for(create_app()))
    page = client.get("/login").text
    assert "data-sso-entra" not in page and 'action="/login"' in page


def test_success_jit_provisioning_and_session(sso: TestClient) -> None:
    client = anon(client_for(create_app()))
    page = client.get("/login").text
    assert "data-sso-entra" in page and 'action="/login"' in page and fake_entra.SECRET not in page
    r = flow(client, sso)
    assert r.status_code == 303 and r.headers["location"] == "/applications"
    u = next(u for u in users(client) if u["email"] == "ana@contoso.com")
    assert u["roles"] == ["Colaborador"] and u["sso_subject"] == "oid-ana" and "password_hash" not in u
    client.headers["cookie"] = f"hub_session={r.cookies['hub_session']}"
    assert client.get("/").status_code == 200
    # segundo login: mesmo usuário, sem duplicar
    anon(client)
    assert flow(client, sso).status_code == 303
    assert sum(u["email"] == "ana@contoso.com" for u in users(client)) == 1


def test_role_mapping_from_groups(sso: TestClient) -> None:
    fake_entra.OVERRIDES["groups"] = ["G-ADMINS", "outro"]
    client = anon(client_for(create_app()))
    assert flow(client, sso).status_code == 303
    assert next(u for u in users(client) if u["email"] == "ana@contoso.com")["roles"] == ["Administrador"]


@pytest.mark.parametrize(
    "override", [{"tid": "99999999-0000-0000-0000-000000000000"}, {"bad_nonce": True}, {"bad_sig": True}]
)
def test_rejected_tokens(sso: TestClient, override: dict[str, Any]) -> None:
    fake_entra.OVERRIDES.update(override)
    client = anon(client_for(create_app()))
    r = flow(client, sso)
    assert r.status_code == 401 and "hub_session" not in r.cookies
    assert not any(u["email"] == "ana@contoso.com" for u in users(client))


def test_bad_state_rejected(sso: TestClient) -> None:
    client = anon(client_for(create_app()))
    r = client.get("/auth/entra/callback?code=x&state=forjado")
    assert r.status_code == 401


def test_deactivated_user_blocked(sso: TestClient) -> None:
    client = anon(client_for(create_app()))
    assert flow(client, sso).status_code == 303
    path = client.app.state.auth.path  # type: ignore[attr-defined]
    d = json.loads(path.read_text(encoding="utf-8"))
    for u in d["users"]:
        if u["email"] == "ana@contoso.com":
            u["active"] = False
    path.write_text(json.dumps(d), encoding="utf-8")
    anon(client)
    assert flow(client, sso).status_code == 401


def test_allowed_domains(sso: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_ENTRA_ALLOWED_DOMAINS", "fabrikam.com")
    client = anon(client_for(create_app()))
    assert flow(client, sso).status_code == 401


def test_local_login_admins_only(sso: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_LOCAL_LOGIN", "admins")
    client = client_for(create_app())
    h = {"sec-fetch-site": "same-origin", "cookie": f"hub_session={SESSION}"}
    ok = client.post("/login", data={"email": "admin", "password": "test-password"}, headers=h)
    assert ok.status_code == 303
    status = client.get("/admin", headers={"cookie": f"hub_session={ok.cookies['hub_session']}"}).text
    assert "data-sso-status" in status and fake_entra.TENANT in status and fake_entra.SECRET not in status


def test_multi_tenant_requires_allowed_tenants(sso: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_ENTRA_TENANT_ID", "organizations")
    client = client_for(create_app())
    assert "data-sso-entra" not in anon(client_for(create_app())).get("/login").text
    page = client.get("/admin").text
    assert "data-sso-reason" in page and "HUB_ENTRA_ALLOWED_TENANTS" in page
    assert fake_entra.SECRET not in page
    monkeypatch.setenv("HUB_ENTRA_ALLOWED_TENANTS", fake_entra.TENANT)
    assert "data-sso-entra" in anon(client_for(create_app())).get("/login").text


def test_role_map_formats() -> None:
    assert entra.parse_role_map("a=Administrador, b = Leitor") == {"a": "Administrador", "b": "Leitor"}
    assert entra.parse_role_map('{"A":"X"}') == {"a": "X"}


def test_sso_link_drops_unchanged_temporary_password(sso: TestClient) -> None:
    """Vincular pelo e-mail um usuário que nunca trocou a senha temporária anula essa senha e não exige a troca."""
    client = anon(client_for(create_app()))
    store = client.app.state.auth  # type: ignore[attr-defined]
    asyncio.run(store.create_user("ana@contoso.com", "Ana", ["Colaborador"], "Temporaria-123!", True))
    r = flow(client, sso)
    assert r.status_code == 303 and r.headers["location"] == "/applications"
    u = next(u for u in users(client) if u["email"] == "ana@contoso.com")
    assert u["must_change_password"] is False and u.get("password_hash") is None
    assert asyncio.run(store.authenticate("ana@contoso.com", "Temporaria-123!")) is None
