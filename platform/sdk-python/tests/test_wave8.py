"""Onda 8: traces (traceparent hub → app → banco), telemetria pelo canal interno e
feature flags (mesmo algoritmo e vetores do crate Rust ``hub-flags``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from hub_sdk import flags as fl
from hub_sdk import internal, telemetry
from hub_sdk.outbox import Outbox
from hub_sdk.telemetry import Telemetry, TraceContext, TraceMiddleware

TOKEN = "0123456789abcdef0123"
TP = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
VECTORS = Path(__file__).resolve().parent / "vectors" / "flags-vectors.json"


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def traced_app(tel: Telemetry) -> FastAPI:
    app = FastAPI()
    app.add_middleware(TraceMiddleware, telemetry=lambda: tel)
    store = fl.FlagStore()
    app.include_router(internal.router("demo", TOKEN, Outbox.memory(), None, telemetry=tel, flags=store))
    app.state.flags = store

    @app.get("/demo/x/{n}")
    async def x(n: int) -> Any:
        comment = telemetry.sql_comment()
        async with telemetry.db("SELECT x"):
            pass
        return {"comment": comment}

    return app


async def test_server_span_is_child_of_hub_and_db_span_of_server() -> None:
    tel = Telemetry("demo")
    async with client(traced_app(tel)) as c:
        r = await c.get("/demo/x/42", headers={"traceparent": TP})
        assert r.json()["comment"].startswith("/*traceparent='00-0af7651916cd43dd8448eb211c80319c-")
        # O canal interno não vira span.
        t = await c.get("/demo/_hub/telemetry?after=0", headers={internal.TOKEN_HEADER: TOKEN})
    body = t.json()
    spans = body["spans"]
    assert len(spans) == 2
    server = next(s for s in spans if s["kind"] == "server")
    db = next(s for s in spans if s["kind"] == "client")
    assert server["trace_id"] == "0af7651916cd43dd8448eb211c80319c"
    assert server["parent_id"] == "b7ad6b7169203331"
    assert server["name"] == "GET /demo/x/{id}"
    assert db["parent_id"] == server["span_id"] and db["trace_id"] == server["trace_id"]
    assert body["next"] == 2
    # Mesmo formato do hub (hub_telemetry::Span): campos obrigatórios presentes.
    for k in ("trace_id", "span_id", "name", "service", "kind", "start_us", "duration_us", "error", "attrs"):
        assert k in server


async def test_telemetry_requires_token_and_forged_traceparent_is_ignored() -> None:
    tel = Telemetry("demo")
    async with client(traced_app(tel)) as c:
        assert (await c.get("/demo/_hub/telemetry")).status_code == 401
        await c.get("/demo/x/1", headers={"traceparent": "00-zz-yy-01"})
    s, _ = tel.pending(0, 10)
    server = next(x for x in s if x["kind"] == "server")
    assert "parent_id" not in server  # inválido → raiz nova


def test_traceparent_parse() -> None:
    c = TraceContext.root()
    assert TraceContext.parse(c.header()) == c
    for bad in ["", "01-" + TP[3:], TP.upper(), "00-" + "0" * 32 + "-b7ad6b7169203331-01"]:
        assert TraceContext.parse(bad) is None


def test_bucket_vectors_match_rust() -> None:
    v = json.loads(VECTORS.read_text(encoding="utf-8"))
    for k, want in v["buckets"].items():
        key, user = k.split(":", 1)
        assert fl.bucket(key, user) == want, k


def test_flag_evaluation_and_header() -> None:
    flag = {
        "key": "novo-painel",
        "app": "ativos",
        "kind": "boolean",
        "envs": {
            "producao": {
                "enabled": True,
                "default": "off",
                "rules": [{"roles": ["Administrador"], "users": [], "serve": "on"}],
                "rollout": {"percent": 30, "serve": "on"},
            }
        },
    }
    assert fl.evaluate(flag, "producao", "x", ["Administrador"]) == "on"
    assert fl.evaluate(flag, "staging", "x", ["Administrador"]) == "off"
    on = sum(fl.evaluate(flag, "producao", f"u{i}", []) == "on" for i in range(10_000))
    assert 2800 <= on <= 3200
    assert fl.evaluate(flag, "producao", "", []) == "off"
    killed = json.loads(json.dumps(flag))
    killed["envs"]["producao"]["enabled"] = False
    assert fl.evaluate(killed, "producao", "x", ["Administrador"]) == "off"
    f = fl.from_headers({"x-hub-flags": "novo-painel=on, X=1,bad,tema=azul"})
    assert f.is_on("novo-painel") and not f.is_on("nada") and f.variant("tema") == "azul"


async def test_flags_push_endpoint() -> None:
    tel = Telemetry("demo")
    app = traced_app(tel)
    body = {
        "version": 5,
        "env": "producao",
        "flags": [
            {
                "key": "a",
                "app": "demo",
                "kind": "boolean",
                "envs": {"producao": {"enabled": True, "default": "on"}},
            }
        ],
    }
    async with client(app) as c:
        assert (await c.post("/demo/_hub/flags", json=body)).status_code == 401
        r = await c.post("/demo/_hub/flags", json=body, headers={internal.TOKEN_HEADER: TOKEN})
        assert r.json() == {"applied": True, "flags": 1, "version": 5}
        old = dict(body, version=4)
        r = await c.post("/demo/_hub/flags", json=old, headers={internal.TOKEN_HEADER: TOKEN})
        assert r.json()["applied"] is False
        bad = dict(body, env="outro")
        assert (
            await c.post("/demo/_hub/flags", json=bad, headers={internal.TOKEN_HEADER: TOKEN})
        ).status_code == 400
    assert app.state.flags.is_on("a", "u", [])


def test_user_digest_vector_and_hashed_rules_match() -> None:
    vec = json.loads(VECTORS.read_text())
    assert (
        fl.user_digest("novo-painel", "Ana@Demo.local") == vec["user_digests"]["novo-painel:Ana@Demo.local"]
    )
    flag = {
        "key": "novo-painel",
        "envs": {
            "producao": {
                "enabled": True,
                "default": "off",
                "rules": [
                    {"users": [fl.user_digest("novo-painel", "ana@demo.local")], "roles": [], "serve": "on"}
                ],
            }
        },
    }
    assert fl.evaluate(flag, "producao", "ANA@demo.local", []) == "on"
    assert fl.evaluate(flag, "producao", "bia@demo.local", []) == "off"


def test_redact_target_matches_rust_rules() -> None:
    from hub_sdk.telemetry import redact_target

    assert redact_target("/ativos/itens/42?q=segredo#x") == "/ativos/itens/:id"
    assert redact_target("/auth/reset/abc") == "/auth/reset/:redacted"
    assert redact_target("/contratos/u/ana@demo.local/perfil") == "/contratos/u/:email/perfil"
    assert redact_target("/x/0f8fad5b-d9cb-469f-a165-70867728950e") == "/x/:id"
    assert redact_target("/files/aB3dE5fG7hJ9kL1m/baixar") == "/files/:token/baixar"
    assert redact_target("/servicos/lista/ordens") == "/servicos/lista/ordens"
