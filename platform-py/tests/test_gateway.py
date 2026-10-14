from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from auth_helper import SESSION, client_for
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hub.app import create_app
from hub.gateway.csp import csp_with
from hub.gateway.identity import Identity, sign_user, verify_user
from hub.gateway.proxy import (
    Gateway,
    GwOptions,
    authorization_allowed,
    filter_request_cookies,
    public_location,
    sanitize_set_cookie,
    targets_internal,
)
from hub.gateway.registry import AppEntry, Registry

KEY = b"k" * 32


# --- identity: vectors computed with openssl, independent of this code ----------------


def test_signature_golden() -> None:
    sig = sign_user(KEY, "ativos", "producao", "abc", 1700000000)
    assert sig == "v1.1700000000.e86e8a642ef0ee6998d08beff4e12db675c26c9e85c74083c5643211a314c351"
    # one app's signature is not valid for another, nor for another environment
    assert sign_user(KEY, "contratos", "producao", "abc", 1700000000) != sig
    assert sign_user(KEY, "ativos", "staging", "abc", 1700000000) != sig


def test_identity_header_golden() -> None:
    ident = Identity(
        "ana@corp.com", ["Administrador"], id="u1", name="Ana", email="ana@corp.com", permissions=["a"]
    )
    assert ident.header_value() == (
        "eyJ1c2VyIjoiYW5hQGNvcnAuY29tIiwicm9sZXMiOlsiQWRtaW5pc3RyYWRvciJdLCJpZCI6InUxIiwibmFtZSI6IkFuYSIsImVtYWls"
        "IjoiYW5hQGNvcnAuY29tIiwicGVybWlzc2lvbnMiOlsiYSJdfQ"
    )
    assert (
        Identity("a", ["r"]).header_value() == "eyJ1c2VyIjoiYSIsInJvbGVzIjpbInIiXX0"
    )  # {"user":"a","roles":["r"]}


def test_verify_user_window() -> None:
    sig = sign_user(KEY, "ativos", "producao", "abc", 1000)
    assert verify_user(KEY, "ativos", "producao", "abc", sig, now=1100)
    assert not verify_user(KEY, "ativos", "producao", "abc", sig, now=1000 + 901)
    assert not verify_user(KEY, "ativos", "producao", "abc", sig, now=900)  # > 60 s in the future
    assert not verify_user(KEY, "contratos", "producao", "abc", sig, now=1100)
    assert not verify_user(KEY, "ativos", "producao", "abc", "lixo", now=1100)


# --- pure functions ----


def test_public_location() -> None:
    assert public_location("ativos", "/ativos/assets?x=1") == "/apps/ativos/assets?x=1"
    assert public_location("ativos", "/ativos") == "/apps/ativos"
    assert public_location("ativos", "/ativosx/a") is None
    assert public_location("ativos", "/apps/ativos/a") is None
    assert public_location("ativos", "https://x/ativos") is None


def test_cookies_auth_internal() -> None:
    assert filter_request_cookies("a=1; hub_session=x; __Host-hub_sso=2; b=3") == ["a=1", "b=3"]
    assert sanitize_set_cookie("hub_session=x") is None
    assert sanitize_set_cookie("a=1; Domain=evil.com; Path=/; HttpOnly") == "a=1; Path=/; HttpOnly"
    assert not authorization_allowed("Bearer hubt_abc", ("Bearer",))
    assert not authorization_allowed("Bearer abc", ())
    assert authorization_allowed("Bearer abc", ("bearer",))
    assert targets_internal("/ativos/_hub/outbox")
    assert targets_internal("/ativos/%5Fhub/outbox")
    assert not targets_internal("/ativos/hub")


def test_csp_app_page() -> None:
    csp = csp_with("http://h", "ativos", [("a.js", "sha384-xyz")])
    assert "script-src http://h/assets/ 'sha384-xyz' http://h/_frag/ativos/assets/a.js;" in csp
    assert "ws://h/apps/ativos/ws/" in csp
    assert "form-action http://h/apps/ativos http://h/apps/ativos/ http://h/logout" in csp
    assert "require-trusted-types-for 'script'; trusted-types hub-parse hub-svg hub-script default" in csp
    assert csp_with(None, "ativos", [("a.js", "sha384-xyz")]).count("'self'") >= 1
    assert "form-action 'none'" in csp_with(None, "ativos")
    assert "trusted-types" not in csp_with("http://h", None)


