"""`hub login` (OAuth device flow), token resolution, refresh rotation and `hub logout` against a fake hub."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from hub_cli import auth, deploy, mcp
from hub_cli.errors import AUTH, CliError
from hub_cli.main import main

HUB = "http://localhost:9"
REAL = httpx.Client


class FakeOAuthHub:
    """Scripted hub: device authorization, token polls (one scripted answer per poll), refresh, revoke."""

    def __init__(self, polls: list[str] | None = None) -> None:
        self.polls = list(polls or [])
        self.seen: list[httpx.Request] = []
        self.forms: list[dict[str, str]] = []
        self.refresh_status = 200
        self.rotation = 0
        self.device_status = 200
        self.api_401_for: set[str] = set()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeOAuthHub:
        def client(**kw: Any) -> httpx.Client:
            return REAL(transport=httpx.MockTransport(self.handle), **kw)

        monkeypatch.setattr(auth.httpx, "Client", client)
        return self

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.seen.append(req)
        path = req.url.path
        if path.startswith("/oauth/"):
            form = dict(httpx.QueryParams(req.content.decode()))
            self.forms.append(form)
            route = {
                "device_authorization": self.device_authorization,
                "token": self.token,
                "revoke": self.revoke,
            }
            return route[path.removeprefix("/oauth/")](form)
        if req.headers.get("authorization", "").removeprefix("Bearer ") in self.api_401_for:
            return httpx.Response(401)
        return httpx.Response(201, json={"ok": True, "message": "implantada"})

    def device_authorization(self, form: dict[str, str]) -> httpx.Response:
        if self.device_status != 200:
            return httpx.Response(self.device_status)
        return httpx.Response(200, json={
            "device_code": "dev-1", "user_code": "BCDF-GHJK", "verification_uri": f"{HUB}/oauth/device",
            "verification_uri_complete": f"{HUB}/oauth/device?user_code=BCDF-GHJK", "expires_in": 600,
            "interval": 5})  # fmt: skip

    def token(self, form: dict[str, str]) -> httpx.Response:
        if form["grant_type"] == "refresh_token":
            if self.refresh_status != 200:
                return httpx.Response(self.refresh_status, json={"error": "invalid_grant"})
            self.rotation += 1
            return self.tokens(f"hubo_r{self.rotation}", f"hubr_r{self.rotation}")
        answer = self.polls.pop(0)
        if answer == "ok":
            return self.tokens("hubo_1", "hubr_1")
        return httpx.Response(400, json={"error": answer, "error_description": "x"})

    def revoke(self, form: dict[str, str]) -> httpx.Response:
        return httpx.Response(200)

    @staticmethod
    def tokens(access: str, refresh: str) -> httpx.Response:
        return httpx.Response(200, json={"access_token": access, "refresh_token": refresh, "expires_in": 3600,
                                         "token_type": "Bearer", "scope": auth.SCOPE})  # fmt: skip


class FakeClock:
    def __init__(self) -> None:
        self.slept: list[float] = []

    def sleep(self, s: float) -> None:
        self.slept.append(s)


@pytest.fixture(autouse=True)
def creds(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr(auth.time, "sleep", clock.sleep)
    monkeypatch.setattr(auth.webbrowser, "open", lambda url: True)
    return clock


def saved() -> dict[str, Any]:
    return json.loads(auth.cred_path().read_text(encoding="utf-8"))


def save_oauth(expires_in: float = 3600, refresh: str = "hubr_1") -> None:
    auth.save_login({"hub": HUB, "access_token": "hubo_1", "refresh_token": refresh,
                     "expires_at": time.time() + expires_in})  # fmt: skip


def test_device_flow_pending_slow_down_then_approved(creds: FakeClock, monkeypatch: pytest.MonkeyPatch,
                                                     capsys: Any) -> None:  # fmt: skip
    hub = FakeOAuthHub(["authorization_pending", "slow_down", "ok"]).install(monkeypatch)
    opened: list[str] = []
    monkeypatch.setattr(auth.webbrowser, "open", opened.append)
    assert main(["login", "--hub", HUB]) == 0
    out = capsys.readouterr()
    assert f"Abra {HUB}/oauth/device?user_code=BCDF-GHJK e confirme o código BCDF-GHJK" in out.err
    assert f"{HUB}/oauth/device" in out.err and "hubo_" not in out.err + out.out
    assert "hubr_" not in out.err + out.out and HUB in out.out
    assert opened == [f"{HUB}/oauth/device?user_code=BCDF-GHJK"]
    assert creds.slept == [5, 5, 10]  # slow_down adds 5 s
    assert hub.forms[0] == {"client_id": "hub-cli", "scope": "mcp api:read api:write app-api deploy"}
    assert hub.forms[1] == {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                            "device_code": "dev-1", "client_id": "hub-cli"}  # fmt: skip
    s = saved()
    assert (s["hub"], s["access_token"], s["refresh_token"]) == (HUB, "hubo_1", "hubr_1")
    assert s["expires_at"] > time.time() + 3000
    assert "token" not in s


def test_no_browser_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeOAuthHub(["ok"]).install(monkeypatch)
    opened: list[str] = []
    monkeypatch.setattr(auth.webbrowser, "open", opened.append)
    assert main(["login", "--hub", HUB, "--no-browser"]) == 0
    assert opened == []


@pytest.mark.parametrize(("answer", "words"), [("access_denied", "negado"), ("expired_token", "expirou")])
def test_device_flow_denied_or_expired(answer: str, words: str, monkeypatch: pytest.MonkeyPatch,
                                       capsys: Any) -> None:  # fmt: skip
    FakeOAuthHub(["authorization_pending", answer]).install(monkeypatch)
    assert main(["login", "--hub", HUB]) == AUTH
    assert words in capsys.readouterr().err
    assert not auth.cred_path().exists()


def test_device_flow_disabled_on_hub(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    hub = FakeOAuthHub().install(monkeypatch)
    hub.device_status = 404
    assert main(["login", "--hub", HUB]) == AUTH
    assert "HUB_TOKEN" in capsys.readouterr().err


def test_refresh_rotation_is_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = FakeOAuthHub().install(monkeypatch)
    save_oauth(expires_in=30)  # expires within 60 s: refresh before use
    s = auth.resolve(None, None)
    assert (s.hub, s.token) == (HUB, "hubo_r1")
    assert (saved()["access_token"], saved()["refresh_token"]) == ("hubo_r1", "hubr_r1")
    assert hub.forms[-1] == {"grant_type": "refresh_token", "refresh_token": "hubr_1", "client_id": "hub-cli"}
    s.refresh()
    assert hub.forms[-1]["refresh_token"] == "hubr_r1"  # never the dead one
    assert saved()["refresh_token"] == "hubr_r2"


def test_refresh_adopts_pair_rotated_by_another_process(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = FakeOAuthHub().install(monkeypatch)
    save_oauth()
    s = auth.resolve(None, None)
    save_oauth(refresh="hubr_other")  # e.g. `hub deploy` refreshed while `hub mcp` was running
    s.refresh()
    assert hub.forms == [] and s.login is not None and s.login["refresh_token"] == "hubr_other"


def test_refresh_refused_asks_for_login_again(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeOAuthHub().install(monkeypatch).refresh_status = 400
    save_oauth(expires_in=0)
    with pytest.raises(CliError) as e:
        auth.resolve(None, None)
    assert e.value.code == AUTH and str(e.value) == auth.MSG_EXPIRED


def test_no_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(CliError) as e:
        auth.resolve(HUB, None)
    assert e.value.code == AUTH
    assert str(e.value) == "não autenticado: rode `hub login` (abre o navegador para você aprovar o acesso)"
    save_oauth()
    with pytest.raises(CliError) as e:  # the saved login belongs to another hub: never sent there
        auth.resolve("https://outro.example", None)
    assert e.value.code == AUTH


def test_hub_token_wins_over_saved_login(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = FakeOAuthHub().install(monkeypatch)
    save_oauth(expires_in=0)
    monkeypatch.setenv("HUB_TOKEN", "hubt_ci")
    s = auth.resolve(None, None)
    assert s.token == "hubt_ci" and s.login is None and hub.forms == []


def test_legacy_saved_token_still_works() -> None:
    auth.save_login({"hub": HUB, "token": "hubt_old"})
    s = auth.resolve(None, None)
    assert (s.token, s.login) == ("hubt_old", None)


def test_deploy_refreshes_on_401_and_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_deploy import app_dir

    hub = FakeOAuthHub().install(monkeypatch)
    hub.api_401_for = {"hubo_1"}  # revoked/expired early on the hub side
    save_oauth()
    out = deploy.deploy(app_dir(tmp_path), "staging", None, None, None, False)
    assert out["ok"] and saved()["access_token"] == "hubo_r1"
    assert [r.headers["authorization"] for r in hub.seen if r.url.path == "/api/ops/packages"] == [
        "Bearer hubo_1", "Bearer hubo_r1"]  # fmt: skip


def test_logout_revokes_and_deletes(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    hub = FakeOAuthHub().install(monkeypatch)
    save_oauth()
    assert main(["logout"]) == 0
    assert hub.forms == [{"client_id": "hub-cli", "token": "hubr_1"}]
    assert hub.seen[0].url.path == "/oauth/revoke" and not auth.cred_path().exists()
    assert main(["logout"]) == 0 and "nenhum login" in capsys.readouterr().out


def test_logout_is_best_effort_when_hub_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(**kw: Any) -> httpx.Client:
        def boom(r: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("x")

        return REAL(transport=httpx.MockTransport(boom), **kw)

    monkeypatch.setattr(auth.httpx, "Client", down)
    save_oauth()
    assert main(["logout"]) == 0 and not auth.cred_path().exists()


def test_mcp_refreshes_after_401_and_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeOAuthHub().install(monkeypatch)
    save_oauth()
    sent: list[str] = []

    def mcp_hub(r: httpx.Request) -> httpx.Response:
        sent.append(r.headers["authorization"])
        if r.headers["authorization"] == "Bearer hubo_1":
            return httpx.Response(401)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    out = io.StringIO()
    session = auth.resolve(None, None)
    inp = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n')
    assert mcp.run(session, "/mcp", inp, out, REAL(transport=httpx.MockTransport(mcp_hub))) == 0
    assert sent == ["Bearer hubo_1", "Bearer hubo_r1"]
    assert json.loads(out.getvalue())["result"] == {}


def test_mcp_refresh_refused_becomes_rpc_error(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeOAuthHub().install(monkeypatch).refresh_status = 400
    save_oauth()
    b = mcp.Bridge(
        auth.resolve(None, None), client=REAL(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    )
    _, msgs = b.forward('{"jsonrpc":"2.0","id":1,"method":"tools/list"}')
    assert msgs[0]["error"]["message"] == auth.MSG_EXPIRED


@pytest.mark.skipif(not hasattr(__import__("os"), "getuid"), reason="POSIX permissions")
def test_login_file_is_owner_only() -> None:
    save_oauth()
    assert auth.cred_path().stat().st_mode & 0o777 == 0o600
