"""Signing in again after leaving: a POST that met an ended session (e.g. "Sair" in a second tab) must not come
back as `next` — the login then sent the browser to GET /logout, which only accepts POST ("Method Not Allowed")."""

from __future__ import annotations

from pathlib import Path

import pytest
from admin_helper import make_hub
from fastapi.testclient import TestClient


@pytest.fixture
def anon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    hub = make_hub(tmp_path, monkeypatch)
    hub.headers.pop("cookie", None)
    hub.cookies.clear()
    return hub


def test_post_without_session_goes_to_login_without_next(anon: TestClient) -> None:
    r = anon.post("/logout")
    assert r.status_code == 303
    assert "next=" not in r.headers["location"], r.headers["location"]


def test_get_without_session_keeps_next(anon: TestClient) -> None:
    assert anon.get("/admin/users").headers["location"] == "/login?next=/admin/users"


def test_login_never_lands_on_logout(anon: TestClient) -> None:
    r = anon.post(
        "/login", data={"email": "admin@demo.local", "password": "test-password", "next": "/logout"}
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_safe_next_never_returns_logout() -> None:
    from hub.ux.account_routes import safe_next

    assert safe_next("/logout") == "/" and safe_next("/logout?x=1") == "/"
    assert safe_next("/admin") == "/admin"
