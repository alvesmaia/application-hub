import asyncio
import json
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import uvicorn
from auth_helper import client_for
from starlette.testclient import TestClient

from hub import devapp, devmode
from hub.app import create_app
from hub.insight import otlp


def span(parent: str | None = None, err: bool = False) -> dict[str, Any]:
    return {
        "trace_id": "a" * 32, "span_id": "b" * 16, "parent_id": parent, "name": "GET /x", "service": "hub",
        "kind": "server", "start_us": 1_000, "duration_us": 500, "error": err, "attrs": {"hub.app": "x"},
    }  # fmt: skip


def test_otlp_endpoint_and_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "ftp://x")
    assert otlp.endpoint_from_env() == ""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", " http://c:4318/ ")
    assert otlp.endpoint_from_env() == "http://c:4318"
    body = otlp.traces_body([span(), span("c" * 16, True)], "app-hub")
    sp = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert body["resourceSpans"][0]["resource"]["attributes"][0]["value"]["stringValue"] == "app-hub"
    assert sp[0]["startTimeUnixNano"] == "1000000" and sp[0]["kind"] == 2 and "parentSpanId" not in sp[0]
    assert sp[1]["status"]["code"] == 2 and sp[1]["parentSpanId"] == "c" * 16


def test_exporter_flushes_and_retries() -> None:
    posted: list[tuple[str, dict[str, Any]]] = []
    fail = {"on": True}

    def handler(req: httpx.Request) -> httpx.Response:
        if fail["on"]:
            return httpx.Response(500)
        posted.append((req.url.path, json.loads(req.content)))
        return httpx.Response(200)

    async def go() -> None:
        ex = otlp.Exporter("http://c:4318", "hub")
        ex.on_span(span())
        ex.on_observe("x", "producao", 200, 12.0)
        ex.on_observe("x", "producao", 503, 700.0)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await ex.flush_traces(c)
            assert len(ex.spans) == 1  # falhou: devolvido à fila
            fail["on"] = False
            await ex.flush_traces(c)
            await ex.flush_metrics(c)
            await ex.flush_traces(c)  # fila vazia: nada a enviar

    asyncio.run(go())
    assert [p for p, _ in posted] == ["/v1/traces", "/v1/metrics"]
    m = posted[1][1]["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    assert [x["name"] for x in m] == [
        "hub.http.server.requests",
        "hub.http.server.errors",
        "hub.http.server.duration",
    ]
    assert m[0]["sum"]["dataPoints"][0]["asInt"] == "2" and m[1]["sum"]["dataPoints"][0]["asInt"] == "1"
    assert sum(int(c) for c in m[2]["histogram"]["dataPoints"][0]["bucketCounts"]) == 2


def test_exporter_run_and_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_OTLP_TRACES_S", "0.01")
    hits: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        hits.append(req.url.path)
        return httpx.Response(200)

    async def go() -> None:
        ex = otlp.Exporter("http://c:4318")
        ex.on_span(span())
        ex.on_observe("x", "producao", 200, 1.0)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ex.task = asyncio.ensure_future(ex.run(client))
        await asyncio.sleep(0.4)
        await ex.stop()
        await client.aclose()

    asyncio.run(go())
    assert "/v1/traces" in hits and "/v1/metrics" in hits


def test_page_shows_otlp_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert client_for(create_app()).get("/admin/health").status_code == 200
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://app-hub-otel-collector:4318")
    app = create_app()
    assert app.state.insight.otlp is not None
    monkeypatch.setenv("HUB_TELEMETRY", "0")
    assert create_app().state.insight.otlp is None


