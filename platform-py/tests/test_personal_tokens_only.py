"""Credentials belong to a person: no platform-wide token page; a deploy token is a personal token with the
deploy actions, limited by its owner's permissions; old owner-less CI tokens are handed to whoever created them;
the administration cuts every access of a person in one step."""

import asyncio
import json
from pathlib import Path

import pytest
from admin_helper import add_user, make_hub
from api_helper import bearer, make_token
from fastapi.testclient import TestClient

from hub.auth import AuthStore


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return make_hub(tmp_path, monkeypatch)


def test_no_platform_token_page_or_api(hub: TestClient) -> None:
    old = hub.get("/admin/tokens", follow_redirects=False)
    assert old.status_code == 308 and old.headers["location"] == "/account/access"
    assert hub.get("/api/admin/tokens").status_code == 404
    assert hub.post("/api/admin/tokens", json={"name": "ci", "actions": ["upload"]}).status_code in (404, 405)
    assert 'href="/admin/tokens"' not in hub.get("/admin/users").text


def test_deploy_token_is_personal_and_follows_the_owner(hub: TestClient) -> None:
    ci = bearer(hub, make_token(hub, ["upload", "status"], name="pipeline"))
    assert ci.get("/api/ops/deployments").status_code == 200
    mine = hub.get("/api/me/tokens").json()["items"]
    assert [t["owner"] for t in mine if t["name"] == "pipeline"] == ["u-test"]
    reader = add_user(hub, "leitor", ["User"])
    theirs = bearer(hub, make_token(reader, ["upload", "status"]))
    assert theirs.get("/api/ops/deployments").status_code == 403  # the owner cannot deploy
    page = hub.get("/account/access").text
    assert 'value="upload"' in page and 'value="status"' in page


def test_owner_less_tokens_go_to_their_creator_or_are_revoked(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    base = {"roles": ["User"], "active": True, "must_change_password": False, "created_at": 1, "updated_at": 1,
            "last_login": None, "failed_attempts": 0, "locked_until": None}  # fmt: skip
    users = [base | {"id": "u1", "email": "ana@x", "name": "Ana"}, base | {"id": "u2", "email": "bia@x", "name": "Bia"},
             base | {"id": "u3", "email": "bia2@x", "name": "Bia"}]  # fmt: skip
    tok = {"hash": "h", "prefix": "hubt_x", "apps": [], "envs": [], "actions": ["upload"], "created_at": 1,
           "expires_at": 10**15, "revoked_at": None, "last_used": None, "owner": None}  # fmt: skip
    tokens = [tok | {"id": "t1", "name": "a", "created_by": "Ana"}, tok | {"id": "t2", "name": "b", "created_by": "Bia"},
              tok | {"id": "t3", "name": "c", "created_by": "Gone"}]  # fmt: skip
    path.write_text(
        json.dumps({"users": users, "sessions": [], "tokens": tokens, "next_id": 9}), encoding="utf-8"
    )
    store = AuthStore(path)
    asyncio.run(store.bootstrap())
    after = {t["id"]: t for t in json.loads(path.read_text(encoding="utf-8"))["tokens"]}
    assert after["t1"]["owner"] == "u1" and after["t1"]["revoked_at"] is None
    assert (
        after["t2"].get("owner") is None and after["t2"]["revoked_at"]
    )  # two people named Bia: nobody guesses
    assert after["t3"]["revoked_at"]


def test_admin_revokes_every_access_of_a_person(hub: TestClient) -> None:
    bia = add_user(hub, "bia", ["Administrador"])
    tok = make_token(bia, ["api:read"])
    assert bearer(hub, tok).get("/api/v1/apps").status_code == 200
    r = hub.post("/api/admin/users/bia/revoke-access", json={})
    assert r.status_code == 200 and r.json() == {"tokens": 1, "apps": 0}
    assert bearer(hub, tok).get("/api/v1/apps").status_code == 401
    assert bia.get("/api/me/tokens").status_code == 401  # the session ended too
    titles = [e["title"] for e in hub.get("/api/v1/audit?limit=10").json()["items"]]
    assert "Acessos do usuário revogados" in titles
    menu = hub.get("/admin/users").text.split('data-user-id="bia"')[1].split("</tr>")[0]
    assert 'data-user-revoke-access="bia"' in menu
    leitor = add_user(hub, "leitor2", ["User"])
    assert leitor.post("/api/admin/users/bia/revoke-access", json={}).status_code == 403
