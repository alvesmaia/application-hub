"""API pública `/api/v1`: autorização, escopos, erros RFC 9457, paginação, limite de taxa e contrato."""

from pathlib import Path
from typing import Any

import pytest
from api_helper import add_user, api_hub, bearer, make_token, rpc
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator

from hub.api import ops


def problem(r: Any, status: int, slug: str) -> dict[str, Any]:
    assert r.status_code == status, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    b = r.json()
    assert b["status"] == status and b["type"] == f"/api/v1/problems/{slug}"
    assert {"title", "detail", "instance"} <= b.keys()
    return b  # type: ignore[no-any-return]


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return api_hub(tmp_path, monkeypatch)


def test_table_is_consistent() -> None:
    ids = [o.id for o in ops.OPS]
    assert len(ids) == len(set(ids))
    for o in ops.OPS:
        for p in (p for p in o.params if p.loc == "path"):
            assert "{" + p.name + "}" in o.path
        if o.body and o.body != ops.MULTIPART:
            assert o.body in ops.sc.schemas()
        if o.target:
            assert o.target[0] == o.method  # método interno = método público


def test_routing_matches_templates_and_reports_405() -> None:
    hit = ops.route("POST", "/deployments/12/rollback")
    assert not isinstance(hit, bool)
    op, p = hit
    assert op.id == "deployments_rollback" and p["id"] == "12"
    apps = ops.route("GET", "/apps")
    assert not isinstance(apps, bool) and apps[0].id == "apps_list"
    assert ops.route("DELETE", "/apps") is True
    assert ops.route("GET", "/nada") is False


def test_unauthenticated_is_problem_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = TestClient(api_hub(tmp_path, monkeypatch).app, follow_redirects=False)
    problem(c.get("/api/v1/apps"), 401, "unauthorized")
    problem(c.get("/api/v1/openapi.json"), 401, "unauthorized")


def test_session_reads_openapi_docs_and_errors(hub: TestClient) -> None:
    doc = hub.get("/api/v1/openapi.json").json()
    assert doc == ops.openapi() and doc["openapi"] == "3.1.0"
    assert hub.get("/api/docs").headers["content-type"].startswith("text/html")
    problem(hub.get("/api/v1/nada"), 404, "not-found")
    problem(hub.delete("/api/v1/apps"), 405, "method-not-allowed")
    problem(hub.get("/api/v1/jobs/..%2Fadmin"), 404, "not-found")
    problem(hub.get("/api/v1/jobs/x y"), 400, "bad-request")
    problem(hub.get("/api/v1/jobs/9999"), 404, "not-found")  # id inexistente
    assert hub.post("/api/v1/inbox/1/decide", content="x").status_code == 415
    problem(
        hub.post("/api/v1/inbox/1/decide", content="{", headers={"content-type": "application/json"}),
        400,
        "bad-request",
    )


def test_apps_catalog_status_and_filters(hub: TestClient) -> None:
    r = hub.get("/api/v1/apps")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    page = r.json()
    assert page["total"] == 2 and page["next"] is None
    app = page["items"][0]
    assert app["id"] == "ativos" and app["stack"] == "python" and app["url"] == "/apps/ativos"
    assert app["api"]["base"] == "/apps/ativos/api/v1" and app["mcp"]["tool_prefix"] == "ativos."
    assert hub.get("/api/v1/apps?stack=go").json()["total"] == 0
    assert hub.get("/api/v1/apps?q=SERVICOS").json()["total"] == 1
    assert hub.get("/api/v1/apps/servicos").json()["id"] == "servicos"
    problem(hub.get("/api/v1/apps/nada"), 404, "not-found")
    st = hub.get("/api/v1/apps/ativos/status").json()
    assert st["app"] == "ativos" and st["open_incidents"] == [] and st["last_hour"]["requests"] == 0
    sp = hub.get("/api/v1/status").json()
    assert sp["overall"] == "ok" and [a["id"] for a in sp["apps"]] == ["ativos", "servicos"]
    obs = hub.get("/api/v1/observability/summary?minutes=30&app=ativos").json()
    assert obs["minutes"] == 30 and [i["app"] for i in obs["items"]] == ["ativos"]


def test_pagination_and_bad_numbers(hub: TestClient) -> None:
    p1 = hub.get("/api/v1/apps?limit=1").json()
    assert len(p1["items"]) == 1 and p1["total"] == 2 and p1["limit"] == 1
    assert p1["next"] == "/api/v1/apps?limit=1&offset=1"
    p2 = hub.get(p1["next"]).json()
    assert p2["items"][0]["id"] == "servicos" and p2["next"] is None
    problem(hub.get("/api/v1/apps?limit=x"), 400, "bad-request")
    assert hub.get("/api/v1/apps?limit=9999").json()["limit"] == 500


