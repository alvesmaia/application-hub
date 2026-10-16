"""Task 30: publishing a `.hubapp` through MCP with a one-time upload link (`deployments_upload`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from api_helper import add_user, api_hub, bearer, make_token, rpc
from fastapi.testclient import TestClient
from test_oauth import ISS, exchange, grant_code, pkce, register
from test_ops import package

from hub.api import mcp_uploads
from hub.ops import model as m


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return api_hub(tmp_path, monkeypatch, env={"HUB_PUBLIC_URL": ISS})


@pytest.fixture
def agent(hub: TestClient) -> TestClient:
    return bearer(hub, make_token(hub, ["mcp", "api:write"]))


def call(c: TestClient, name: str, args: dict[str, Any]) -> dict[str, Any]:
    r = rpc(c, "tools/call", {"name": name, "arguments": args})
    assert r.status_code == 200 and "error" not in r.json(), r.text
    return json.loads(r.json()["result"]["content"][0]["text"])  # type: ignore[no-any-return]


def link(c: TestClient, env: str = "staging", **extra: str) -> dict[str, Any]:
    return call(c, "deployments_upload", {"env": env, **extra})


def send(hub: TestClient, url: str, data: bytes | None = None, wait: bool = True) -> httpx.Response:
    anon = TestClient(hub.app, follow_redirects=False)
    path = url.removeprefix(ISS) + ("?wait=true" if wait else "")
    files = {"package": ("app.hubapp", data if data is not None else package())}
    return cast(httpx.Response, anon.post(path, files=files))


def names(c: TestClient) -> set[str]:
    return {t["name"] for t in rpc(c, "tools/list").json()["result"]["tools"]}


def test_link_shape_and_staging_upload_creates_the_job_and_the_deployment(
    hub: TestClient, agent: TestClient
) -> None:
    out = link(agent, commit="abcdef1")
    assert out["upload_url"].startswith(f"{ISS}/mcp/uploads/") and out["method"] == "POST"
    assert out["field"] == "package" and out["max_mb"] == 20 and out["expires_at"] > 0
    assert out["upload_url"] in out["curl"] and "-F package=@" in out["curl"]
    assert "jobs_get" in out["next"] and "deployments_approve" in out["next"]
    r = send(hub, out["upload_url"])
    job = r.json()
    assert r.status_code in (201, 422) and job["kind"] == "deploy", r.text
    dep = job["result"]["deployment"]
    assert (dep["app"], dep["env"], dep["commit"]) == ("novoapp", "Staging", "abcdef1")
    assert dep["submitted_by"] == "Administrador"
    assert any(d["id"] == dep["id"] for d in hub.get("/api/ops/deployments").json())


def test_production_upload_waits_for_approval(hub: TestClient, agent: TestClient) -> None:
    r = send(hub, link(agent, "producao")["upload_url"])
    assert r.status_code == 201, r.text
    assert r.json()["result"]["deployment"]["status"] == m.AGUARDANDO


def test_async_upload_answers_202_with_the_job(hub: TestClient, agent: TestClient) -> None:
    r = send(hub, link(agent, "producao")["upload_url"], wait=False)
    assert r.status_code == 202 and r.json()["kind"] == "deploy"


def test_ticket_is_single_use(hub: TestClient, agent: TestClient) -> None:
    url = link(agent, "producao")["upload_url"]
    assert send(hub, url).status_code == 201
    again = send(hub, url)
    assert again.status_code == 404 and "inválido, vencido ou já usado" in again.json()["error"]


def test_unknown_and_expired_tickets_are_refused(
    hub: TestClient, agent: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hub.api import records

    assert send(hub, f"{ISS}/mcp/uploads/nao-existe").status_code == 404
    url = link(agent, "producao")["upload_url"]
    later = records._now() + 11 * 60_000
    monkeypatch.setattr(records, "_now", lambda: later)
    assert send(hub, url).status_code == 404


def test_owner_who_lost_the_permission_cannot_use_the_ticket(hub: TestClient) -> None:
    lead = add_user(hub, "lead", ["Tech Lead"])
    url = link(bearer(hub, make_token(lead, ["mcp", "api:write"])), "producao")["upload_url"]
    assert hub.put("/api/admin/users/lead", json={"roles": ["Colaborador"]}).status_code == 200
    r = send(hub, url)
    assert r.status_code == 403 and hub.get("/api/ops/deployments").json() == []


def test_deactivated_owner_cannot_use_the_ticket(hub: TestClient) -> None:
    lead = add_user(hub, "lead", ["Tech Lead"])
    url = link(bearer(hub, make_token(lead, ["mcp", "api:write"])), "producao")["upload_url"]
    assert hub.put("/api/admin/users/lead", json={"active": False}).status_code == 200
    assert send(hub, url).status_code == 403


def test_app_scoped_token_only_uploads_its_apps(hub: TestClient) -> None:
    c = bearer(hub, make_token(hub, ["mcp", "api:write"], ["ativos"]))
    r = send(hub, link(c, "producao")["upload_url"])
    assert r.status_code == 403 and "novoapp" in r.json()["error"]


def test_oversized_and_non_multipart_bodies_are_refused_without_spending_the_ticket(
    hub: TestClient, agent: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = link(agent, "producao")["upload_url"]
    anon = TestClient(hub.app, follow_redirects=False)
    path = url.removeprefix(ISS)
    assert anon.post(path, content=b"{}", headers={"content-type": "application/json"}).status_code == 415
    monkeypatch.setattr(mcp_uploads, "MAX_BODY", 1000)
    assert send(hub, url, b"x" * 2000).status_code == 413
    monkeypatch.undo()
    assert send(hub, url).status_code == 201  # still valid


def test_invalid_arguments_are_refused(agent: TestClient) -> None:
    r = rpc(
        agent, "tools/call", {"name": "deployments_upload", "arguments": {"env": "staging", "commit": "zz"}}
    )
    assert r.json()["result"]["isError"] is True
    r = rpc(agent, "tools/call", {"name": "deployments_upload", "arguments": {}})
    assert r.json()["result"]["isError"] is True


def test_ticket_never_reaches_audit_or_logs(hub: TestClient, agent: TestClient) -> None:
    out = link(agent, "producao")
    ticket = out["upload_url"].rsplit("/", 1)[1]
    assert send(hub, out["upload_url"]).status_code == 201
    audit = json.dumps(hub.get("/api/admin/audit?limit=200").json(), ensure_ascii=False)
    logs = json.dumps(hub.get("/api/admin/logs").json(), ensure_ascii=False)
    assert "Link de envio emitido" in audit and ticket not in audit and ticket not in logs
    assert ticket not in json.dumps(list(hub.app.state.records.mem), ensure_ascii=False)


def test_tool_follows_rbac_and_the_write_scope(hub: TestClient, agent: TestClient) -> None:
    assert "deployments_upload" in names(agent)
    colab = add_user(hub, "colab", ["Colaborador"])
    assert "deployments_upload" not in names(bearer(hub, make_token(colab, ["mcp", "api:write"])))
    assert "deployments_upload" not in names(bearer(hub, make_token(hub, ["mcp"])))


def test_oauth_token_without_api_write_cannot_call_it(hub: TestClient) -> None:
    cl = register(hub)
    v, ch = pkce()
    code = grant_code(hub, cl["client_id"], ch, scopes=("mcp",))
    tok = exchange(hub, cl["client_id"], code, v).json()["access_token"]
    c = bearer(hub, tok)
    assert "deployments_upload" not in names(c)
    r = rpc(c, "tools/call", {"name": "deployments_upload", "arguments": {"env": "staging"}})
    assert r.json()["error"]["code"] == -32602


def test_upload_keeps_the_control_plane_network_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hub: TestClient, agent: TestClient
) -> None:
    url = link(agent, "producao")["upload_url"]
    monkeypatch.setenv("HUB_ADMIN_ALLOWED_CIDRS", "10.0.0.0/8")
    assert send(hub, url).status_code == 403  # outside the admin network and MCP is not public
    monkeypatch.delenv("HUB_ADMIN_ALLOWED_CIDRS")
    public = api_hub(
        tmp_path / "p", monkeypatch, env={"HUB_PUBLIC_URL": ISS, "HUB_MCP_PUBLIC_NETWORK": "true"}
    )
    out = link(bearer(public, make_token(public, ["mcp", "api:write"])), "producao")
    monkeypatch.setenv("HUB_ADMIN_ALLOWED_CIDRS", "10.0.0.0/8")
    assert send(public, out["upload_url"]).status_code == 201
