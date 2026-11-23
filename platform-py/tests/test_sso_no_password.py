"""Entra ID accounts have no hub password: Administration offers no "Reset password" for them (screen and API) and
the person sees no "Change password" in their account."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from admin_helper import add_user, make_hub
from fastapi.testclient import TestClient


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return make_hub(tmp_path, monkeypatch)


def _row(page: str, user_id: str) -> str:
    m = re.search(rf'<tr role="row"[^>]*data-user-id="{user_id}".*?</tr>', page, re.S)
    assert m, user_id
    return m.group(0)


def test_users_screen_hides_reset_for_entra_accounts(hub: TestClient) -> None:
    add_user(hub, "ana", ["Colaborador"], sso=True)
    add_user(hub, "bia", ["Colaborador"])
    page = hub.get("/admin/users").text
    assert "data-user-reset" not in _row(page, "ana")
    assert 'data-user-reset="bia"' in _row(page, "bia")


def test_api_refuses_reset_for_entra_accounts(hub: TestClient) -> None:
    add_user(hub, "ana", ["Colaborador"], sso=True)
    r = hub.post("/api/admin/users/ana/reset-password", json={"password": "outra-senha-forte-1"})
    assert r.status_code == 409, r.text
    assert "Entra ID" in r.json()["error"]


def test_entra_account_has_no_change_password(hub: TestClient) -> None:
    ana = add_user(hub, "ana", ["Colaborador"], sso=True)
    assert "data-acct-password" not in ana.get("/account/security").text
    assert ana.get("/account/password").status_code == 404
    bia = add_user(hub, "bia", ["Colaborador"])
    assert "data-acct-password" in bia.get("/account/security").text
