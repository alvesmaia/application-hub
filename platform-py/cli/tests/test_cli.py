import io
import json
import os
import zipfile
from pathlib import Path

import httpx
import pytest

from hub_cli import mcp
from hub_cli.main import main
from hub_cli.pack import PackError, pack

IMG = "hub-x@sha256:" + "a" * 64


def app(d: Path) -> Path:
    d.mkdir(exist_ok=True)
    (d / "ui").mkdir()
    (d / "ui" / "index.html").write_text("<p>oi</p>")
    (d / "manifest.json").write_text(json.dumps({"id": "x", "version": "1.0.0"}))
    return d


def test_pack_layout_and_checksums(tmp_path: Path):
    d = app(tmp_path / "a")
    (d / "openapi.json").write_text("{}")
    out = tmp_path / "o.hubapp"
    assert len(pack(d, IMG, out)) == 64
    z = zipfile.ZipFile(out)
    assert z.namelist() == ["manifest.json", "runtime.json", "openapi.json", "ui/index.html", "checksums.txt"]
    assert json.loads(z.read("runtime.json")) == {"image": IMG}
    assert b"ui/index.html" in z.read("checksums.txt")


def test_pack_errors(tmp_path: Path):
    with pytest.raises(PackError, match="não encontrado"):
        pack(tmp_path, IMG, tmp_path / "o")
    d = app(tmp_path / "a")
    with pytest.raises(PackError, match="digest"):
        pack(d, "hub-x", tmp_path / "o")
    (d / "manifest.json").write_text('{"id":"x","version":"1","api":{"enabled":true}}')
    with pytest.raises(PackError, match=r"openapi.json"):
        pack(d, IMG, tmp_path / "o")
    (d / "manifest.json").write_text("{")
    with pytest.raises(PackError, match="inválido"):
        pack(d, IMG, tmp_path / "o")
    (d / "manifest.json").write_text("{}")
    with pytest.raises(PackError, match="id/version"):
        pack(d, IMG, tmp_path / "o")


def test_main(tmp_path: Path, capsys, monkeypatch):
    d = app(tmp_path / "a")
    monkeypatch.chdir(tmp_path)
    assert main(["pack", "--dir", str(d), "--image", IMG]) == 0
    assert (tmp_path / "x-1.0.0.hubapp").is_file()
    assert "x-1.0.0.hubapp" in capsys.readouterr().out
    assert main(["pack", "--dir", str(tmp_path / "nada"), "--image", IMG]) == 1
    assert main(["dev", "--upstream", "http://x"]) == 2
    monkeypatch.delenv("HUB_TOKEN", raising=False)
    assert main(["mcp"]) == 1
    monkeypatch.setenv("HUB_TOKEN", "t")
    assert main(["mcp", "--hub", "http://h.local"]) == 1


def test_url_and_token(tmp_path: Path):
    for ok in ["https://h.local", "http://localhost:8080", "http://127.0.0.1:1", "http://[::1]:8080"]:
        mcp.check_hub_url(ok)
    for bad in ["http://h.local", "http://10.0.0.5", "ftp://x", "nada", "https://"]:
        with pytest.raises(ValueError):
            mcp.check_hub_url(bad)
    f = tmp_path / "t"
    f.write_text("hubt_f\nx")
    assert mcp.resolve_token("c", f, "e") == ("hubt_f", None)
    assert mcp.resolve_token("c", None, " hubt_e ") == ("hubt_e", None)
    t, w = mcp.resolve_token("hubt_c", None, None)
    assert t == "hubt_c" and w and "processos" in w
    for args in [(None, None, "  "), (None, tmp_path / "nope", None)]:
        with pytest.raises(ValueError):
            mcp.resolve_token(*args)
    f.write_text("\n")
    with pytest.raises(ValueError):
        mcp.resolve_token(None, f, None)


def test_sse_and_header_safe():
    body = ': keep\n\ndata: {"a":1}\n\r\ndata: {"b":\ndata: 2}\n\ndata: lixo\n\n'
    assert mcp.sse_messages(body) == [{"a": 1}, {"b": 2}]
    assert mcp.header_safe("ok") == "ok"
    assert mcp.header_safe("açaí").startswith("=?base64?")
    assert mcp.header_safe("") == "=?base64??="


def fake_hub(seen: list[httpx.Request], mode: str = "ok") -> httpx.Client:
    def h(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        body = json.loads(r.content)
        m = body.get("method")
        if mode == "404" or m == "gone":
            return httpx.Response(404)
        if m == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18"}},
                headers={"mcp-session-id": "S1"},
            )
        if m == "notifications/initialized":
            return httpx.Response(202)
        sse = {"content-type": "text/event-stream"}
        if m == "sse":
            t = 'data: {"jsonrpc":"2.0","method":"p"}\n\ndata: {"jsonrpc":"2.0","id":3,"result":{}}\n\n'
            return httpx.Response(200, text=t, headers=sse)
        if m == "empty":
            return httpx.Response(200, text="", headers=sse)
        if m == "plain":
            return httpx.Response(401, json={"error": "token"})
        if m == "html":
            return httpx.Response(500, text="<x>")
        if m == "boom":
            raise httpx.ConnectError("x")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}})

    return httpx.Client(transport=httpx.MockTransport(h))


