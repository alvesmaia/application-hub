"""`hub login` / `hub deploy` contra um hub simulado (httpx.MockTransport)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from hub_cli import deploy
from hub_cli.errors import CliError
from hub_cli.main import main


@pytest.fixture(autouse=True)
def creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("HUB_URL", raising=False)
    monkeypatch.delenv("HUB_TOKEN", raising=False)


REAL = httpx.Client


def fake_hub(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    def client(**kw: Any) -> httpx.Client:
        return REAL(transport=httpx.MockTransport(h), **kw)

    monkeypatch.setattr(deploy.httpx, "Client", client)
    return seen


def app_dir(tmp: Path) -> Path:
    d = tmp / "a"
    d.mkdir()
    m = {"id": "estoque", "name": "Estoque", "version": "0.1.0", "routes": ["/estoque/*"],
         "api": {"enabled": True}, "requires": {"database": True, "variables": ["API_URL"], "secrets": ["API_KEY"]}}  # fmt: skip
    (d / "manifest.json").write_text(json.dumps(m))
    (d / "openapi.json").write_text("{}")
    (d / "Dockerfile").write_text("FROM scratch\n")
    (d / ".env").write_text("SEGREDO=1\n")
    return d


def test_login_saves_and_resolves(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    seen = fake_hub(monkeypatch, lambda r: httpx.Response(200, json=[]))
    assert main(["login", "--hub", "http://localhost:9", "--token", "hubt_x", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["hub"] == "http://localhost:9"
    assert seen[0].headers["authorization"] == "Bearer hubt_x"
    assert deploy.resolve(None, None) == ("http://localhost:9", "hubt_x")
    fake_hub(monkeypatch, lambda r: httpx.Response(401))
    assert main(["login", "--hub", "http://localhost:9", "--token", "hubt_y"]) == 3
    fake_hub(monkeypatch, lambda r: httpx.Response(500))
    assert main(["login", "--hub", "http://localhost:9", "--token", "hubt_y"]) == 1


def test_resolve_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(CliError) as e:
        deploy.resolve(None, None)
    assert e.value.code == 2
    with pytest.raises(CliError) as e:
        deploy.resolve("http://localhost:1", None)
    assert e.value.code == 3
    with pytest.raises(CliError) as e:
        deploy.resolve("ftp://x", "t")
    assert e.value.code == 2
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(CliError):
        deploy.login("http://localhost:1", None)


def test_deploy_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    d = app_dir(tmp_path)
    (d / "deploy.staging.env").write_text("# valores\nAPI_URL=http://x\nAPI_KEY=segredo-123\n")
    body = {"ok": True, "message": "implantada", "provisioned": {"database": "app_estoque_staging"}}
    seen = fake_hub(monkeypatch, lambda r: httpx.Response(201, json=body))
    args = [
        "deploy",
        "--env",
        "staging",
        "--dir",
        str(d),
        "--hub",
        "http://localhost:9",
        "--token",
        "t",
        "--json",
    ]
    assert main(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["url"].endswith("/hub-env?to=staging&next=/apps/estoque") and out["secrets"] == ["API_KEY"]
    assert "segredo-123" not in json.dumps(out)
    req = seen[0]
    assert req.url.params["env"] == "staging" and req.url.params["wait"] == "true"
    raw = req.read()
    assert b'"API_KEY": "segredo-123"' in raw and b"SEGREDO=1" not in raw  # .env nunca vai no pacote
    assert main([*args[:-1]]) == 0 and "abrir:" in capsys.readouterr().out


def test_deploy_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = app_dir(tmp_path)
    base = ["deploy", "--dir", str(d), "--hub", "http://localhost:9", "--token", "t"]
    fake_hub(monkeypatch, lambda r: httpx.Response(422, json={"ok": False, "message": "Pacote rejeitado"}))
    assert main([*base, "--env", "production"]) == 1
    fake_hub(monkeypatch, lambda r: httpx.Response(403))
    assert main([*base, "--env", "staging"]) == 3

    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x")

    fake_hub(monkeypatch, boom)
    assert main([*base, "--env", "staging"]) == 4
    fake_hub(monkeypatch, lambda r: httpx.Response(502, text="gateway"))
    assert main([*base, "--env", "staging"]) == 1
    (d / "deploy.staging.env").write_text("OUTRA=1\n")
    assert main([*base, "--env", "staging"]) == 2
    (d / "deploy.staging.env").write_text("sem-igual\n")
    assert main([*base, "--env", "staging"]) == 2
    with pytest.raises(CliError):
        deploy.deploy(d, "qa", "http://localhost:9", "t", None, False)
    (d / "manifest.json").write_text(json.dumps({"id": "X"}))
    assert main([*base, "--env", "staging"]) == 2


def test_collect_values_prompts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    m = {"requires": {"variables": ["A"], "secrets": ["B"]}}
    monkeypatch.setattr("builtins.input", lambda _p: "va")
    monkeypatch.setattr(deploy.getpass, "getpass", lambda _p: "")
    assert deploy.collect_values(m, None, True) == {"variables": {"A": "va"}, "secrets": {}}
    assert deploy.collect_values(m, tmp_path / "nada.env", False) == {"variables": {}, "secrets": {}}
    assert deploy.cred_path().name == "credentials.json"
    assert deploy.load_login() == {}


def test_deploy_shows_hub_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A recusa do hub vem em `error` (ex.: pacote grande): antes a CLI mostrava só o código HTTP."""
    d = app_dir(tmp_path)
    base = ["deploy", "--dir", str(d), "--hub", "http://localhost:9", "--token", "t", "--env", "staging"]
    msg = "O pacote tem 22.0 MB; o limite é 20 MB."
    fake_hub(monkeypatch, lambda r: httpx.Response(400, json={"error": msg}))
    with pytest.raises(CliError) as e:
        deploy.deploy(d, "staging", "http://localhost:9", "t", None, False)
    assert msg in str(e.value)
    assert main(base) == 1
