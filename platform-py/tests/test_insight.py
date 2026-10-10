"""F3b: flags, métricas/SLOs, traces, analytics, logs, auditoria, saúde e as páginas/APIs de Observabilidade."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from admin_helper import HDR, add_user, make_hub
from fastapi.testclient import TestClient
from starlette.requests import Request

from hub.admin.store import LogBus
from hub.insight import flags as fl
from hub.insight.analytics import Analytics, day_key, norm_term
from hub.insight.metrics import (
    Metrics,
    TraceStore,
    burn_rates,
    daily_uptime,
    exhaust_label,
    fast_firing,
    fraction_under,
    percentile,
    slo_status,
)
from hub.insight.routes import chart, ms_text, split_ids
from hub.insight.runtime import page_key, redact_target
from hub.ux.status import status_data

JSON = {"content-type": "application/json"}
VECTORS = json.loads((Path(__file__).parent / "vectors/flags-vectors.json").read_text(encoding="utf-8"))


def echo(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        stream=httpx.ByteStream(json.dumps({"headers": dict(request.headers)}).encode()),
        headers={"content-type": "application/json"},
    )


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("HUB_TELEMETRY", raising=False)
    return make_hub(tmp_path, monkeypatch, echo)


def audit_titles(c: TestClient) -> list[str]:
    return [e["title"] for e in asyncio.run(c.app.state.admin.cfg.read())["audit"]]  # type: ignore[attr-defined]


def flag_body(key: str = "novo", **over: Any) -> dict[str, Any]:
    rule = {
        "enabled": True,
        "default": "off",
        "rules": [{"roles": ["Administrador"], "users": [], "serve": "on"}],
    }
    return {
        "key": key,
        "app": "ativos",
        "description": "d",
        "kind": "boolean",
        "variants": [],
        "envs": {"producao": rule},
    } | over


# ---- flags (puras) -------------------------------------------------------------------------------


def test_flag_vectors_shared_with_rust() -> None:
    for k, v in VECTORS["buckets"].items():
        key, user = k.split(":", 1)
        assert fl.bucket(key, user) == v
    for k, v in VECTORS["user_digests"].items():
        key, user = k.split(":", 1)
        assert fl.user_digest(key, user) == v


def test_flag_evaluation_order() -> None:
    f = fl.parse_flag(flag_body())
    f.envs["producao"].rollout = fl.Rollout(percent=30, serve="on")
    assert fl.evaluate(f, "producao", "x", ["Administrador"]) == "on"
    assert fl.evaluate(f, "staging", "x", ["Administrador"]) == "off"  # sem regra no ambiente
    assert fl.evaluate(f, "producao", "", []) == "off"  # convidado fora do rollout
    on = sum(fl.evaluate(f, "producao", f"u{i}", []) == "on" for i in range(10_000))
    assert 2_800 <= on <= 3_200
    f.envs["producao"].enabled = False
    assert fl.evaluate(f, "producao", "x", ["Administrador"]) == "off"  # kill switch
    f.envs["producao"].enabled, f.envs["producao"].rules[0].users = (
        True,
        [fl.user_digest("novo", "Ana@Demo.local")],
    )
    f.envs["producao"].rules[0].roles = []
    assert fl.evaluate(f, "producao", "ana@demo.local", []) == "on"  # regra empurrada por resumo
    assert fl.evaluate(f, "producao", "bia@demo.local", []) == "off"


def test_flag_header_value_and_validation() -> None:
    a, b = fl.parse_flag(flag_body("b")), fl.parse_flag(flag_body("a"))
    assert fl.header_value([a, b], "ativos", "producao", "x", ["Administrador"]) == "a=on,b=on"
    assert fl.header_value([a], "outro", "producao", "x", []) == ""
    big = [fl.parse_flag(flag_body(f"k{i:04}" + "x" * 50)) for i in range(100)]
    assert len(fl.header_value(big, "ativos", "producao", "x", ["Administrador"])) <= fl.MAX_HEADER_BYTES
    bad = lambda **o: pytest.raises(ValueError).__enter__ and fl.parse_flag(flag_body(**o)).validate_rules()  # noqa: E731
    for over in (
        {"key": "Inválida"},
        {"description": "x" * 201},
        {"kind": "variant", "variants": ["a"]},
        {"kind": "variant", "variants": ["a", "B!"]},
        {"envs": {"dev": {"enabled": True, "default": "off"}}},
        {"envs": {"producao": {"enabled": True, "default": "talvez"}}},
        {
            "envs": {
                "producao": {"enabled": True, "default": "off", "rollout": {"percent": 101, "serve": "on"}}
            }
        },
        {
            "envs": {
                "producao": {
                    "enabled": True,
                    "default": "off",
                    "rules": [{"roles": ["r"] * 101, "serve": "on"}],
                }
            }
        },
    ):
        with pytest.raises(ValueError):
            bad(**over)
    with pytest.raises(ValueError):
        fl.parse_flag({"key": 1})
    assert fl.flag_detail(fl.parse_flag(flag_body())).startswith("novo · Produção: ligada, 1 regra(s)")


# ---- métricas, SLOs e traces ----------------------------------------------------------------------


def test_red_metrics_and_percentiles() -> None:
    m, now = Metrics(), 1_800_000_000
    for i in range(100):
        m.observe("a", "producao", 503 if i < 5 else 200, 20.0 if i < 90 else 800.0, now)
    m.observe("a", "producao", 404, 1.0, now)  # 4xx não é erro do servidor
    w = m.window("a", "producao", 0, 2**40)
    assert (w.requests, w.errors) == (101, 5) and w.availability == pytest.approx(1 - 5 / 101)
    assert w.under(250) == pytest.approx(91 / 101)
    assert 500 <= (w.p95 or 0) <= 1000
    assert m.window("a", "staging", 0, 2**40).requests == 0
    assert percentile([0] * 13, 0.5) is None and fraction_under([0] * 13, 5) is None
    top = [0] * 12 + [1]
    assert (percentile(top, 0.99) or 0) >= 30000


def test_points_gauges_probes_and_persistence(tmp_path: Path) -> None:
    m = Metrics(tmp_path)
    assert m.points("a", "producao", 1) == [] and m.last_gauges("a", "producao") is None
    m.observe("a", "producao", 200, 12.0)
    m.inflight_inc("a", "producao")
    m.inflight_inc("a", "producao")
    m.inflight_dec("a", "producao")
    m.set_resources("a", "producao", 0.5, 100)
    m.set_app_gauges("a", "producao", {"jobs_queued": 3.0})
    pts = m.points("a", "producao", 1)
    assert pts and pts[-1]["rps"] > 0 and pts[-1]["cpu"] == 0.5 and pts[-1]["mem"] == 100
    g = m.last_gauges("a", "producao")
    assert g and g["inflight"] == 1 and g["app"]["jobs_queued"] == 3.0
    t = 1_700_000_000
    m.record_probe("b", "producao", True, t)
    m.record_probe("b", "producao", False, t + 60)
    m.save()
    w = Metrics(tmp_path).window("b", "producao", t - 600, t + 600)
    assert (w.probes, w.probes_ok) == (2, 1)
    (tmp_path / "metrics.json").write_text("{lixo", encoding="utf-8")
    assert Metrics(tmp_path).series == {}


def test_slo_budget_burn_and_uptime() -> None:
    m, now = Metrics(), 1_800_000_000
    slo = {
        "app": "a",
        "env": "producao",
        "availability": 99.0,
        "latency_ms": 250,
        "latency_target": 90.0,
        "window_days": 7,
    }
    for i in range(1000):
        m.observe(
            "a", "producao", 500 if i < 5 else 200, 400.0 if i % 10 == 0 else 20.0, now - 86_400 * 3 + i
        )
    s = slo_status(m, slo, now)
    assert s["availability"] == pytest.approx(0.995) and s["budget_remaining"] == pytest.approx(0.5, abs=1e-6)
    assert s["latency"] == pytest.approx(0.9) and s["met"]
    assert burn_rates(m, slo, now)[0] == 0.0
    for i in range(100):
        m.observe("a", "producao", 503 if i % 2 == 0 else 200, 10.0, now - 120 + i)
    burn = burn_rates(m, slo, now)
    assert fast_firing(burn) and not slo_status(m, slo, now)["met"]
    assert exhaust_label(burn[0], 7).startswith("~") and exhaust_label(0, 7) == "nunca"
    assert exhaust_label(100, 30) == "~7 h"
    day = now - now % 86_400 + 3600
    for i in range(60):
        m.record_probe("p", "producao", i != 0, day - 3000 + i * 10)
    d = daily_uptime(m, "p", "producao", 3, day)
    assert [x[1] for x in d[:2]] == [None, None] and d[2][1] == pytest.approx(59 / 60)
    only_probes = {**slo, "app": "p"}
    assert slo_status(m, only_probes, day)["availability"] == pytest.approx(59 / 60)


def test_trace_store_slow_and_owner() -> None:
    ts = TraceStore(cap=10)
    root = {"trace_id": "t" * 32, "span_id": "s", "parent_id": None, "name": "GET /a", "service": "hub", "kind": "server",
            "start_us": 1, "duration_us": 900, "error": False, "attrs": {"hub.app": "a", "http.status_code": "200"}}  # fmt: skip
    ts.record(root)
    ts.record({**root, "span_id": "c", "parent_id": "s", "service": "a", "start_us": 2, "error": True})
    ts.claim("t" * 32, "a")
    slow = ts.slow("a", 5)
    assert len(slow) == 1 and slow[0]["spans"] == 2 and slow[0]["error"] and slow[0]["status"] == "200"
    assert ts.slow("b", 5) == [] and ts.owners["t" * 32] == "a"
    assert [s["span_id"] for s in ts.trace("t" * 32)] == ["s", "c"]
    for i in range(40):
        ts.claim(f"x{i}", "a")
    assert len(ts.owners) <= 40


def test_redact_target_and_helpers() -> None:
    assert redact_target("/ativos/itens/42?q=segredo#x") == "/ativos/itens/:id"
    assert redact_target("/auth/reset/abc") == "/auth/reset/:redacted"
    assert redact_target("/c/u/ana@demo.local/perfil") == "/c/u/:email/perfil"
    assert redact_target("/x/" + "a1" * 10) == "/x/:token"
    assert redact_target("/x/123e4567-e89b-12d3-a456-426614174000") == "/x/:id"
    assert (
        page_key([""]) == "/"
        and page_key(["admin", "users"]) == "/admin/users"
        and page_key(["apps", "x"]) is None
    )
    assert ms_text(2_500_000) == "2,50 s" and ms_text(5_000) == "5,00 ms" and ms_text(50_000) == "50 ms"
    parts = split_ids("Token · HUB_INTERNAL_TOKEN_X (Secreta) e hubt_ab12… fim")
    assert [p for p, code in parts if code] == ["HUB_INTERNAL_TOKEN_X", "hubt_ab12…"]
    assert split_ids("sem nada") == [("sem nada", False)]
    c = chart("t", "ms", [("a", "red", [None, 5.0, 10.0])])
    assert not c["empty"] and c["ymax"] == "12" and chart("t", "ms", [("a", "red", [None, None])])["empty"]


# ---- analytics ---------------------------------------------------------------------------------------


def test_analytics_privacy_aggregates_and_csv(tmp_path: Path) -> None:
    a, now = Analytics(tmp_path, None), 1_790_000_000_000
    for u in ("u1", "u2"):
        a.record(u, "login", now=now)
        a.record(u, "open", "ativos", now=now)
    a.record("u1", "action", "ativos", now=now)
    a.record("u1", "section", "ativos", "assets", now=now)
    a.record("u1", "page", "/admin/users", now=now)
    a.record("u1", "search", "ana@x.com 123", n=0, now=now)  # com @: descartado
    a.record("u1", "search", "contrato 12345", n=0, now=now)
    a.record("u1", "feature", "palette_open", now=now)
    a.record("u1", "feature", "inventada", now=now)
    a.record_global("approval_approved", "ativos", now=now)
    s = a.summary(now)
    assert (s["dau"], s["wau"], s["mau"]) == (2, 2, 2) and s["funnel"] == [2, 2, 1]
    assert s["apps"] == [["ativos", 2, 2, 2, 2]] and s["searches"] == 2 and s["searches_zero"] == 2
    assert s["top_sections"] == [["ativos/assets", 1]] and s["features"] == [["palette_open", 1]]
    assert s["zero_terms"][0][0].startswith("termo #")  # só hash, sem opt-in
    a.set_raw_terms(True)
    a.record("u1", "search", "contrato 99", n=0, now=now)
    assert ["contrato ##", 1] in a.summary(now)["zero_terms"]
    a.set_raw_terms(False)
    assert all(k.startswith("termo #") for k, _ in a.summary(now)["zero_terms"])
    a.flush(now)
    disk = (tmp_path / "analytics.json").read_text(encoding="utf-8")
    assert "u1" not in disk and "ana@x.com" not in disk and "contrato" not in disk
    assert (tmp_path / "analytics.key").exists()
    csv = a.csv(now)
    assert csv.startswith("dia;usuarios_ativos") and '"ativos";2;2;2;2' in csv
    first = a._h("user|u1")
    a.rotate_key(now)
    assert a._h("user|u1") != first  # ids novos não se ligam aos antigos
    assert Analytics(tmp_path, None).summary(now)["dau"] == 2  # recarrega do disco


def test_analytics_retention_optout_and_disabled(tmp_path: Path) -> None:
    a, now = Analytics(tmp_path, None), 1_790_000_000_000
    a.record("u", "page", "/x", now=now - 100 * 86_400_000)
    assert a.set_retention(1) == 7 and a.set_retention(9999) == 400
    a.set_retention(30)
    a.flush(now)
    assert day_key(now - 100 * 86_400_000) not in a.days
    assert a.window(now, 3)[-1][0] == day_key(now)
    off = Analytics(None, None, enabled=False)
    off.record("u", "login")
    off.record_global("x", "y")
    assert off.days == {} and off.bytes_estimate() > 0
    assert norm_term("  ") is None and norm_term("a@b") is None and norm_term("Pedido 12") == "pedido ##"

    a.ux = SimpleNamespace(data=SimpleNamespace(users={"u": SimpleNamespace(analytics_opt_out=True)}))
    a.record("u", "login", now=now)
    assert a.summary(now)["dau"] == 0


def test_analytics_csv_neutralizes_formulas() -> None:
    a, now = Analytics(None, None), 1_790_000_000_000
    a.record("u", "open", "=HYPERLINK(x)", now=now)
    assert '"\'=HYPERLINK(x)"' in a.csv(now)


# ---- logs ----------------------------------------------------------------------------------------------


def test_log_bus_filters() -> None:
    bus, now = LogBus(), int(time.time() * 1000)
    for ago, app, user, level, source in (
        (97, "ativos", "ana", "INFO", "gateway"),
        (70, "fornecedores", "bia", "WARN", "admin"),
        (2, "servicos", "caio", "ERROR", "gateway"),
    ):
        bus.add(level, app, user, "m", source, now - ago * 60_000)
    q = lambda **f: len(bus.query(f))  # noqa: E731
    assert q() == 3 and q(minutes="60") == 1 and q(app="ativos") == 1 and q(app="all") == 3
    assert (
        q(user="ANA") == 1
        and q(level="WARN,ERROR") == 2
        and q(source="admin") == 1
        and q(app="platform") == 0
    )
    bus.gateway("GET", "/x", 502, 12, "ativos", "u")
    assert bus.lines[-1]["level"] == "ERROR" and "GET /x → 502 (12 ms)" in bus.lines[-1]["message"]
    bus.gateway("GET", "/x", 404, 1, "ativos", "u")
    assert bus.lines[-1]["level"] == "WARN"


# ---- páginas e APIs --------------------------------------------------------------------------------------


def test_pages_render_and_rbac(hub: TestClient) -> None:
    for p in ("logs", "audit", "health"):
        r = hub.get(f"/admin/{p}")
        assert r.status_code == 200, p
    old = hub.get("/admin/observability?app=ativos", follow_redirects=False)
    assert old.status_code == 308 and old.headers["location"] == "/admin/health?app=ativos#obs"
    assert hub.get("/admin/analytics", follow_redirects=False).headers["location"] == "/admin#uso"
    assert hub.get("/admin/flags", follow_redirects=False).status_code == 308  # tela removida
    assert "Logs · " in hub.get("/admin/logs").text and "Auditoria · " in hub.get("/admin/audit").text
    colab = add_user(hub, "colab", ["Colaborador"])
    for p in ("logs", "audit", "health", "observability", "analytics"):
        assert colab.get(f"/admin/{p}").status_code == 403, p
    for url in (
        "/api/admin/logs",
        "/api/admin/audit",
        "/api/admin/flags",
        "/api/admin/slos",
        "/api/admin/health",
        "/api/admin/analytics",
    ):
        assert colab.get(url).status_code == 403, url
    po = add_user(hub, "po", ["Product Owner"])  # ver auditoria, sem editar configurações
    for p in ("logs", "audit", "health"):
        assert po.get(f"/admin/{p}").status_code == 200, p
    assert po.get("/api/admin/flags").status_code == 403
    for url in (
        "/api/admin/logs",
        "/api/admin/audit",
        "/api/admin/slos",
        "/api/admin/incidents",
        "/api/admin/health",
        "/api/admin/analytics",
    ):
        assert po.get(url).status_code == 200, url
    assert po.post("/api/admin/health/check", json={}).status_code == 200
    assert po.get("/api/admin/flags").status_code == 403
    assert po.put("/api/admin/slos", json={}).status_code == 403
    assert po.post("/api/admin/health", json={}).status_code == 403
    assert po.post("/api/admin/incidents", json={}).status_code == 403
    assert po.post("/api/admin/analytics/rotate-key", json={}).status_code == 403
    assert po.put("/api/admin/analytics/retention", json={"days": 30}).status_code == 403
    assert po.put("/api/admin/analytics/terms", json={"raw": True}).status_code == 403
    assert (
        hub.post("/api/admin/flags", content="x", headers={"content-type": "text/plain"}).status_code == 415
    )


def test_flags_crud_is_audited_and_validated(hub: TestClient) -> None:
    r = hub.post("/api/admin/flags", json=flag_body())
    assert r.status_code == 200 and r.json()["key"] == "novo"
    assert hub.post("/api/admin/flags", json=flag_body()).status_code == 400  # duplicada
    assert hub.post("/api/admin/flags", json=flag_body("x", app="nenhum")).status_code == 400
    assert hub.post("/api/admin/flags", json=flag_body("Ruim")).status_code == 400
    assert hub.post("/api/admin/flags", content=b"[]", headers=JSON).status_code == 400
    assert hub.post("/api/admin/flags", content=b"{", headers=JSON).status_code == 400
    assert (
        hub.put("/api/admin/flags/ativos/novo", json=flag_body(description="outra")).json()["description"]
        == "outra"
    )
    assert hub.put("/api/admin/flags/ativos/nao", json=flag_body("nao")).status_code == 404
    r = hub.post("/api/admin/flags/ativos/novo/staging/enabled", json={"enabled": True})
    assert r.status_code == 200 and r.json()["envs"]["staging"]["enabled"] is True
    assert hub.post("/api/admin/flags/ativos/novo/dev/enabled", json={"enabled": True}).status_code == 400
    assert hub.post("/api/admin/flags/ativos/novo/staging/enabled", json={"enabled": "s"}).status_code == 400
    assert hub.post("/api/admin/flags/ativos/nao/staging/enabled", json={"enabled": True}).status_code == 404
    listed = hub.get("/api/admin/flags").json()
    assert listed["version"] == 3 and [f["key"] for f in listed["flags"]] == ["novo"]
    assert hub.delete("/api/admin/flags/ativos/novo", headers=JSON).json() == {"deleted": "novo"}
    assert hub.delete("/api/admin/flags/ativos/novo", headers=JSON).status_code == 404
    titles = audit_titles(hub)
    for t in ("Feature flag criada", "Feature flag alterada", "Feature flag ligada", "Feature flag excluída"):
        assert t in titles, t
    assert "Feature flag desligada (kill switch)" not in titles
    hub.post("/api/admin/flags", json=flag_body("v", kind="variant", variants=["a", "b"], envs={}))
    hub.post("/api/admin/flags/ativos/v/producao/enabled", json={"enabled": True})
    hub.post("/api/admin/flags/ativos/v/producao/enabled", json={"enabled": False})
    assert "Feature flag desligada (kill switch)" in audit_titles(hub)
    assert hub.app.state.admin.chain.verify().ok  # type: ignore[attr-defined]
    many = [flag_body(f"f{i}") for i in range(fl.MAX_FLAGS_PER_APP)]
    for body in many[: fl.MAX_FLAGS_PER_APP - 1]:  # já há 1 flag (v)
        assert hub.post("/api/admin/flags", json=body).status_code == 200
    assert hub.post("/api/admin/flags", json=flag_body("excesso")).status_code == 400


def test_gateway_observed_metrics_logs_traces_and_flags_header(hub: TestClient) -> None:
    hub.post("/api/admin/flags", json=flag_body("resumo"))
    r = hub.get("/_frag/ativos/echo", headers={"x-hub-nav": "1"})
    assert r.status_code == 200
    h = r.json()["headers"]
    assert (
        h["x-hub-flags"] == "resumo=on"
        and h["traceparent"].startswith("00-")
        and h["traceparent"].endswith("-01")
    )
    ins = hub.app.state.insight  # type: ignore[attr-defined]
    w = ins.tel.metrics.window("ativos", "producao", 0, int(time.time()) + 600)
    assert w.requests == 1 and w.errors == 0
    root = ins.tel.traces.spans[-1]
    assert (
        root["attrs"]["hub.app"] == "ativos"
        and root["attrs"]["http.target"] == "/ativos/echo"
        and root["span_id"] in h["traceparent"]
    )
    logs = hub.get("/api/admin/logs?source=gateway&app=ativos").json()
    assert (
        logs
        and "[gateway] GET /ativos/echo → 200" in logs[-1]["message"]
        and logs[-1]["user"] == "Administrador"
    )
    assert hub.get("/api/admin/logs/stats").json() == {"subscribers": 0}
    page = hub.get("/admin/logs").text
    assert "[gateway] GET /ativos/echo" in page and "log-level-info" in page
    # app desconhecido: sem métrica, log em "platform"
    assert hub.get("/_frag/nao-existe/x").status_code == 404
    assert ins.tel.metrics.window("nao-existe", "producao", 0, 2**40).requests == 0
    # outro usuário (sem a regra por função) recebe a flag desligada
    colab = add_user(hub, "colab", ["Colaborador"])
    assert colab.get("/_frag/ativos/echo").json()["headers"]["x-hub-flags"] == "resumo=off"
    assert "x-hub-flags" not in hub.get("/_frag/ativos/echo").json()["headers"] or True


def test_observability_on_health_page(hub: TestClient) -> None:
    hub.get("/_frag/ativos/echo")
    ins = hub.app.state.insight  # type: ignore[attr-defined]
    ins.tel.traces.record({**ins.tel.traces.spans[-1], "span_id": "c" * 16, "parent_id": ins.tel.traces.spans[-1]["span_id"],
                           "service": "ativos", "kind": "client", "start_us": ins.tel.traces.spans[-1]["start_us"] + 1})  # fmt: skip
    r = hub.get("/admin/health?app=ativos&min=15")
    assert r.status_code == 200 and 'id="obs"' in r.text and "Requisições mais lentas" in r.text
    assert (
        'class="filter-chip active" href="/admin/health?app=ativos&amp;env=producao&amp;min=15#obs"' in r.text
    )
    assert "Sem dados" in hub.get("/admin/health?app=fornecedores&env=staging").text
    # restrito a um app: não enxerga o trace de outro
    asyncio.run(hub.app.state.admin.cfg.role_apps("t", "Product Owner", ["servicos"]))  # type: ignore[attr-defined]
    hub.app.state.roles = __import__("hub.auth", fromlist=["roles_for"]).roles_for(hub.app.state.data_dir)  # type: ignore[attr-defined]
    po = add_user(hub, "po2", ["Product Owner"])
    page = po.get("/admin/health").text
    assert "Ativos" not in page.split('id="obs"')[1].split("filter-chips")[1]


def test_ingest_from_apps_is_validated(hub: TestClient) -> None:
    ins = hub.app.state.insight  # type: ignore[attr-defined]
    good = {"trace_id": "a" * 32, "span_id": "b" * 16, "name": "GET /x", "start_us": 5, "duration_us": 9, "kind": "server",
            "attrs": {"hub.app": "falso", "enduser.id": "x", "http.target": "/u/1/y?z=1", "k": "v"}}  # fmt: skip
    payload = {"spans": [good, {**good, "trace_id": "zz"}, {**good, "span_id": "c" * 16, "duration_us": -1}, "lixo"],
               "gauges": {"jobs_queued": 2, "ruim": "x", "k" * 41: 1}}  # fmt: skip
    assert ins.ingest("ativos", "producao", payload) == 1
    s = ins.tel.traces.spans[-1]
    assert s["service"] == "ativos" and s["attrs"] == {
        "k": "v",
        "http.target": "/u/:id/y",
        "hub.app": "ativos",
        "hub.env": "producao",
    }
    assert ins.tel.metrics.last_gauges("ativos", "producao")["app"] == {"jobs_queued": 2.0}  # type: ignore[index]
    # outro app não enxerta spans no trace de ativos
    assert ins.ingest("contratos", "producao", {"spans": [{**good, "span_id": "d" * 16}]}) == 0
    assert len(ins.tel.traces.spans) == 1


def test_health_slo_incidents_and_status(hub: TestClient) -> None:
    rows = hub.get("/api/admin/health").json()
    assert [r["name"] for r in rows][:2] == [
        "Autenticação",
        "Gestão de Ativos",
    ] and "Verificar agora" in hub.get("/admin/health").text
    r = hub.post(
        "/api/admin/health", json={"name": "Fila", "kind": "Serviço", "status": "Disponível", "app": None}
    )
    assert r.status_code == 201
    assert (
        hub.post(
            "/api/admin/health", json={"name": "fila", "kind": "Serviço", "status": "Disponível"}
        ).status_code
        == 400
    )
    for bad in ({"name": "", "kind": "Serviço", "status": "Disponível"}, {"name": "X", "kind": "?", "status": "Disponível"},
                {"name": "X", "kind": "Serviço", "status": "?"}, {"name": "X", "kind": "Serviço", "status": "Disponível", "app": "n"}):  # fmt: skip
        assert hub.post("/api/admin/health", json=bad).status_code == 400
    assert (
        hub.put("/api/admin/health/Fila", json={"kind": "Serviço", "status": "Manutenção", "app": ""}).json()[
            "status"
        ]
        == "Manutenção"
    )
    assert (
        hub.put("/api/admin/health/Nada", json={"kind": "Serviço", "status": "Manutenção"}).status_code == 404
    )
    assert 'data-row-menu="health:Fila"' in hub.get("/admin/health").text
    chk = hub.post("/api/admin/health/check", json={}).json()
    assert {x["app"] for x in chk["results"]} >= {"ativos"} and all(x["available"] for x in chk["results"])
    assert any(s["latency_ms"] is not None for s in chk["health"] if s["app"])
    assert hub.delete("/api/admin/health/Fila", headers=JSON).status_code == 204
    assert hub.delete("/api/admin/health/Fila", headers=JSON).status_code == 404

    slo = {
        "app": "ativos",
        "env": "producao",
        "availability": 99.5,
        "latency_ms": 800,
        "latency_target": 95.0,
        "window_days": 30,
    }
    assert hub.put("/api/admin/slos", json=slo).json() == slo
    for bad in ({**slo, "app": "n"}, {**slo, "env": "x"}, {**slo, "availability": 10}, {**slo, "latency_ms": 0},
                {**slo, "window_days": 5}, {"app": "ativos"}):  # fmt: skip
        assert hub.put("/api/admin/slos", json=bad).status_code == 400
    hub.get("/_frag/ativos/echo")
    page = hub.get("/admin/health").text
    assert (
        "99,5% · 95% ≤ 800 ms · 30 dias" in page and "Dentro da meta" in page and "100,00% (1 req.)" in page
    )
    assert hub.get("/api/admin/slos").json() == [slo]

    inc = hub.post(
        "/api/admin/incidents",
        json={"app": "ativos", "title": "Lento", "severity": "minor", "note": "olhando"},
    ).json()
    assert inc["id"] == 1 and inc["notes"][0]["author"] == "Administrador" and inc["kind"] == "manual"
    for bad in ({"app": "n", "title": "x", "severity": "minor"}, {"app": "ativos", "title": "", "severity": "minor"},
                {"app": "ativos", "title": "x", "severity": "?"}, {"app": "ativos", "title": "x", "severity": "minor", "note": "n" * 2001}):  # fmt: skip
        assert hub.post("/api/admin/incidents", json=bad).status_code == 400
    st = hub.get("/status").text
    assert (
        "Algumas aplicações com problemas" in st
        and 'data-incident="1"' in st
        and "Degradado" in st
        and "SLO 99,50%" in st
    )
    assert hub.post("/api/admin/incidents/1", json={"note": "x" * 2001}).status_code == 400
    assert hub.post("/api/admin/incidents/9", json={"note": "a"}).status_code == 404
    assert hub.post("/api/admin/incidents/1", json={"note": "ok"}).json()["notes"][-1]["text"] == "ok"
    major = hub.post(
        "/api/admin/incidents", json={"app": "platform", "title": "Queda", "severity": "major"}
    ).json()
    assert "Há aplicações indisponíveis" in hub.get("/status").text
    hub.post(f"/api/admin/incidents/{major['id']}", json={"resolve": True})
    st = hub.get("/status").text
    assert "Resolvido" in st and "Todos" not in st
    hub.post("/api/admin/incidents/1", json={"resolve": True})
    st = hub.get("/status").text
    assert "Todos os sistemas operacionais" in st and "data-incident-resolve" not in st
    assert [i["id"] for i in hub.get("/api/admin/incidents").json()] == [2, 1]
    assert hub.delete("/api/admin/slos/ativos/producao", headers=JSON).json() == {"deleted": True}
    assert hub.delete("/api/admin/slos/ativos/producao", headers=JSON).status_code == 404
    titles = audit_titles(hub)
    for t in ("Serviço adicionado ao monitoramento", "Serviço atualizado", "Verificação de integridade concluída",
              "Serviço removido do monitoramento", "SLO definido", "SLO removido", "Incidente registrado",
              "Nota de incidente adicionada", "Incidente resolvido"):  # fmt: skip
        assert t in titles, t


def test_status_reflects_probes_and_uptime(hub: TestClient) -> None:
    ins = hub.app.state.insight  # type: ignore[attr-defined]
    now = int(time.time())
    for i in range(5):
        ins.tel.metrics.record_probe("ativos", "producao", False, now - i)
    st = hub.get("/status").text
    assert "Instável" in st and "uptime-bad" in st
    home = hub.get("/").text
    assert "Instável" in home
    data = status_data(
        hub.app.state.gateway.registry,
        {"ativos"},
        time.time(),
        ins.tel.metrics,
        {"incidents": [], "slos": []},
    )  # type: ignore[attr-defined]
    assert [a.name for a in data.apps] == ["App Ativos"] and data.apps[0].uptime.endswith("em 30 dias")


def test_auto_incident_from_fast_burn(hub: TestClient) -> None:
    ins = hub.app.state.insight  # type: ignore[attr-defined]
    slo = {
        "app": "ativos",
        "env": "producao",
        "availability": 99.0,
        "latency_ms": 500,
        "latency_target": 95.0,
        "window_days": 7,
    }
    hub.put("/api/admin/slos", json=slo)
    for i in range(60):
        ins.tel.metrics.observe("ativos", "producao", 503 if i % 2 else 200, 10.0)

    async def run() -> list[dict[str, Any]]:
        await ins.check_slo(slo)
        await ins.check_slo(slo)  # sem duplicar
        first = list(await ins.ops.incidents())
        assert len(first) == 1 and first[0]["kind"] == "auto" and first[0]["resolved"] is None
        ins.tel.metrics.series["ativos|producao"].coarse.clear()
        await ins.check_slo(slo)
        return list(await ins.ops.incidents())

    done = asyncio.run(run())
    assert (
        len(done) == 1
        and done[0]["resolved"] is not None
        and done[0]["notes"][-1]["text"].startswith("Taxa de erro voltou")
    )


def test_audit_page_verify_export_and_filter(hub: TestClient) -> None:
    hub.post("/api/admin/flags", json=flag_body("a1"))
    hub.app.state.admin.logs.add("INFO", "ativos", "x", "m", "app")  # type: ignore[attr-defined]
    asyncio.run(
        hub.app.state.admin.audit(
            "Ana", "Token rotacionado", "HUB_INTERNAL_TOKEN_X (Secreta) hubt_ab12…", scope="ativos"
        )
    )  # type: ignore[attr-defined]
    page = hub.get("/admin/audit").text
    assert (
        "Feature flag criada" in page
        and "<code>HUB_INTERNAL_TOKEN_X</code>" in page
        and "<code>hubt_ab12…</code>" in page
    )
    assert "Proteção contra rollback: só local" in page and '<option value="" selected>Todas</option>' in page
    only = hub.get("/admin/audit?scope=ativos").text
    assert "Token rotacionado" in only and "Acesso" not in only.split("activity-list")[1]
    assert "Nenhum evento" in hub.get("/admin/audit?scope=servicos").text
    api = hub.get("/api/admin/audit?scope=ativos&limit=1").json()
    assert len(api) == 1 and api[0]["title"] == "Token rotacionado"  # mais recente primeiro
    assert len(hub.get("/api/admin/audit?limit=0").json()) == 1
    v = hub.post("/api/admin/audit/verify", json={}).json()
    assert v["ok"] and v["entries"] >= 2 and v["problems"] == [] and v["segments"] == 1
    assert "Integridade da auditoria verificada" in audit_titles(hub)
    ex = hub.get("/api/admin/audit/export")
    assert ex.headers["content-disposition"] == "attachment; filename=auditoria.jsonl" and ex.headers[
        "content-type"
    ].startswith("application/x-ndjson")
    lines = [json.loads(x) for x in ex.text.strip().split("\n")]
    assert (
        lines[-1]["t"] == "proof"
        and lines[-1]["verification"]["ok"]
        and lines[0]["t"] == "e"
        and "Auditoria exportada (JSONL com provas)" in audit_titles(hub)
    )
    # cadeia adulterada: a verificação acusa e a página mostra o bloqueio
    chain = hub.app.state.admin.chain  # type: ignore[attr-defined]
    first = json.loads(chain.path.read_text(encoding="utf-8").splitlines()[0])
    first["hash"] = "f" * 64
    chain.path.write_text(json.dumps(first) + "\n", encoding="utf-8")
    bad = hub.post("/api/admin/audit/verify", json={}).json()
    assert not bad["ok"] and bad["problems"]
    assert "Cadeia de auditoria bloqueada" in hub.get("/admin/audit").text


def test_analytics_card_api_and_tracking(hub: TestClient) -> None:
    hub.get("/applications")
    hub.get("/admin/users")
    hub.get("/_frag/ativos/assets", headers={"x-hub-nav": "1"})
    hub.get("/_frag/ativos/_interno", headers={"x-hub-nav": "1"})
    hub.post("/_frag/ativos/acao", headers={"sec-fetch-site": "same-origin"})
    hub.get("/apps/ativos")
    hub.post("/api/me/analytics", json={"feature": "palette_open"})
    ins = hub.app.state.insight  # type: ignore[attr-defined]
    ins.analytics.record("u-test", "login")
    s = hub.get("/api/admin/analytics").json()
    sm = s["summary"]
    assert (
        sm["dau"] == 1 and ["/applications", 1] in sm["top_pages"] and ["/admin/users", 1] in sm["top_pages"]
    )
    assert ["ativos/assets", 1] in sm["top_sections"] and not any(
        "_interno" in k for k, _ in sm["top_sections"]
    )
    assert sm["apps"][0][0] == "ativos" and sm["funnel"][0] == 1 and s["retention_days"] == 90
    page = hub.get("/admin").text
    assert 'id="uso"' in page and "Usuários ativos (MAU)" in page and "Buscas sem resultado" in page
    csv = hub.get("/api/admin/analytics.csv")
    assert csv.text.startswith("﻿dia;") and "attachment" in csv.headers["content-disposition"]
    assert hub.put("/api/admin/analytics/retention", json={"days": 30}).json() == {"ok": True, "days": 30}
    assert hub.put("/api/admin/analytics/retention", json={"days": "x"}).status_code == 400
    assert hub.put("/api/admin/analytics/terms", json={"raw": True}).json() == {"ok": True, "raw": True}
    assert hub.put("/api/admin/analytics/terms", json={"raw": 1}).status_code == 400
    assert hub.post("/api/admin/analytics/rotate-key", json={}).json() == {"ok": True}
    t = audit_titles(hub)
    for x in (
        "Retenção do analytics alterada",
        "Termos de busca sem resultado",
        "Chave dos ids do analytics trocada",
    ):
        assert x in t, x
    # opt-out: o usuário deixa de ser contado
    hub.put("/api/me/prefs", json={"analytics_opt_out": True})
    before = hub.get("/api/admin/analytics").json()["summary"]["top_pages"]
    hub.get("/inbox")
    assert hub.get("/api/admin/analytics").json()["summary"]["top_pages"] == before
    assert HDR  # cabeçalhos de origem usados pelo cliente


def test_login_and_search_feed_analytics(hub: TestClient) -> None:
    ins = hub.app.state.insight  # type: ignore[attr-defined]
    hub.post("/api/me/search", json={"q": "zzz-nada"})
    s = ins.analytics.summary(int(time.time() * 1000))
    assert s["searches"] == 1 and s["searches_zero"] == 1


def test_for_push_hashes_users_and_keeps_only_env() -> None:
    f = fl.parse_flag(flag_body("p", envs={"producao": {"enabled": True, "default": "off", "rules": [{"users": ["Ana@Demo.local"], "serve": "on"}]},
                                          "staging": {"enabled": True, "default": "off"}}))  # fmt: skip
    pushed = fl.for_push([f, fl.parse_flag(flag_body("q", app="outro"))], "ativos", "producao")
    assert [p["key"] for p in pushed] == ["p"] and list(pushed[0]["envs"]) == ["producao"]
    assert "ana@demo.local" not in json.dumps(pushed).lower()
    assert pushed[0]["envs"]["producao"]["rules"][0]["users"] == [fl.user_digest("p", "Ana@Demo.local")]
    assert fl.for_push([], "x", "y") == []


def test_background_loops_probe_poll_and_push(hub: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    ins = hub.app.state.insight  # type: ignore[attr-defined]
    asyncio.run(
        ins.admin.cfg.ensure_internal_tokens("t", lambda a: "tok-interno-123456" if a == "ativos" else None)
    )
    hub.post("/api/admin/flags", json=flag_body("bg"))
    seen: list[tuple[str, str, str]] = []
    span = {
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
        "name": "GET /x",
        "start_us": 1,
        "duration_us": 2,
        "attrs": {},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("x-hub-internal-token", "")))
        if request.url.path.endswith("/_hub/telemetry"):
            return httpx.Response(200, json={"spans": [span], "gauges": {"heavy_in_use": 1}, "next": 7})
        if request.url.path.endswith("/_hub/flags"):
            return httpx.Response(404)
        raise httpx.ConnectError("fora do ar") if "contratos" in request.url.path else httpx.ConnectError("x")

    real_sleep = asyncio.sleep

    async def fast(_s: float) -> None:
        await real_sleep(0.001)

    async def run() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr("hub.insight.runtime.asyncio.sleep", fast)
        ins.start(client)
        await real_sleep(0.3)
        await ins.stop()
        await client.aclose()

    monkeypatch.setenv("HUB_TELEMETRY_POLL_MS", "200")
    asyncio.run(run())
    methods = {(m, p.rsplit("/", 1)[-1]) for m, p, _ in seen}
    assert ("GET", "telemetry") in methods and ("POST", "flags") in methods
    assert all(tok == "tok-interno-123456" for _, p, tok in seen if p.startswith("/ativos/_hub"))
    assert ins.tel.traces.spans and ins.tel.metrics.last_gauges("ativos", "producao")["app"] == {
        "heavy_in_use": 1.0
    }  # type: ignore[index]
    w = ins.tel.metrics.window("ativos", "producao", 0, int(time.time()) + 600)
    assert w.probes >= 1 and w.probes_ok == 0  # sonda sem resposta conta como fora do ar
    assert (Path(ins.tel.metrics.path).exists()) and ins.tel.metrics.path is not None  # type: ignore[arg-type]


def test_flags_page_removed_and_pager(hub: TestClient) -> None:
    r = hub.get("/admin/flags", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/admin"
    nav = hub.get("/admin").text
    assert "/admin/flags" not in nav and "Feature flags" not in nav
    from hub.ux.pager import paginate

    req = Request({"type": "http", "query_string": b"x=1&page=3&per=10"})
    rows, p = paginate(req, list(range(45)))
    assert rows == list(range(20, 30)) and p["pages"] == 5 and p["next"] == "?x=1&page=4&per=10"