def test_token_scopes_and_owner_rbac(hub: TestClient) -> None:
    ro = bearer(hub, make_token(hub, ["api:read"]))
    assert ro.get("/api/v1/apps").status_code == 200
    # sem api:write a superfície de escrita nem aceita o token
    r = ro.post("/api/v1/containers/x/restart")
    problem(r, 401, "unauthorized")
    assert r.headers["www-authenticate"].startswith("Bearer")
    assert ro.get("/api/v1/deployments").status_code == 200
    rw = bearer(hub, make_token(hub, ["api:read", "api:write"]))
    assert rw.get("/api/v1/status").status_code == 200
    # token de CI (sem dono) não vale na API v1; token da API não vale no plano de controle interno
    ci = bearer(hub, make_token(hub, ["upload", "status"]))
    assert ci.get("/api/v1/apps").status_code == 401
    # ...mas vale nas ações dele (`hub deploy`/CI): status das implantações; nada além disso
    assert ci.get("/api/ops/deployments").status_code == 200
    assert ci.get("/api/ops/replicas").status_code == 401
    assert rw.get("/api/ops/deployments").status_code == 401
    # segredos nunca são revelados pela API (a operação não existe mais)
    assert rw.post("/api/v1/variables/FOO/reveal").status_code in (404, 405)
    # token só vale nas superfícies do seu escopo (mcp não abre a API)
    assert bearer(hub, make_token(hub, ["mcp"])).get("/api/v1/apps").status_code == 401
    # o RBAC do dono vale: usuário só com "Acessar aplicações" não lê operações
    colab = add_user(hub, "colab", ["Colaborador"])
    tok = make_token(colab, ["api:read", "api:write"])
    b2 = bearer(hub, tok)
    assert b2.get("/api/v1/apps").status_code == 200
    problem(b2.get("/api/v1/deployments"), 403, "forbidden")
    problem(b2.get("/api/v1/observability/summary"), 403, "forbidden")
    assert b2.get("/api/v1/inbox").status_code == 200


def test_app_scoped_token_limits_apps_everywhere(hub: TestClient) -> None:
    sc = bearer(hub, make_token(hub, ["api:read", "api:write"], ["ativos"]))
    assert [a["id"] for a in sc.get("/api/v1/apps").json()["items"]] == ["ativos"]
    problem(sc.post("/api/v1/apps/servicos/backups"), 403, "forbidden")
    problem(sc.post("/api/v1/deployments/99/approve"), 404, "not-found")  # id inexistente: nem vaza se existe
    problem(sc.post("/api/v1/audit/verify"), 403, "forbidden")  # plataforma, sem app: negada a token restrito


def test_writes_are_audited_and_rate_limited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = api_hub(tmp_path, monkeypatch, env={"HUB_API_RATE_PER_MIN": "3"})
    assert hub.post("/api/v1/audit/verify").status_code == 200
    audit = hub.get("/api/v1/audit?limit=5").json()["items"]
    assert any(e["title"] == "API v1: audit_verify" for e in audit)
    r = hub.get("/api/v1/apps")
    assert r.status_code == 200
    limited = hub.get("/api/v1/apps")
    problem(limited, 429, "rate-limited")
    assert int(limited.headers["retry-after"]) >= 1


def test_disabled_by_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    off = api_hub(tmp_path, monkeypatch, toml={"api": {"enabled": False}})
    problem(off.get("/api/v1/apps"), 404, "not-found")
    assert off.get("/admin/env").status_code == 200  # a UI segue funcionando
    docs_off = api_hub(tmp_path, monkeypatch, env={"HUB_API_DOCS_ENABLED": "false"})
    problem(docs_off.get("/api/v1/openapi.json"), 404, "not-found")
    assert docs_off.get("/api/docs").status_code == 404


# ---- contrato: toda resposta valida contra o esquema declarado ------------------------------------

DOC = ops.openapi()


def validator(schema: dict[str, Any]) -> Validator:
    return Draft202012Validator({"components": DOC["components"], **schema})


def declared(op: ops.Op) -> dict[str, Any]:
    o = DOC["paths"][op.full_path][op.method.lower()]
    return o["responses"]["2XX" if op.write else "200"]["content"]["application/json"]  # type: ignore[no-any-return]


def test_examples_validate_against_their_schemas() -> None:
    for op in ops.OPS:
        ok = declared(op)
        assert not list(validator(ok["schema"]).iter_errors(ok["example"])), op.id
        if op.body and op.body != ops.MULTIPART:
            body = DOC["paths"][op.full_path][op.method.lower()]["requestBody"]["content"]["application/json"]
            assert not list(validator(body["schema"]).iter_errors(body["example"])), op.body
            bad = {**body["example"], "campo_inexistente": 1}  # corpo fechado
            assert list(validator(body["schema"]).iter_errors(bad)), op.body


READS = [o for o in ops.OPS if not o.write and not any(p.required for p in o.params)]


@pytest.mark.parametrize("op", READS, ids=lambda o: o.id)
def test_read_operations_answer_with_their_declared_schema(hub: TestClient, op: ops.Op) -> None:
    r = hub.get(op.full_path)
    assert r.status_code == 200, (op.id, r.text)
    errors = [e.message for e in validator(declared(op)["schema"]).iter_errors(r.json())]
    assert not errors, (op.id, errors)


