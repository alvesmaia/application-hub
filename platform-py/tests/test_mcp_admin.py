"""Task 31: every API write action (and the useful reads) as MCP tools, with RBAC, confirmations and no secrets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from api_helper import add_user, api_hub, bearer, make_token, rpc
from fastapi.testclient import TestClient

from hub.api.ops_admin import ADMIN_OPS
from hub.api.v1_authz import need_of

NEW = [o for o in ADMIN_OPS if o.mcp]
SECRET_CONN = "S3cr3t-da-conexao-998877"
SECRET_VAR = "segredo-da-variavel-112233"


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HUB_STORAGE_ALLOW_HOSTS", "objstore")
    return api_hub(tmp_path, monkeypatch)


@pytest.fixture
def agent(hub: TestClient) -> TestClient:
    return bearer(hub, make_token(hub, ["mcp", "api:write"]))


@pytest.fixture
def colab(hub: TestClient) -> TestClient:
    return bearer(hub, make_token(add_user(hub, "colab", ["Colaborador"]), ["mcp", "api:write"]))


def tools(c: TestClient) -> dict[str, dict[str, Any]]:
    return {t["name"]: t for t in rpc(c, "tools/list").json()["result"]["tools"]}


def raw_call(c: TestClient, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    r = rpc(c, "tools/call", {"name": name, "arguments": args or {}})
    assert r.status_code == 200 and "error" not in r.json(), r.text
    return r.json()["result"]  # type: ignore[no-any-return]


def call(c: TestClient, name: str, args: dict[str, Any] | None = None) -> Any:
    """Runs the tool, confirming it when destructive (the confirm_token round-trip); fails on a tool error."""
    res = raw_call(c, name, args)
    out = json.loads(res["content"][0]["text"])
    if isinstance(out, dict) and out.get("confirmation_required"):
        res = raw_call(c, name, {**(args or {}), "confirm_token": out["confirm_token"]})
        out = json.loads(res["content"][0]["text"])
    assert res["isError"] is False, (name, out)
    return out


def error_of(c: TestClient, name: str, args: dict[str, Any]) -> dict[str, Any]:
    res = raw_call(c, name, args)
    out = json.loads(res["content"][0]["text"])
    if isinstance(out, dict) and out.get("confirmation_required"):
        res = raw_call(c, name, {**args, "confirm_token": out["confirm_token"]})
        out = json.loads(res["content"][0]["text"])
    assert res["isError"] is True, (name, out)
    return out  # type: ignore[no-any-return]


def test_every_new_op_is_a_tool_for_the_admin_and_refused_without_the_permission(
    agent: TestClient, colab: TestClient
) -> None:
    admin, mine = tools(agent), tools(colab)
    for op in NEW:
        assert op.id in admin, op.id
        if not need_of(op) or "acessar_aplicacoes" in need_of(op):
            assert op.id in mine, op.id  # personal actions (own Inbox, access requests)
            continue
        assert op.id not in mine, op.id
        r = rpc(colab, "tools/call", {"name": op.id, "arguments": {}})
        assert r.json()["error"]["code"] == -32602, op.id


def test_users_and_roles(hub: TestClient, agent: TestClient) -> None:
    body = {
        "email": "ana@demo.local",
        "name": "Ana",
        "roles": ["Colaborador"],
        "password": "senha-forte-12345",
    }
    u = call(agent, "users_create", {"body": body})
    assert u["email"] == "ana@demo.local" and body["password"] not in json.dumps(u)
    assert tools(agent)["users_update"]["annotations"]["destructiveHint"] is True
    assert call(agent, "users_update", {"id": u["id"], "body": {"name": "Ana B."}})["name"] == "Ana B."
    assert call(agent, "users_unlock", {"id": u["id"]})["locked"] is False
    role = call(agent, "roles_create", {"body": {"name": "Suporte N1", "level": "Padrão"}})
    assert role["name"] == "Suporte N1"
    assert call(agent, "roles_update", {"name": "Suporte N1", "body": {"description": "N1", "level": "Alto"}})
    perms = call(agent, "roles_set_permission", {"name": "Suporte N1", "index": 3, "body": {"granted": True}})
    assert perms["permissions"][3] is True
    assert call(agent, "roles_set_apps", {"name": "Suporte N1", "body": {"apps": ["ativos"]}})["apps"] == [
        "ativos"
    ]
    assert "Suporte N1" in [r["name"] for r in call(agent, "roles_list")["items"]]
    call(agent, "roles_delete", {"name": "Suporte N1"})
    assert call(agent, "users_update", {"id": u["id"], "body": {"active": False}})["active"] is False
    call(agent, "users_delete", {"id": u["id"]})
    assert u["id"] not in [x["id"] for x in hub.get("/api/admin/users").json()]
    titles = [e["title"] for e in hub.get("/api/admin/audit?limit=100").json()]
    assert "MCP: users_delete" in titles and "Usuário excluído" in titles


def test_destructive_tool_needs_the_confirmation_round_trip(hub: TestClient, agent: TestClient) -> None:
    call(agent, "roles_create", {"body": {"name": "Temporaria", "level": "Padrão"}})
    first = json.loads(raw_call(agent, "roles_delete", {"name": "Temporaria"})["content"][0]["text"])
    assert first["confirmation_required"] is True
    assert "Temporaria" in [r["name"] for r in hub.get("/api/admin/roles").json()]  # nothing ran yet
    raw_call(agent, "roles_delete", {"name": "Temporaria", "confirm_token": first["confirm_token"]})
    assert "Temporaria" not in [r["name"] for r in hub.get("/api/admin/roles").json()]


def test_settings_and_tokens(hub: TestClient) -> None:
    secret = make_token(hub, ["mcp", "api:write"])
    agent = bearer(hub, secret)
    s = call(agent, "settings_get")
    out = call(agent, "settings_update", {"body": {**s, "platform_name": "Hub da Empresa"}})
    assert out["platform_name"] == "Hub da Empresa"
    items = call(agent, "tokens_list")["items"]
    assert items and secret not in json.dumps(items) and all("hash" not in t for t in items)
    tid = items[0]["id"]
    assert call(agent, "tokens_revoke", {"id": tid})["active"] is False


def test_health_slos_incidents_flags_and_analytics(agent: TestClient) -> None:
    svc = {"name": "Fila de Pedidos", "kind": "Serviço", "status": "Disponível"}
    assert call(agent, "health_create", {"body": svc})["name"] == "Fila de Pedidos"
    assert call(
        agent,
        "health_update",
        {"name": "Fila de Pedidos", "body": {"kind": "Serviço", "status": "Manutenção"}},
    )
    assert "results" in call(agent, "health_check")
    call(agent, "health_delete", {"name": "Fila de Pedidos"})
    slo = {"app": "ativos", "env": "producao", "availability": 99.5, "latency_ms": 500, "window_days": 30}
    assert call(agent, "slos_set", {"body": slo})["availability"] == 99.5
    assert call(agent, "slos_list")["total"] == 1
    call(agent, "slos_delete", {"app": "ativos", "env": "producao"})
    inc = call(
        agent, "incidents_create", {"body": {"app": "ativos", "title": "Lentidão", "severity": "minor"}}
    )
    assert call(agent, "incidents_update", {"id": inc["id"], "body": {"resolve": True}})["resolved"]
    assert call(agent, "incidents_list")["total"] == 1
    flag = {"key": "novo-fluxo", "app": "ativos", "envs": {"producao": {"enabled": False, "default": "off"}}}
    assert call(agent, "flags_create", {"body": flag})["key"] == "novo-fluxo"
    on = call(agent, "flags_set_enabled", {"app": "ativos", "key": "novo-fluxo", "env": "producao",
                                           "body": {"enabled": True}})  # fmt: skip
    assert on["envs"]["producao"]["enabled"] is True
    assert call(agent, "flags_update", {"app": "ativos", "key": "novo-fluxo", "body": {"description": "x"}})
    assert call(agent, "flags_list")["total"] == 1
    call(agent, "flags_delete", {"app": "ativos", "key": "novo-fluxo"})
    assert "retention_days" in call(agent, "analytics_get")
    assert call(agent, "analytics_set_terms", {"body": {"raw": False}})["raw"] is False
    assert call(agent, "analytics_set_retention", {"body": {"days": 90}})["days"] == 90
    assert call(agent, "analytics_rotate_key")["ok"] is True
    assert "subscribers" in call(agent, "logs_stats")


def test_variables_and_connections_never_hand_out_secrets(hub: TestClient, agent: TestClient) -> None:
    var = {"name": "API_SEGREDO", "value": SECRET_VAR, "kind": "secreta",
           "bindings": [{"app": "ativos", "env": "producao"}]}  # fmt: skip
    results = [call(agent, "variables_create", {"body": var})]
    results.append(
        call(
            agent,
            "variables_update",
            {"name": "API_SEGREDO", "body": {"value": "", "kind": "secreta", "bindings": var["bindings"]}},
        )
    )
    results.append(call(agent, "variables_list"))
    conn = {"name": "docs-s3", "kind": "s3", "endpoint": "http://objstore:9000", "bucket": "docs",
            "access_key": "AKEY", "secret": SECRET_CONN}  # fmt: skip
    results.append(call(agent, "connections_create", {"body": conn}))
    same = {
        k: v for k, v in conn.items() if k not in ("name", "secret")
    }  # empty secret keeps the current one
    results.append(
        call(agent, "connections_update", {"name": "docs-s3", "body": {**same, "description": "Docs"}})
    )
    bind = {"bindings": [{"app": "ativos", "env": "producao"}]}
    results.append(call(agent, "connections_set_bindings", {"name": "docs-s3", "body": bind}))
    results.append(
        call(agent, "connections_rotate", {"name": "docs-s3", "body": {"secret": SECRET_CONN + "-2"}})
    )
    results.append(call(agent, "connections_list"))
    results.append(call(agent, "connections_rotate_storage_token", {"app": "ativos", "env": "producao"}))
    results.append(call(agent, "connections_delete", {"name": "docs-s3"}))
    results.append(call(agent, "variables_delete", {"name": "API_SEGREDO"}))
    dump = json.dumps(results, ensure_ascii=False) + json.dumps(hub.get("/api/admin/audit?limit=200").json())
    assert SECRET_VAR not in dump and SECRET_CONN not in dump
    assert results[0]["value"] != SECRET_VAR and results[3]["secret"] != SECRET_CONN


def test_operations_area(hub: TestClient, agent: TestClient) -> None:
    lim = call(
        agent,
        "containers_set_limits",
        {"app": "ativos", "body": {"cpu": 0.5, "memory": "256Mi", "pids": 100}},
    )
    assert lim["limits"]["pids"] == 100
    pol = {"schedule": "diário 02:00", "keep": 7, "volumes": ["data"], "destination": "local"}
    assert call(agent, "backups_set_policy", {"app": "ativos", "body": pol})["keep"] == 7
    assert call(agent, "backups_aside_list")["aside"] == []
    assert isinstance(call(agent, "replicas_get"), dict)
    assert "não está em manutenção" in error_of(agent, "maintenance_clear", {"app": "ativos"})["detail"]
    assert error_of(agent, "backups_clone", {"id": "999"})["status"] == 404
    assert error_of(agent, "backups_aside_drop", {"name": "ativos_old"})["status"] in (404, 503)


def test_personal_inbox_actions(hub: TestClient, colab: TestClient) -> None:
    assert "marked" in call(colab, "inbox_mark_read", {"body": {}})
    assert "changed" in call(colab, "inbox_archive", {"body": {"ids": []}})
    assert "já tem acesso" in error_of(colab, "access_request", {"body": {"app": "ativos"}})["detail"]


def test_bodies_are_validated_at_the_edge(agent: TestClient) -> None:
    bad = error_of(agent, "variables_update", {"name": "X", "body": {"value": "v"}})  # kind/bindings required
    assert bad["status"] == 400 and "kind" in bad["detail"]


def test_free_text_names_in_the_path(hub: TestClient, agent: TestClient) -> None:
    body = {"description": "Dono do produto", "level": "Alto"}
    assert (
        call(agent, "roles_update", {"name": "Product Owner", "body": body})["description"]
        == "Dono do produto"
    )
    assert hub.put("/api/v1/roles/Product%20Owner", json=body).status_code == 200
    for bad in ("..", "a?b", "a%2Fb", "a\\b"):
        assert error_of(agent, "roles_update", {"name": bad, "body": body})["status"] == 400, bad
