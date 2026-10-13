import json

from auth_helper import SESSION, client_for
from fastapi.testclient import TestClient

from hub.app import create_app
from hub.auth import sha, verify


def test_login_username_rotates_session_then_logout() -> None:
    client = client_for(create_app())
    r = client.post(
        "/login",
        data={"email": "admin", "password": "test-password", "next": "/"},
        headers={"sec-fetch-site": "same-origin", "cookie": f"hub_session={SESSION}"},
    )
    assert r.status_code == 303
    cookie = r.cookies["hub_session"]
    assert cookie != SESSION
    client.headers["cookie"] = f"hub_session={cookie}"
    assert client.get("/").status_code == 200
    assert client.get("/", headers={"cookie": f"hub_session={SESSION}"}).status_code == 303
    assert (
        client.post(
            "/logout", headers={"sec-fetch-site": "same-origin", "cookie": f"hub_session={cookie}"}
        ).status_code
        == 303
    )
    assert client.get("/").status_code == 303


def test_login_failure_and_rate_limit() -> None:
    client = client_for(create_app())
    client.cookies.clear()
    for _ in range(5):
        r = client.post(
            "/login", data={"email": "admin", "password": "bad"}, headers={"sec-fetch-site": "same-origin"}
        )
        assert r.status_code == 401
    assert (
        client.post(
            "/login", data={"email": "admin", "password": "bad"}, headers={"sec-fetch-site": "same-origin"}
        ).status_code
        == 429
    )


def test_personal_bearer_token_scope_and_owner() -> None:
    client: TestClient = client_for(create_app())
    created = client.post(
        "/api/me/tokens",
        json={"name": "reader", "actions": ["api:read"]},
        headers={"sec-fetch-site": "same-origin", "cookie": f"hub_session={SESSION}"},
    )
    assert created.status_code == 201
    secret = created.json()["token"]
    assert secret.startswith("hubt_t")
    assert client.get("/api/v1/example", headers={"authorization": f"Bearer {secret}"}).status_code == 404
    assert client.get("/api/me/tokens", headers={"authorization": f"Bearer {secret}"}).status_code == 403
    assert client.get("/api/v1/example", headers={"authorization": "Bearer invalid"}).status_code == 401


def test_rbac_denial_renders_shell() -> None:
    client = client_for(create_app())
    client.app.state.roles = [
        {"name": "Leitor", "permissions": [True, False, False, False, False, False], "apps": []}
    ]
    path = client.app.state.auth.path
    data = json.loads(path.read_text(encoding="utf-8"))
    data["users"][0]["roles"] = ["Leitor"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert client.get("/").status_code == 200
    r = client.get("/admin/users")
    assert r.status_code == 403 and 'class="platform-rail"' in r.text and "Conectado como" in r.text


def test_legacy_password_and_secret_hash_vectors() -> None:
    legacy_phc = (
        "$argon2id$v=19$m=19456,t=2,p=1$3lQtv6poUwlEOEEZ9C/qzw$/+qjFZ9ssXJ2hSWwi8fLrEyp+OfdGfUlzoxaQ8RN7LI"
    )
    assert verify("Rust-Golden-Password-42!", legacy_phc)
    assert sha("hubt_t1deadbeef_secret") == "383021d9142e0e710b7ccdbe95d46e1acbace9af0418de4ae661fb0309d0a6ac"
    assert sha("session-cookie-golden") == "558aa802936fe8c2c46d96773cfac8f7342e6833c3ecf9e31c4abd7036c08f1f"