# --- HTTP proxy (in-memory ASGI upstream) ----


def make_upstream() -> FastAPI:
    up = FastAPI()

    @up.api_route("/ativos/echo", methods=["GET", "POST"])
    async def echo(request: Request) -> JSONResponse:
        body = await request.body()
        resp = JSONResponse(
            {
                "headers": dict(request.headers),
                "body_len": len(body),
                "query": request.url.query,
                "path": request.url.path,
            }
        )
        resp.headers.append("set-cookie", "hub_session=evil; Path=/")
        resp.headers.append("set-cookie", "ok=1; Domain=evil.com; Path=/")
        resp.headers["x-hub-secret"] = "no"
        return resp

    @up.get("/ativos/go")
    async def go() -> RedirectResponse:
        return RedirectResponse("/ativos/dest?a=1", status_code=302)

    @up.get("/ativos/stream")
    async def stream() -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            for i in range(5):
                yield f"chunk{i};".encode()
                await asyncio.sleep(0)

        return StreamingResponse(gen(), media_type="text/plain")

    @up.get("/ativos/slow")
    async def slow() -> JSONResponse:
        await asyncio.sleep(2)
        return JSONResponse({})

    return up


def make_hub(transport: httpx.AsyncBaseTransport, **opts: Any) -> tuple[TestClient, Gateway]:
    reg = Registry([AppEntry("ativos", "Gestão de Ativos", "http://up", max_upload_mb=1)])
    gw = Gateway(reg, GwOptions(user_sig_key=KEY, **opts), transport=transport)
    return client_for(create_app(gateway=gw)), gw


@pytest.fixture
def hub() -> Iterator[TestClient]:
    c, _ = make_hub(httpx.ASGITransport(app=make_upstream()))
    yield c


def test_proxy_headers_and_identity(hub: TestClient) -> None:
    r = hub.post(
        "/_frag/ativos/echo?x=1",
        content=b"abc",
        headers={
            "sec-fetch-site": "same-origin",
            "x-hub-user": "forjado",
            "x-hub-env": "staging",
            "cookie": f"hub_session={SESSION}; tema=1",
            "traceparent": "00-x",
        },
    )
    assert r.status_code == 200
    j = r.json()
    h = j["headers"]
    assert j["path"] == "/ativos/echo" and j["query"] == "x=1" and j["body_len"] == 3
    assert h["x-hub-base"] == "/apps/ativos" and h["x-hub-env"] == "producao"
    assert h["x-hub-user"] != "forjado" and h["x-hub-user-sig"].startswith("v1.")
    assert h["x-hub-locale"] == "pt-BR"  # the user's language goes to the app (preference or Accept-Language)
    assert verify_user(KEY, "ativos", "producao", h["x-hub-user"], h["x-hub-user-sig"])
    assert h["cookie"] == "tema=1"
    assert "authorization" not in h and h.get("traceparent") != "00-x"
    # response: x-hub-* and the hub's cookies are dropped; Domain is removed
    assert "x-hub-secret" not in r.headers
    assert r.headers.get_list("set-cookie") == ["ok=1; Path=/"]
    csp = r.headers["content-security-policy"]
    assert csp.endswith("; sandbox") and "trusted-types" in csp


def test_api_path_strips_apps_prefix(hub: TestClient) -> None:
    r = hub.get("/apps/ativos/api/%2e%2e/x")  # travessia
    assert r.status_code == 404
    r = hub.get("/_frag/ativos/echo")
    assert r.json()["path"] == "/ativos/echo"


def test_csrf_blocks_cross_site_post(hub: TestClient) -> None:
    assert hub.post("/_frag/ativos/echo", content=b"x").status_code == 403
    r = hub.post("/_frag/ativos/echo", content=b"x", headers={"origin": "http://testserver"})
    assert r.status_code == 200


def test_internal_and_unknown(hub: TestClient) -> None:
    assert hub.get("/_frag/ativos/_hub/outbox").status_code == 404
    assert hub.get("/_frag/ativos/%5fhub/x").status_code == 404
    assert hub.get("/_frag/ativos/a%252e%252e/x").status_code == 404
    assert hub.get("/_frag/nope/x").status_code == 404


