import json
import time
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import Request

from hub.auth import (
    AuthStore,
    _cidrs,
    _user_login,
    client_ip,
    control_allowed,
    normalize_email,
    owned_surface,
    permissions,
    policy,
    roles_for,
    token_action,
    token_covers,
    verify,
)


def test_auth_helpers(tmp_path, monkeypatch):
    assert policy("weak") and policy("loweronlypassword") and policy("SameLenPass12!\n")
    assert policy("Abcdefgh1!") is None
    assert not verify("Abcdefgh1!", None)
    assert not verify("wrong", "$invalid")
    assert normalize_email(" A@corp.example ") == "a@corp.example"
    for bad in ("", "x@localhost", "x@.example", "a @x.com", "<x>@a.com"):
        with pytest.raises(ValueError):
            normalize_email(bad)
    users = [{"email": "a@demo.local"}, {"email": "a@corp.test"}]
    assert _user_login(users, "a") is users[0]
    assert _user_login(users, " A@CORP.TEST ") is users[1]
    assert _user_login(users, "missing") is None
    assert permissions({"roles": ["missing"]}, [])[0] == []
    assert permissions({"roles": ["reader"]}, [{"name": "reader", "permissions": [True], "apps": ["x"]}]) == (
        ["acessar_aplicacoes"],
        ["x"],
    )
    assert roles_for(tmp_path)[0]["name"] == "Administrador"
    (tmp_path / "admin.json").write_text(json.dumps({"roles": []}), encoding="utf-8")
    assert roles_for(tmp_path)[0]["name"] == "Administrador"
    (tmp_path / "admin.json").write_text("{", encoding="utf-8")
    assert roles_for(tmp_path)[0]["name"] == "Administrador"
    (tmp_path / "admin.json").write_text(json.dumps({"roles": [{"name": "Ops"}]}), encoding="utf-8")
    assert roles_for(tmp_path) == [{"name": "Ops", "permissions": []}]
    assert _cidrs("broken") == []
    monkeypatch.setenv("HUB_ADMIN_ALLOWED_CIDRS", "10.0.0.0/8")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admin",
        "headers": [],
        "client": ("192.0.2.1", 1),
        "server": ("test", 80),
        "scheme": "http",
        "query_string": b"",
    }
    request = Request(scope)
    assert not control_allowed(request)
    assert client_ip(request) == "192.0.2.1"
    assert control_allowed(Request(scope | {"path": "/"}))
    # coming from the edge (HUB_EDGE_CIDR), the X-Forwarded-For it forwards is trusted; from outside, the header is ignored
    monkeypatch.setenv("HUB_EDGE_CIDR", "10.231.250.0/24")
    edge = scope | {"client": ("10.231.250.2", 1), "headers": [(b"x-forwarded-for", b"10.1.2.3")]}
    assert client_ip(Request(edge)) == "10.1.2.3" and control_allowed(Request(edge))
    assert client_ip(Request(scope | {"headers": [(b"x-forwarded-for", b"10.1.2.3")]})) == "192.0.2.1"


def test_token_helpers():
    assert owned_surface("GET", "/api/v1/items") == "api:read"
    assert owned_surface("POST", "/api/v1/items") == "api:write"
    assert owned_surface("GET", "/mcp") == "mcp"
    assert owned_surface("GET", "/apps/a/api/x") == "app-api"
    assert owned_surface("GET", "/elsewhere") is None
    assert token_covers({"actions": ["api:write"]}, "api:read")
    assert not token_covers({"actions": ["mcp"]}, "mcp")
    assert token_covers({"owner": "u", "actions": ["mcp"]}, "mcp")
    assert token_action("POST", "/api/ops/packages") == "upload"
    assert token_action("GET", "/api/ops/jobs/123") == "status"
    assert token_action("POST", "/api/ops/jobs/123") is None


@pytest.mark.asyncio
async def test_auth_store_lifecycle(tmp_path, monkeypatch):
    monkeypatch.delenv("HUB_DEMO_USERS", raising=False)
    monkeypatch.setenv("HUB_BOOTSTRAP_ADMIN_PASSWORD", "ValidPassword42!")
    store = AuthStore(tmp_path / "auth.json")
    await store.bootstrap()
    with pytest.raises(ValueError):
        await store.create_user("nope", "N", ["Reader"], "ValidPassword42!")
    user = await store.create_user("new@corp.test", " New ", [" Reader ", "Reader"], "ValidPassword42!")
    assert user["roles"] == ["Reader"]
    with pytest.raises(ValueError):
        await store.create_user("new@corp.test", "Again", ["Reader"], "ValidPassword42!")
    with pytest.raises(ValueError):
        await store.create_user("u@corp.test", "N", [], "ValidPassword42!")
    with pytest.raises(LookupError):
        await store.update_user("absent", active=False)
    with pytest.raises(ValueError):
        await store.update_user(user["id"], name=" ")
    user = await store.update_user(user["id"], active=False)
    assert not user["active"]
    user = await store.update_user(user["id"], active=True, name="User")
    with pytest.raises(ValueError):
        await store.update_user(user["id"], roles=[])
    with pytest.raises(ValueError):
        await store.reset_password(user["id"], "short")
    reset = await store.reset_password(user["id"], "AnotherPassword3!")
    assert reset["must_change_password"]
    assert await store.change_password(reset, "AnotherPassword3!", "ChangedPassword4!")
    with pytest.raises(ValueError):
        await store.change_password(reset, "AnotherPassword3!", "AnotherPassword3!")
    secret, _ = await store.create_token(user, "x", [], ["api:read"])
    assert await store.bearer(secret)
    assert not await store.bearer("invalid")
    with pytest.raises(ValueError):
        await store.create_token(user, "x", [], ["secrets:reveal"])
    with pytest.raises(ValueError):
        await store.create_token(user, "x", [], ["unknown"])
    with pytest.raises(ValueError):
        await store.create_token(user, "x", [], ["api:read"], days=400)
    data = json.loads(store.path.read_text(encoding="utf-8"))
    data["tokens"][0]["revoked_at"] = time.time_ns() // 1_000_000
    store.path.write_text(json.dumps(data), encoding="utf-8")
    assert await store.bearer(secret) is None


async def test_auth_file_is_written_while_the_cross_replica_lock_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # writing after file_lock was released let another replica read the old file and overwrite it (lost update)
    import contextlib

    import hub.auth as auth_mod

    held = {"now": False, "at_write": None}

    class FakeDb:
        @contextlib.asynccontextmanager
        async def file_lock(self, name: str, write: bool = False):  # type: ignore[no-untyped-def]
            held["now"] = True
            try:
                yield
            finally:
                held["now"] = False

    real = auth_mod.write_auth_file

    def spy(path: Path, data: dict[str, object], db: bool) -> None:
        held["at_write"] = held["now"]
        real(path, data, db)

    monkeypatch.setattr(auth_mod, "write_auth_file", spy)
    store = auth_mod.AuthStore(tmp_path / "auth.json", cast(Any, FakeDb()))
    async with store.locked(True) as d:
        d["next_id"] = 7
    assert held["at_write"] is True