def test_dev_production_markers_and_check(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {
        "HUB_DEPLOYMENT": "production",
        "HUB_DEMO_USERS": "0",
        "HUB_SSO_ENABLED": "true",
        "HUB_MASTER_KEY_FILE": "/k",
    }
    assert devmode.production_markers(env.get) == [
        "HUB_DEPLOYMENT=production", "HUB_DEMO_USERS=0", "HUB_SSO_ENABLED", "HUB_MASTER_KEY_FILE",
    ]  # fmt: skip
    assert devmode.production_markers({"HUB_DEMO_USERS": "1"}.get) == []
    for k in env:
        monkeypatch.delenv(k, raising=False)
    devmode.startup_check()  # fora do modo dev: nada
    monkeypatch.setenv("HUB_DEV_MODE", "1")
    devmode.startup_check()
    monkeypatch.setenv("HUB_DEPLOYMENT", "production")
    with pytest.raises(SystemExit, match="RECUSADO"):
        devmode.startup_check()


def test_watch_parse_and_fingerprint(tmp_path: Path) -> None:
    w = devmode.parse_watch("/a, /b@2500 ,,/c@x")
    assert w == [(Path("/a"), 0.0), (Path("/b"), 2.5), (Path("/c@x"), 0.0)]
    (tmp_path / "s").mkdir()
    (tmp_path / "s/a.css").write_text("a{}")
    (tmp_path / ".hidden").write_text("x")
    f1 = devmode.fingerprint(tmp_path)
    assert f1 == devmode.fingerprint(tmp_path)
    (tmp_path / "s/a.css").write_text("a{color:red}")
    assert f1 != devmode.fingerprint(tmp_path)


def test_reload_route_only_in_dev_with_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HUB_DEV_MODE", raising=False)
    off = client_for(create_app())
    assert off.get("/_dev/reload").status_code == 404
    assert "hub-dev-reload" not in off.get("/").text
    monkeypatch.setenv("HUB_DEV_MODE", "1")
    monkeypatch.setenv("HUB_DEV_RELOAD", "1")
    on = client_for(create_app())
    assert 'name="hub-dev-reload"' in on.get("/").text
    rl = devmode.Reloader()

    async def go() -> list[str]:
        out: list[str] = []
        gen = rl.stream()
        assert isinstance(gen, AsyncGenerator)
        out.append(await anext(gen))
        rl.notify()
        out.append(await anext(gen))
        await gen.aclose()
        return out

    got = asyncio.run(go())
    assert got[0].startswith("event: hello") and got[1].startswith("event: reload") and not rl.subs


def test_watcher_notifies_on_change(tmp_path: Path) -> None:
    rl = devmode.Reloader()

    async def go() -> bool:
        q: asyncio.Queue[str] = asyncio.Queue()
        rl.subs.add(q)
        task = asyncio.ensure_future(rl.watch([(tmp_path, 0.0)], every=0.02))
        await asyncio.sleep(0.1)
        (tmp_path / "n.js").write_text("1")
        try:
            await asyncio.wait_for(q.get(), 2)
            return True
        finally:
            task.cancel()

    assert asyncio.run(go())


def test_mount_start_watch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HUB_DEV_MODE", "1")
    monkeypatch.setenv("HUB_DEV_RELOAD", "1")
    monkeypatch.setenv("HUB_DEV_WATCH", str(tmp_path))
    app = create_app()

    async def go() -> None:
        devmode.mount(app)()
        app.state.dev_watch.cancel()

    asyncio.run(go())


def mk_app(d: Path, env: str = "") -> Path:
    d.mkdir()
    (d / ".hub").mkdir()
    (d / "manifest.json").write_text(
        json.dumps({"id": "demo", "name": "Demo", "version": "1.0.0", "env": ["API_URL"]})
    )
    if env:
        (d / ".hub/dev.env").write_text(env)
    return d


def test_dev_env_and_load_app(tmp_path: Path) -> None:
    d = mk_app(tmp_path / "a", "# c\n\nAPI_URL = http://x\nSECRET=1\n")
    entry, headers, keys, ignored = devapp.load_app(d, "http://localhost:5000/")
    assert (entry.id, entry.upstream, keys, ignored) == (
        "demo",
        "http://localhost:5000",
        ["API_URL"],
        ["SECRET"],
    )
    assert headers == [("x-hub-env-api_url", "http://x")]
    assert devapp.load_env(tmp_path, []) == ([], [], [])
    (d / ".hub/dev.env").write_text("sem-igual")
    with pytest.raises(ValueError, match="inválida"):
        devapp.load_app(d, "http://u")
    with pytest.raises(ValueError, match=r"manifest.json"):
        devapp.load_app(tmp_path, "http://u")


def test_dev_app_serves_shell_and_proxies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = mk_app(tmp_path / "a", "API_URL=http://x\n")
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path / "data"))
    seen: dict[str, str] = {}

    def upstream(req: httpx.Request) -> httpx.Response:
        seen.update(dict(req.headers))
        return httpx.Response(200, stream=httpx.ByteStream(b"<p>fragmento</p>"))

    asgi, entry = devapp.dev_asgi(d, "http://app.test", "admin")
    assert entry.id == "demo"
    cast(Any, asgi).inner.state.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    with TestClient(asgi) as c:
        r = c.get("/apps/demo")
        assert r.status_code == 200 and "Desenvolvedor (hub dev)" in r.text
        f = c.get("/_frag/demo/")
        assert f.status_code == 200 and "fragmento" in f.text
    assert seen["x-hub-env-api_url"] == "http://x" and "x-hub-user" in seen


def test_cli_dev_runs_with_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "cli"))
    from hub_cli import dev as cli_dev

    d = mk_app(tmp_path / "a", "API_URL=1\nOUTRA=2\n")
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path / "data"))
    called: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: called.update(kw))
    rc = cli_dev.run(d, "http://localhost:5000", 4999, "admin", True, [sys.executable, "-c", "pass"])
    assert rc == 0 and called["port"] == 4999 and called["host"] == "127.0.0.1"
    entry = devapp.load_app(d, "http://u")[0]
    assert "hub dev" in cli_dev.banner(devapp, entry, ["A"], ["B"], 1, "admin", "http://u")