# ---- tokens com dono nas demais superfícies -------------------------------------------------------


def test_app_api_tokens_through_the_gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI, Request

    up = FastAPI()

    @up.api_route("/{app}/api/v1/items", methods=["GET", "POST"])
    async def items(app: str, request: Request) -> dict[str, str]:
        return {"auth": request.headers.get("authorization", ""), "app": app}

    hub = api_hub(tmp_path, monkeypatch, upstream=up)
    tok = make_token(hub, ["app-api"], ["ativos"])
    c = bearer(hub, tok)
    r = c.post("/apps/ativos/api/v1/items")  # sem CSRF: não há navegador
    assert r.status_code == 200 and r.json()["auth"] == "", "o token do hub nunca é repassado ao app"
    assert c.get("/apps/servicos/api/v1/items").status_code == 403  # fora do escopo de apps do token
    assert c.get("/apps/ativos").status_code == 401  # fora de /apps/{app}/api o token não vale
    assert c.get("/api/v1/apps").status_code == 401
    assert (
        hub.post("/_frag/ativos/_hub/mcp", json={}).status_code == 404
    )  # o MCP interno do app nunca é alcançável
    assert hub.post("/apps/ativos/api/../_hub/mcp", json={}).status_code // 100 == 4
    # revogado: recusa na hora, nas três superfícies
    both = make_token(hub, ["mcp", "app-api"], ["ativos"], name="c")
    cc = bearer(hub, both)
    assert cc.get("/apps/ativos/api/v1/items").status_code == 200
    tid = next(t["id"] for t in hub.get("/api/me/tokens").json()["items"] if t["name"] == "c")
    assert next(t for t in hub.get("/api/me/tokens").json()["items"] if t["id"] == tid)["active"] is True
    assert hub.post(f"/api/me/tokens/{tid}/revoke").status_code == 200
    assert next(t for t in hub.get("/api/me/tokens").json()["items"] if t["id"] == tid)["active"] is False
    assert cc.get("/apps/ativos/api/v1/items").status_code == 401
    assert rpc(cc, "initialize", {}).status_code == 401


def test_rate_limit_is_shared_by_the_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = api_hub(tmp_path, monkeypatch, env={"HUB_API_RATE_PER_MIN": "6"})
    t1, t2 = make_token(hub, ["api:read"], name="a"), make_token(hub, ["api:read"], name="b")
    codes = [bearer(hub, t1 if i % 2 == 0 else t2).get("/api/v1/status").status_code for i in range(8)]
    assert codes.count(200) <= 6 and 429 in codes, codes


def test_owner_changes_reach_the_token(hub: TestClient) -> None:
    colab = add_user(hub, "colab", ["Colaborador"])
    c = bearer(hub, make_token(colab, ["mcp", "api:read"]))
    assert rpc(c, "tools/list").status_code == 200
    # dono que perde as funções perde as ferramentas junto
    assert hub.post("/api/admin/roles", json={"name": "Sem acesso", "level": "Padrão"}).status_code in (
        200,
        201,
    )
    uid = next(u["id"] for u in hub.get("/api/admin/users").json() if u["id"] == "colab")
    assert hub.put(f"/api/admin/users/{uid}", json={"roles": ["Sem acesso"]}).status_code == 200
    names = {t["name"] for t in rpc(c, "tools/list").json()["result"]["tools"]}
    assert "apps_list" in names and "deployments_list" not in names and "tasks_list" not in names
    # dono desativado: o token para de valer
    assert hub.put(f"/api/admin/users/{uid}", json={"active": False}).status_code == 200
    assert rpc(c, "tools/list").status_code == 401


def test_invalid_configuration_stops_the_hub() -> None:
    from hub.api.config import load_cfg

    for toml in ({"mcp": {"path": "/x"}}, {"mcp": {"path": "/mcp/A"}}, {"mcp": {"transports": ["stdio"]}},
                 {"mcp": {"destructive_actions": "sim"}}):  # fmt: skip
        with pytest.raises(ValueError):
            load_cfg(toml, {})
    with pytest.raises(ValueError):
        load_cfg({}, {"HUB_API_RATE_PER_MIN": "muito"})
    cfg = load_cfg(
        {
            "mcp": {"path": "/mcp/langdock", "limits": {"calls_per_min": 5}},
            "api": {"docs": {"enabled": False}},
        },
        {},
    )
    assert cfg.mcp.path == "/mcp/langdock" and cfg.mcp.calls_per_min == 5 and cfg.api.docs is False


def test_mcp_path_is_configurable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = api_hub(tmp_path, monkeypatch, toml={"mcp": {"path": "/mcp/langdock"}})
    c = bearer(hub, make_token(hub, ["mcp"]))
    assert c.post("/mcp/langdock", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 200
    assert c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code != 200