def test_bridge_flow():
    seen: list[httpx.Request] = []
    b = mcp.Bridge("http://localhost:1", "tk", "mcp", fake_hub(seen))
    assert b.url == "http://localhost:1/mcp"
    assert b.forward("{")[1][0]["error"]["code"] == -32700
    assert b.forward("[]")[1][0]["error"]["code"] == -32600
    assert b.forward('{"jsonrpc":"2.0","id":1,"method":"initialize"}')[0] == "reply"
    assert b.session == "S1" and b.protocol == "2025-06-18"
    assert b.forward('{"jsonrpc":"2.0","method":"notifications/initialized"}') == ("accepted", [])
    assert len(b.forward('{"jsonrpc":"2.0","id":3,"method":"sse"}')[1]) == 2
    assert b.forward('{"jsonrpc":"2.0","id":4,"method":"empty"}')[1][0]["error"]["code"] == -32603
    assert "token" in b.forward('{"jsonrpc":"2.0","id":5,"method":"plain"}')[1][0]["error"]["message"]
    assert "500" in b.forward('{"jsonrpc":"2.0","id":6,"method":"html"}')[1][0]["error"]["message"]
    assert b.forward('{"jsonrpc":"2.0","method":"html"}') == ("accepted", [])
    assert "inacessível" in b.forward('{"jsonrpc":"2.0","id":7,"method":"boom"}')[1][0]["error"]["message"]
    assert b.forward('{"jsonrpc":"2.0","id":8,"method":"gone"}')[0] == "reply"
    assert b.session is None
    last = seen[1]
    assert last.headers["authorization"] == "Bearer tk"
    assert last.headers["mcp-session-id"] == "S1"
    assert last.headers["mcp-protocol-version"] == "2025-06-18"


def test_new_revision_headers():
    seen: list[httpx.Request] = []
    b = mcp.Bridge("http://localhost:1", "tk", client=fake_hub(seen))
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}
    q = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "açaí", "_meta": meta}}
    b.forward(json.dumps(q))
    h = seen[-1].headers
    assert h["mcp-method"] == "tools/call" and h["mcp-name"].startswith("=?base64?")
    assert h["mcp-protocol-version"] == "2026-07-28"


def test_run_loop_and_disabled():
    out = io.StringIO()
    inp = io.StringIO(
        '\n{"jsonrpc":"2.0","id":1,"method":"initialize"}\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
    )
    assert mcp.run("http://localhost:1", "t", "/mcp", inp, out, fake_hub([])) == 0
    assert json.loads(out.getvalue())["result"]["protocolVersion"] == "2025-06-18"
    out = io.StringIO()
    inp = io.StringIO('{"id":1,"method":"initialize"}\n')
    assert mcp.run("http://localhost:1", "t", "/mcp", inp, out, fake_hub([], "404")) == 3
    assert "desligado" in out.getvalue()
    with pytest.raises(ValueError):
        mcp.run("http://localhost:1", "", "/mcp")


def _hub_up(url: str) -> bool:
    try:
        return httpx.get(url + "/healthz", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


HUBPY = os.environ.get("APP_HUB_URL", "http://localhost:8090")


@pytest.mark.skipif(not _hub_up(HUBPY), reason="app-hub indisponível (APP_HUB_URL, padrão :8090)")
def test_smoke_hubpy():
    """Sem APP_HUB_TOKEN usa um token inválido: a ponte devolve erro JSON-RPC (hub alcançado)."""
    b = mcp.Bridge(HUBPY, os.environ.get("APP_HUB_TOKEN", "hubt_invalido"))
    init = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}
    k, ms = b.forward(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": init}))
    assert k in ("reply", "disabled") and ms
    if "APP_HUB_TOKEN" in os.environ and k == "reply":
        assert ms[0].get("result")
        assert b.forward('{"jsonrpc":"2.0","id":2,"method":"tools/list"}')[1]
    else:
        assert "error" in ms[0]


def test_dev_without_platform_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    (tmp_path / "manifest.json").write_text("{}")
    monkeypatch.setitem(__import__("sys").modules, "hub", None)  # import falha como sem a plataforma
    assert main(["dev", "--dir", str(tmp_path), "--upstream", "http://localhost:5000"]) == 2
    assert "pacote da plataforma" in capsys.readouterr().err


def test_pack_build_dir(tmp_path: Path):
    d = app(tmp_path / "a")
    ctx = tmp_path / "ctx"
    (ctx / "pkg" / "__pycache__").mkdir(parents=True)
    (ctx / "Dockerfile").write_text("FROM scratch\n")
    (ctx / "pkg" / "m.py").write_text("x=1")
    (ctx / "pkg" / "__pycache__" / "m.pyc").write_bytes(b"0")
    out = tmp_path / "b.hubapp"
    pack(d, None, out, ctx)
    z = zipfile.ZipFile(out)
    assert json.loads(z.read("runtime.json")) == {"build": {"dockerfile": "Dockerfile"}}
    names = z.namelist()
    assert "build/Dockerfile" in names and "build/pkg/m.py" in names and not any("pyc" in n for n in names)
    assert b"build/pkg/m.py" in z.read("checksums.txt")
    with pytest.raises(PackError, match="Outro"):
        pack(d, None, out, ctx, "Outro")
    with pytest.raises(PackError, match="build-dir"):
        pack(d, None, out)
    assert main(["pack", "--dir", str(d), "--build-dir", str(ctx), "--out", str(out)]) == 0
