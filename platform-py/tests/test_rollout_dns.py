"""The candidate is created as `<name>__new` and renamed. Podman's DNS keeps the name a container was created
with (a rename does not update it): without an alias for the final name, the route answered 502 after a deploy
reported as done. Staging's first candidate copies production and must never take production's aliases."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from admin_helper import make_hub
from fastapi.testclient import TestClient
from test_f5b import FD, JSON, upload

from hub.ops import model as m
from hub.ops import rollout as ro

UNRESOLVED: set[str] = set()


def resolver(request: httpx.Request) -> httpx.Response:
    """Ready everywhere, except the hosts whose name does not resolve on the app's network."""
    if request.url.host in UNRESOLVED:
        raise httpx.ConnectError(f"[Errno -2] Name or service not known: {request.url.host}")
    return httpx.Response(200 if request.url.path.endswith("/_hub/ready") else 404)


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("HUB_ROLLOUT_DRAIN_S", "0")
    monkeypatch.setenv("HUB_ROLLOUT_INTERVAL_S", "0.01")
    monkeypatch.setenv("HUB_ROLLOUT_TIMEOUT_S", "0.3")
    monkeypatch.setenv("HUB_SCRIPTS_ALLOW_SELF_APPROVAL", "1")
    UNRESOLVED.clear()
    with make_hub(tmp_path, monkeypatch, resolver) as c:
        yield c


@pytest.fixture
def fake(hub: TestClient) -> FD:
    fd = FD()
    hub.app.state.ops_engine.factory = lambda: fd  # type: ignore[attr-defined]
    return fd


def test_candidate_answers_to_its_final_name_and_staging_never_takes_productions_aliases() -> None:
    prod = {"Name": "/app-hub-app-inventory", "Id": "0123456789abcdef",
            "NetworkSettings": {"Networks": {"net": {"Aliases": ["inventory-svc", "app-hub-app-inventory"]}}}}  # fmt: skip
    assert ro.candidate_aliases(prod, ["net"], "app-hub-app-inventory") == {
        "net": ["inventory-svc", "app-hub-app-inventory"]
    }
    assert ro.candidate_aliases(prod, ["net"], "app-hub-app-inventory__staging") == {
        "net": ["app-hub-app-inventory__staging"]
    }
    assert ro.candidate_aliases({}, ["default", "bridge"], "x") == {}  # Docker refuses aliases there


def test_the_candidate_is_created_with_the_final_name_as_alias(hub: TestClient, fake: FD) -> None:
    r = hub.post(
        f"/api/ops/deployments/{upload(hub).json()['result']['deployment']['id']}/approve?wait=true",
        headers=JSON,
    )
    assert r.json()["status"] == m.IMPLANTADO, r.text
    create = next(c for c in fake.calls if c[0] == "create")
    assert create[3]["networking_config"]["EndpointsConfig"]["app-hub_default"]["Aliases"] == [
        "app-hub-app-inventory"
    ]


def test_a_final_name_the_hub_cannot_reach_fails_the_deploy_with_the_cause(hub: TestClient, fake: FD) -> None:
    UNRESOLVED.add("app-hub-app-inventory")
    dep = upload(hub).json()["result"]["deployment"]
    r = hub.post(f"/api/ops/deployments/{dep['id']}/approve?wait=true", headers=JSON).json()
    assert r["status"] == m.FALHOU and "app-hub-app-inventory" in r["note"] and "DNS" in r["note"], r
    reg = hub.app.state.gateway.registry  # type: ignore[attr-defined]
    assert (
        reg.get("inventory").upstream == "http://app-hub-app-inventory__new:8080"
    )  # the route that still answers