def test_upload_limit(hub: TestClient) -> None:
    big = b"x" * (1024 * 1024 + 1)
    r = hub.post("/_frag/ativos/echo", content=big, headers={"origin": "http://testserver"})
    assert r.status_code == 413
    assert "1 MB" in r.text


def test_location_rewrite_and_legacy_redirects(hub: TestClient) -> None:
    r = hub.get("/_frag/ativos/go")
    assert r.status_code == 302 and r.headers["location"] == "/apps/ativos/dest?a=1"
    r = hub.get("/ativos/assets?q=1")
    assert r.status_code == 301 and r.headers["location"] == "/apps/ativos/assets?q=1"
    r = hub.post("/ativos/api/x", content=b"1")
    assert r.status_code == 308 and r.headers["location"] == "/apps/ativos/api/x"
    assert hub.get("/healthz").status_code == 200  # hub routes are not affected
    assert hub.get("/nada/x").status_code == 404


def test_streaming_body(hub: TestClient) -> None:
    with hub.stream("GET", "/_frag/ativos/stream") as r:
        assert r.status_code == 200
        assert "".join(r.iter_text()) == "chunk0;chunk1;chunk2;chunk3;chunk4;"


def test_timeout_is_504() -> None:
    c, _ = make_hub(httpx.ASGITransport(app=make_upstream()), response_timeout=0.2)
    r = c.get("/_frag/ativos/slow")
    assert r.status_code == 504
    assert "data-hub-unavailable" in r.text and r.headers["retry-after"] == "5"


def test_upstream_down_is_502() -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("recusado")

    c, _ = make_hub(httpx.MockTransport(boom))
    assert c.get("/_frag/ativos/x").status_code == 502


def test_app_page_renders_shell() -> None:
    c = client_for(create_app())
    r = c.get("/apps/ativos")
    assert r.status_code == 200 and 'data-hub-app="ativos"' in r.text and 'data-hub-frag="/_frag"' in r.text
    assert "trusted-types" in r.headers["content-security-policy"]
    assert c.get("/apps/nope").status_code == 404


# --- WebSocket (real upstream on uvicorn in a thread) ----


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def ws_upstream() -> Iterator[int]:
    up = FastAPI()

    @up.websocket("/ativos/ws/echo")
    async def echo(ws: WebSocket) -> None:
        await ws.accept()
        await ws.send_text("user=" + ws.headers.get("x-hub-user", "")[:6])
        try:
            while True:
                m = await ws.receive_text()
                await ws.send_text(f"echo:{m}")
        except WebSocketDisconnect:
            return

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(up, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield port
    server.should_exit = True
    t.join(5)


def test_websocket_echo(ws_upstream: int) -> None:
    reg = Registry([AppEntry("ativos", "Ativos", f"http://127.0.0.1:{ws_upstream}")])
    c = client_for(create_app(gateway=Gateway(reg, GwOptions(user_sig_key=KEY))))
    hdrs = {"origin": "http://testserver", "cookie": f"hub_session={SESSION}"}
    with c.websocket_connect("/apps/ativos/ws/echo", headers=hdrs) as ws:
        assert ws.receive_text() == "user=eyJ1c2"  # x-hub-user reached the upstream
        ws.send_text("oi")
        assert ws.receive_text() == "echo:oi"
    with (
        pytest.raises(WebSocketDisconnect),
        c.websocket_connect("/apps/ativos/ws/echo", headers={"origin": "http://evil"}),
    ):
        pass


def test_websocket_in_hub_dev(ws_upstream: int, tmp_path: Any) -> None:
    """`hub dev`: the simulated user is also valid on the WebSocket (before: 1008 for lack of a cookie)."""
    import json

    from hub import devapp

    (tmp_path / "manifest.json").write_text(
        json.dumps({"id": "ativos", "name": "Ativos", "version": "1.0.0"})
    )
    asgi, _ = devapp.dev_asgi(tmp_path, f"http://127.0.0.1:{ws_upstream}", "admin")
    with (
        TestClient(asgi) as c,
        c.websocket_connect("/apps/ativos/ws/echo", headers={"origin": "http://testserver"}) as ws,
    ):
        assert ws.receive_text().startswith("user=")
        ws.send_text("oi")
        assert ws.receive_text() == "echo:oi"
