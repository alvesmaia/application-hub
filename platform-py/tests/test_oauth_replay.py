"""Authorization-code replay: Microsoft 365 Copilot (Teams platform) redeems the same code twice within seconds.

A confidential client that authenticates with its secret, presents the same redirect_uri and a valid PKCE
verifier, and repeats within the grace window gets a new token pair for the same grant; any other replay
still revokes the grant (OAuth 2.1 §4.1.3)."""

from pathlib import Path

import pytest
from api_helper import bearer, rpc
from fastapi.testclient import TestClient
from test_oauth import ISS, exchange, grant_code, pkce, register

from hub.api import oauth_tokens


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from api_helper import api_hub

    return api_hub(tmp_path, monkeypatch, env={"HUB_PUBLIC_URL": ISS})


def confidential_code(hub: TestClient) -> tuple[str, str, str, str]:
    cl = register(hub, method="client_secret_post")
    v, ch = pkce()
    cid, secret = str(cl["client_id"]), str(cl["client_secret"])
    return cid, secret, grant_code(hub, cid, ch), v


def test_confidential_client_retry_within_grace_gets_new_tokens(hub: TestClient) -> None:
    cid, secret, code, v = confidential_code(hub)
    first = exchange(hub, cid, code, v, client_secret=secret)
    retry = exchange(hub, cid, code, v, client_secret=secret)
    assert first.status_code == retry.status_code == 200, retry.text
    assert retry.json()["access_token"] != first.json()["access_token"]
    for t in (first, retry):  # the first pair stays valid: the grant was not revoked
        assert rpc(bearer(hub, t.json()["access_token"]), "ping").status_code == 200
    third = exchange(hub, cid, code, v, client_secret=secret)  # only one retry is tolerated
    assert third.status_code == 400
    assert rpc(bearer(hub, first.json()["access_token"]), "ping").status_code == 401


def test_retry_after_the_grace_window_revokes(hub: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    cid, secret, code, v = confidential_code(hub)
    first = exchange(hub, cid, code, v, client_secret=secret)
    monkeypatch.setattr(oauth_tokens, "CODE_RETRY_GRACE_MS", -1)
    assert exchange(hub, cid, code, v, client_secret=secret).status_code == 400
    assert rpc(bearer(hub, first.json()["access_token"]), "ping").status_code == 401


def test_retry_with_wrong_pkce_or_public_client_revokes(hub: TestClient) -> None:
    cid, secret, code, v = confidential_code(hub)
    first = exchange(hub, cid, code, v, client_secret=secret)
    assert exchange(hub, cid, code, "x" * 50, client_secret=secret).status_code == 400
    assert rpc(bearer(hub, first.json()["access_token"]), "ping").status_code == 401
    pub = register(hub)["client_id"]  # public client (no secret): no grace
    pv, pch = pkce()
    pcode = grant_code(hub, pub, pch)
    pfirst = exchange(hub, pub, pcode, pv)
    assert exchange(hub, pub, pcode, pv).status_code == 400
    assert rpc(bearer(hub, pfirst.json()["access_token"]), "ping").status_code == 401
