"""MCP OAuth 2.1: discovery, registration, consent, PKCE, refresh rotation, revocation and CORS."""

import base64
import hashlib
import json
import re
import secrets
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from api_helper import add_user, api_hub, bearer, make_token, rpc
from fastapi.testclient import TestClient

from hub.api import oauth as oa

ISS = "http://testserver"
CB = "http://localhost:7777/cb"


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return api_hub(tmp_path, monkeypatch, env={"HUB_PUBLIC_URL": ISS})


def machine(hub: TestClient) -> TestClient:
    return TestClient(hub.app, follow_redirects=False)


def pkce() -> tuple[str, str]:
    v = secrets.token_urlsafe(48)
    return v, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


def register(hub: TestClient, redirect: str = CB, method: str = "none", **extra: Any) -> dict[str, Any]:
    body = {
        "client_name": "Langdock",
        "redirect_uris": [redirect],
        "token_endpoint_auth_method": method,
        **extra,
    }
    r = machine(hub).post("/oauth/register", json=body)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def authz_query(cid: str, challenge: str, **kw: str) -> str:
    q = {"response_type": "code", "client_id": cid, "redirect_uri": CB, "state": "xyz", "code_challenge": challenge,
         "code_challenge_method": "S256", "resource": f"{ISS}/mcp", **kw}  # fmt: skip
    return urlencode(q)


def consent(hub: TestClient, cid: str, challenge: str, **kw: str) -> tuple[Any, str]:
    r = hub.get("/oauth/authorize?" + authz_query(cid, challenge, **kw))
    m = re.search(r'name="consent" value="([0-9a-f]+)"', r.text)
    return r, (m.group(1) if m else "")


def approve(
    hub: TestClient, nonce: str, scopes: tuple[str, ...] = ("mcp", "api:write"), decision: str = "approve"
) -> Any:
    body = [("consent", nonce), ("decision", decision), *[("scope", s) for s in scopes]]
    return hub.post(
        "/oauth/authorize",
        content=urlencode(body),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )


def query_of(r: Any) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}


def grant_code(hub: TestClient, cid: str, challenge: str, **kw: Any) -> str:
    _, nonce = consent(hub, cid, challenge)
    r = approve(hub, nonce, **kw)
    assert r.status_code == 303, r.text
    q = query_of(r)
    assert q["state"] == "xyz" and q["iss"] == ISS
    return q["code"]


def token(hub: TestClient, form: dict[str, str], **headers: str) -> Any:
    return machine(hub).post("/oauth/token", data=form, headers=headers)


def exchange(hub: TestClient, cid: str, code: str, verifier: str, **extra: str) -> Any:
    return token(hub, {"grant_type": "authorization_code", "client_id": cid, "code": code, "redirect_uri": CB,
                       "code_verifier": verifier, **extra})  # fmt: skip


def oauth_login(hub: TestClient) -> dict[str, Any]:
    cl = register(hub)
    v, ch = pkce()
    r = exchange(hub, cl["client_id"], grant_code(hub, cl["client_id"], ch), v)
    assert r.status_code == 200, r.text
    return {**r.json(), "client_id": cl["client_id"]}


# ---- descoberta ---------------------------------------------------------------------------------------


def test_discovery_documents_and_www_authenticate(hub: TestClient) -> None:
    m = machine(hub)
    prm = m.get("/.well-known/oauth-protected-resource/mcp")
    assert prm.status_code == 200 and prm.headers["access-control-allow-origin"] == "*"
    d = prm.json()
    assert d["resource"] == f"{ISS}/mcp" and d["authorization_servers"] == [ISS]
    assert d["scopes_supported"] == ["mcp", "api:write"] and d["bearer_methods_supported"] == ["header"]
    assert m.get("/.well-known/oauth-protected-resource").json() == d
    assert m.get("/.well-known/oauth-protected-resource/outro").status_code == 404
    a = m.get("/.well-known/oauth-authorization-server").json()
    assert a["issuer"] == ISS and a["code_challenge_methods_supported"] == ["S256"]
    assert (
        a["registration_endpoint"] == f"{ISS}/oauth/register" and a["token_endpoint"] == f"{ISS}/oauth/token"
    )
    assert a["authorization_response_iss_parameter_supported"] is True
    assert m.post("/.well-known/oauth-authorization-server").status_code == 405
    wa = m.post("/mcp", json={}).headers["www-authenticate"]
    assert (
        f'resource_metadata="{ISS}/.well-known/oauth-protected-resource/mcp"' in wa
        and 'scope="mcp api:write"' in wa
    )


def test_switches_follow_the_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    off = api_hub(tmp_path / "a", monkeypatch, env={"HUB_PUBLIC_URL": ISS, "HUB_MCP_OAUTH_ENABLED": "false"})
    assert machine(off).get("/.well-known/oauth-authorization-server").status_code == 404
    assert machine(off).post("/oauth/register", json={}).status_code == 404
    assert off.get("/oauth/authorize").status_code == 404
    nodcr = api_hub(
        tmp_path / "b",
        monkeypatch,
        env={"HUB_PUBLIC_URL": ISS, "HUB_MCP_OAUTH_DYNAMIC_REGISTRATION": "false"},
    )
    assert machine(nodcr).post("/oauth/register", json={}).status_code == 404
    assert "registration_endpoint" not in machine(nodcr).get("/.well-known/oauth-authorization-server").json()
    # without HUB_PUBLIC_URL only localhost answers (never an arbitrary Host)
    anon = api_hub(tmp_path / "c", monkeypatch)
    assert machine(anon).get("/.well-known/oauth-authorization-server").status_code == 404
    local = machine(anon).get("/.well-known/oauth-authorization-server", headers={"host": "localhost:8090"})
    assert local.json()["issuer"] == "http://localhost:8090"


# ---- the full flow ----


def test_full_dance_pkce_refresh_rotation_and_audience(hub: TestClient) -> None:
    cl = register(hub)
    cid = cl["client_id"]
    assert cid.startswith("hubc_") and "client_secret" not in cl
    v, ch = pkce()
    page, nonce = consent(hub, cid, ch)
    assert page.status_code == 200
    html = page.text
    for needle in ('data-oauth-client="">Langdock<', 'data-oauth-redirect-host="">localhost:7777<', "Não verificado",
                   'name="decision" value="approve"', 'value="api:write"', f"<code>{ISS}/mcp</code>",
                   'action="/oauth/authorize"', 'data-oauth-approve=""'):  # fmt: skip
        assert needle in html, needle
    csp = page.headers["content-security-policy"]
    assert "script-src 'none'" in csp and "form-action 'self' http://localhost:7777" in csp
    assert page.headers["x-frame-options"] == "DENY"
    # the nonce is single-use and tied to the user who opened the screen
    other = add_user(hub, "outro", ["Administrador"])
    assert "venceu ou já foi usada" in approve(other, nonce).text
    _, nonce = consent(hub, cid, ch)
    r = approve(hub, nonce)
    q = query_of(r)
    assert r.headers["referrer-policy"] == "no-referrer" and r.headers["location"].startswith(CB + "?")
    assert "venceu ou já foi usada" in approve(hub, nonce).text
    tok = exchange(hub, cid, q["code"], v)
    assert tok.status_code == 200 and tok.headers["cache-control"] == "no-store"
    t = tok.json()
    assert t["token_type"] == "Bearer" and t["expires_in"] == 3600 and t["scope"] == "mcp api:write"
    assert t["access_token"].startswith("hubo_") and t["refresh_token"].startswith("hubr_")
    # the access token is valid on /mcp (as the owner) and only there
    c = bearer(hub, t["access_token"])
    assert rpc(c, "tools/list").status_code == 200
    names = {x["name"] for x in rpc(c, "tools/list").json()["result"]["tools"]}
    assert "apps_list" in names and "tasks_cancel" in names
    assert c.get("/api/v1/apps").status_code == 401
    assert c.get("/api/ops/deployments").status_code == 401
    # only hashes in the state
    dump = json.dumps(list(hub.app.state.records.mem.values()))
    assert t["access_token"] not in dump and t["refresh_token"] not in dump and q["code"] not in dump
    # refresh with rotation: the new pair is valid; the old one is reuse and revokes the whole grant
    r2 = token(hub, {"grant_type": "refresh_token", "client_id": cid, "refresh_token": t["refresh_token"]})
    assert r2.status_code == 200
    n = r2.json()
    assert (
        n["refresh_token"] != t["refresh_token"]
        and rpc(bearer(hub, n["access_token"]), "ping").status_code == 200
    )
    reuse = token(hub, {"grant_type": "refresh_token", "client_id": cid, "refresh_token": t["refresh_token"]})
    assert reuse.status_code == 400 and reuse.json()["error"] == "invalid_grant"
    assert rpc(bearer(hub, n["access_token"]), "ping").status_code == 401  # revoked grant
    assert (
        token(
            hub, {"grant_type": "refresh_token", "client_id": cid, "refresh_token": n["refresh_token"]}
        ).status_code
        == 400
    )
    titles = [e["title"] for e in hub.get("/api/v1/audit?limit=50").json()["items"]]
    assert "OAuth: cliente registrado (registro dinâmico)" in titles
    assert (
        "OAuth: acesso autorizado a aplicativo" in titles
        and "OAuth: token emitido (código de autorização)" in titles
    )
    assert "OAuth: refresh token reutilizado — concessão revogada" in titles
    inbox = [i["title"] for i in hub.app.state.inbox.items.values()]
    assert any("revogado por segurança" in x for x in inbox)


def test_authorization_code_is_single_use_and_replay_revokes_the_grant(hub: TestClient) -> None:
    cid = register(hub)["client_id"]
    v, ch = pkce()
    code = grant_code(hub, cid, ch)
    first = exchange(hub, cid, code, v)
    assert first.status_code == 200
    replay = exchange(hub, cid, code, v)
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
    assert rpc(bearer(hub, first.json()["access_token"]), "ping").status_code == 401


def test_code_exchange_checks_client_redirect_pkce_and_resource(hub: TestClient) -> None:
    cid, other = register(hub)["client_id"], register(hub)["client_id"]
    v, ch = pkce()
    code = grant_code(hub, cid, ch)
    bad = [exchange(hub, other, code, v), exchange(hub, cid, code, "x" * 50),
           token(hub, {"grant_type": "authorization_code", "client_id": cid, "code": code,
                       "redirect_uri": "http://localhost:1/outra", "code_verifier": v}),
           exchange(hub, cid, code, v, resource="http://outro/mcp")]  # fmt: skip
    assert [b.status_code for b in bad] == [400, 400, 400, 400]
    assert bad[3].json()["error"] == "invalid_target" and bad[1].json()["error_description"].startswith(
        "code_verifier"
    )
    # a wrong request does not "burn" the code: the right one still works
    assert exchange(hub, cid, code, v).status_code == 200
    assert (
        token(hub, {"grant_type": "authorization_code", "client_id": cid, "code": "nada"}).status_code == 400
    )
    assert (
        token(hub, {"grant_type": "password", "client_id": cid}).json()["error"] == "unsupported_grant_type"
    )
    assert token(hub, {"client_id": cid}).json()["error"] == "invalid_request"
    assert token(hub, {"grant_type": "x", "client_id": "hubc_nope"}).status_code == 401
    assert token(hub, {"grant_type": "x", "grant_type ": "y", "client_id": cid}).status_code == 400
    dup = machine(hub).post("/oauth/token", content=f"grant_type=a&grant_type=b&client_id={cid}",
                            headers={"content-type": "application/x-www-form-urlencoded"})  # fmt: skip
    assert dup.json()["error"] == "invalid_request"


def test_authorization_request_validation(hub: TestClient) -> None:
    cid = register(hub)["client_id"]
    _, ch = pkce()
    assert "Cliente inválido" in hub.get("/oauth/authorize?client_id=nope").text
    assert (
        "não registrada"
        in hub.get(
            "/oauth/authorize?" + authz_query(cid, ch).replace("localhost%3A7777", "evil.example")
        ).text
    )
    r = hub.get("/oauth/authorize?" + authz_query(cid, ""))  # without PKCE: the error returns to the client
    assert r.status_code == 303 and query_of(r)["error"] == "invalid_request" and query_of(r)["iss"] == ISS
    r = hub.get("/oauth/authorize?" + authz_query(cid, ch, response_type="token"))
    assert query_of(r)["error"] == "unsupported_response_type"
    r = hub.get("/oauth/authorize?" + authz_query(cid, ch, resource="http://outro/mcp"))
    assert query_of(r)["error"] == "invalid_target"
    plain = urlencode(
        {"response_type": "code", "client_id": cid, "code_challenge": ch, "code_challenge_method": "plain"}
    )
    assert query_of(hub.get("/oauth/authorize?" + plain))["error"] == "invalid_request"
    assert "Parâmetro repetido" in hub.get("/oauth/authorize?client_id=a&client_id=b").text
    # no session: goes to login; with a token (no browser session) consent is refused
    anon = machine(hub).get("/oauth/authorize?" + authz_query(cid, ch))
    assert anon.status_code == 303 and anon.headers["location"].startswith("/login?next=")
    # user refusal
    _, nonce = consent(hub, cid, ch)
    r = approve(hub, nonce, decision="deny")
    assert query_of(r)["error"] == "access_denied" and query_of(r)["state"] == "xyz"


def test_user_can_narrow_scopes_and_write_tools_follow(hub: TestClient) -> None:
    cid = register(hub)["client_id"]
    v, ch = pkce()
    code = grant_code(hub, cid, ch, scopes=("mcp",))
    t = exchange(hub, cid, code, v).json()
    assert t["scope"] == "mcp"
    names = {x["name"] for x in rpc(bearer(hub, t["access_token"]), "tools/list").json()["result"]["tools"]}
    assert "apps_list" in names and "tasks_cancel" not in names
    # refresh can only reduce the scope, never widen it
    big = token(
        hub,
        {
            "grant_type": "refresh_token",
            "client_id": cid,
            "refresh_token": t["refresh_token"],
            "scope": "mcp api:write",
        },
    )
    assert big.status_code == 400 and big.json()["error"] == "invalid_scope"


def test_scope_limited_by_administration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = api_hub(
        tmp_path,
        monkeypatch,
        toml={"mcp": {"oauth": {"allowed_scopes": ["mcp"]}}},
        env={"HUB_PUBLIC_URL": ISS},
    )
    cid = register(hub)["client_id"]
    _, ch = pkce()
    page, _ = consent(hub, cid, ch, scope="mcp api:write")
    assert 'value="api:write"' not in page.text and 'value="mcp"' in page.text


# ---- clientes -----------------------------------------------------------------------------------------


def test_registration_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = api_hub(
        tmp_path,
        monkeypatch,
        env={"HUB_PUBLIC_URL": ISS, "HUB_MCP_OAUTH_ALLOWED_REDIRECT_HOSTS": "app.langdock.com"},
    )
    m = machine(hub)

    def reg(**body: Any) -> Any:
        return m.post("/oauth/register", json={"client_name": "X", **body})

    assert reg(redirect_uris=["https://app.langdock.com/cb"]).status_code == 201
    for uri in ("https://evil.example/cb", "http://app.langdock.com/cb", "https://app.langdock.com/cb#f", "ftp://x/cb",
                "https://u:p@app.langdock.com/cb", "javascript:alert(1)"):  # fmt: skip
        r = reg(redirect_uris=[uri])
        assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri", uri
    assert reg(redirect_uris=["http://127.0.0.1:5000/cb"]).status_code == 201  # loopback sempre
    assert reg(redirect_uris=[]).status_code == 400 and reg().status_code == 400
    assert reg(redirect_uris=["http://localhost/a"] * 11).status_code == 400
    assert (
        reg(redirect_uris=[CB], token_endpoint_auth_method="private_key_jwt").json()["error"]
        == "invalid_client_metadata"
    )
    assert reg(redirect_uris=[CB], grant_types=["password"]).status_code == 400
    assert m.post("/oauth/register", content="x").status_code == 400
    r = reg(redirect_uris=[CB], client_name="‮Claude​")
    assert r.json()["client_name"] == "Claude"  # invisible and bidi characters are removed
    confidential = reg(redirect_uris=[CB]).json()  # default: client_secret_basic
    assert (
        confidential["client_secret"].startswith("hubcs_") and confidential["client_secret_expires_at"] == 0
    )
    stored = json.dumps(list(hub.app.state.records.mem.values()))
    assert confidential["client_secret"] not in stored  # only the hash


def test_registration_rate_limit_per_ip(hub: TestClient) -> None:
    m = machine(hub)
    codes = [m.post("/oauth/register", json={"redirect_uris": [CB]}).status_code for _ in range(22)]
    assert codes[:20] == [201] * 20 and set(codes[20:]) == {429}


def test_confidential_client_authentication(hub: TestClient) -> None:
    cl = register(hub, method="client_secret_basic")
    cid, secret = cl["client_id"], cl["client_secret"]
    v, ch = pkce()
    code = grant_code(hub, cid, ch)
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": CB, "code_verifier": v}
    basic = {"authorization": "Basic " + base64.b64encode(f"{cid}:{secret}".encode()).decode()}
    wrong = {"authorization": "Basic " + base64.b64encode(f"{cid}:errado".encode()).decode()}
    assert token(hub, form, **wrong).status_code == 401
    nocred = token(hub, form)  # no credentials
    assert nocred.status_code == 401 and nocred.headers["www-authenticate"].startswith("Basic")
    assert token(hub, form | {"client_id": cid}).status_code == 401  # no secret
    assert token(hub, form, **basic).status_code == 200
    post = register(hub, method="client_secret_post")
    code = grant_code(hub, post["client_id"], ch)
    ok = token(
        hub, form | {"code": code, "client_id": post["client_id"], "client_secret": post["client_secret"]}
    )
    assert ok.status_code == 200


def test_client_metadata_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = api_hub(tmp_path, monkeypatch, env={"HUB_PUBLIC_URL": ISS})
    url = "https://client.example/meta.json"
    doc = {
        "client_id": url,
        "client_name": "Cliente CIMD",
        "redirect_uris": [CB],
        "token_endpoint_auth_method": "none",
    }
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(200, json=doc)

    o = cast(oa.OAuth, hub.app.state.oauth)
    o.cimd_transport = httpx.MockTransport(handler)

    async def resolve(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    o.resolver = resolve
    import asyncio

    v, ch = pkce()
    q = authz_query(url, ch)
    page = hub.get("/oauth/authorize?" + q)
    assert (
        page.status_code == 200
        and "Cliente CIMD" in page.text
        and "Identificado pelo documento em" in page.text
    )
    assert calls and calls[0] == "https://93.184.216.34/meta.json"
    m = re.search(r'name="consent" value="([0-9a-f]+)"', page.text)
    assert m
    nonce = m.group(1)
    code = query_of(approve(hub, nonce))["code"]
    assert (
        token(
            hub,
            {
                "grant_type": "authorization_code",
                "client_id": url,
                "code": code,
                "redirect_uri": CB,
                "code_verifier": v,
            },
        ).status_code
        == 200
    )
    assert any(c["client_id"] == url for c in asyncio.run(o.clients()))
    # inconsistent document is refused
    doc["client_id"] = "https://outro.example/x.json"
    o.cimd_cache.clear()
    assert "client_id do documento" in hub.get("/oauth/authorize?" + q).text


def test_client_metadata_document_never_reaches_internal_addresses(hub: TestClient) -> None:
    _, ch = pkce()
    for url in ("https://localhost/meta.json", "https://127.0.0.1/meta.json", "http://client.example/meta.json",
                "https://client.example/"):  # fmt: skip
        r = hub.get("/oauth/authorize?" + authz_query(url, ch))
        assert r.status_code == 400 and "Cliente inválido" in r.text, url
    assert oa.ip_public(__import__("ipaddress").ip_address("93.184.216.34"))
    for ip in (
        "10.0.0.1",
        "127.0.0.1",
        "169.254.1.1",
        "192.168.1.1",
        "100.64.0.1",
        "::1",
        "fc00::1",
        "::ffff:10.0.0.1",
    ):
        assert not oa.ip_public(__import__("ipaddress").ip_address(ip)), ip


# ---- revocation and screens ----


def test_revoke_endpoint_account_page_and_admin_actions(hub: TestClient) -> None:
    t = oauth_login(hub)
    cid = t["client_id"]
    # another client does not revoke what is not its own; 200 even for an unknown token (RFC 7009)
    stranger = register(hub)["client_id"]
    assert token(hub, {"grant_type": "x"}).status_code == 401
    r = machine(hub).post("/oauth/revoke", data={"token": t["refresh_token"], "client_id": stranger})
    assert r.status_code == 200 and rpc(bearer(hub, t["access_token"]), "ping").status_code == 200
    assert (
        machine(hub).post("/oauth/revoke", data={"token": "hubr_nada", "client_id": cid}).status_code == 200
    )
    # the account page lists and revokes
    page = hub.get("/account/access").text
    m = re.search(r'name="grant" value="(g[0-9a-f]+)"', page)
    assert m
    gid = m.group(1)
    assert (
        "Langdock" in page
        and "localhost:7777" in page
        and "Não verificado" in page
        and "mcp api:write" in page
    )
    r = hub.post(
        "/account/authorized-apps/revoke",
        content=f"grant={gid}",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert "msg=revoked" in r.headers["location"]
    assert rpc(bearer(hub, t["access_token"]), "ping").status_code == 401
    assert (
        token(
            hub, {"grant_type": "refresh_token", "client_id": cid, "refresh_token": t["refresh_token"]}
        ).status_code
        == 400
    )
    # through the client itself
    t2 = oauth_login(hub)
    r = machine(hub).post("/oauth/revoke", data={"token": t2["refresh_token"], "client_id": t2["client_id"]})
    assert r.status_code == 200 and rpc(bearer(hub, t2["access_token"]), "ping").status_code == 401
    # the administration blocks the client: grants vanish and it does not authorize again
    t3 = oauth_login(hub)
    form = {"content-type": "application/x-www-form-urlencoded"}
    r = hub.post("/admin/oauth/block-client", content=f"client={t3['client_id']}", headers=form)
    assert r.status_code == 303 and r.headers["location"] == "/admin/tokens#oauth"
    assert rpc(bearer(hub, t3["access_token"]), "ping").status_code == 401
    _, ch = pkce()
    assert "bloqueado" in hub.get("/oauth/authorize?" + authz_query(t3["client_id"], ch)).text
    hub.post("/admin/oauth/unblock-client", content=f"client={t3['client_id']}", headers=form)
    assert hub.get("/oauth/authorize?" + authz_query(t3["client_id"], ch)).status_code == 200
    hub.post("/admin/oauth/remove-client", content=f"client={t3['client_id']}", headers=form)
    assert "desconhecido" in hub.get("/oauth/authorize?" + authz_query(t3["client_id"], ch)).text
    # administration is restricted to whoever can edit settings
    colab = add_user(hub, "colab", ["Colaborador"])
    assert colab.post("/admin/oauth/block-client", content="client=x", headers=form).status_code == 403


def test_disabled_account_cannot_exchange_or_refresh(hub: TestClient) -> None:
    cid = register(hub)["client_id"]
    v, ch = pkce()
    code = grant_code(hub, cid, ch)
    path = hub.app.state.auth.path
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["users"][0]["active"] = False
    path.write_text(json.dumps(doc), encoding="utf-8")
    r = exchange(hub, cid, code, v)
    assert r.status_code == 400 and "desativada" in r.json()["error_description"]


# ---- CORS ---------------------------------------------------------------------------------------------


def test_cors_preflight_and_readable_errors_on_machine_endpoints(
    hub: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    m = machine(hub)
    for path, methods in (("/mcp", "GET, POST, DELETE, OPTIONS"), ("/.well-known/oauth-authorization-server", "GET, OPTIONS"),
                          ("/.well-known/oauth-protected-resource/mcp", "GET, OPTIONS"), ("/oauth/token", "POST, OPTIONS"),
                          ("/oauth/register", "POST, OPTIONS"), ("/oauth/revoke", "POST, OPTIONS")):  # fmt: skip
        r = m.options(
            path, headers={"origin": "https://app.langdock.com", "access-control-request-method": "POST"}
        )
        assert r.status_code == 204, path
        assert (
            r.headers["access-control-allow-origin"] == "*"
            and "access-control-allow-credentials" not in r.headers
        )
        assert r.headers["access-control-allow-methods"] == methods
        assert (
            "mcp-session-id" in r.headers["access-control-allow-headers"]
            and r.headers["access-control-max-age"] == "600"
        )
    assert m.options("/oauth/authorize").status_code != 204  # browser screen: no CORS
    assert "access-control-allow-origin" not in hub.get("/oauth/authorize").headers
    # the 401 from /mcp is readable by the browser
    r = m.post("/mcp", json={}, headers={"origin": "https://app.langdock.com"})
    assert r.status_code == 401 and r.headers["access-control-allow-origin"] == "*"
    assert (
        "WWW-Authenticate" in r.headers["access-control-expose-headers"]
        and "Mcp-Session-Id" in r.headers["access-control-expose-headers"]
    )
    # origin not allowed: 403, but still with the CORS headers (the client needs to read the error)
    ok = bearer(hub, make_token(hub, ["mcp"]))
    r = rpc(ok, "ping", origin="https://evil.example")
    assert r.status_code == 403 and r.headers["access-control-allow-origin"] == "*"
    out = m.get("/.well-known/oauth-authorization-server")
    assert out.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-origin" not in hub.get("/api/v1/apps").headers
    assert m.post("/oauth/token", data={"grant_type": "x"}).headers["access-control-allow-origin"] == "*"


def test_machine_endpoints_follow_the_control_plane_cidr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = api_hub(tmp_path, monkeypatch, env={"HUB_PUBLIC_URL": ISS, "HUB_ADMIN_ALLOWED_CIDRS": "10.0.0.0/8"})
    m = machine(hub)
    assert m.get("/.well-known/oauth-authorization-server").status_code == 403
    assert m.post("/oauth/token", data={}).status_code == 403
    assert m.options("/mcp").status_code == 204  # preflight comes before everything
    open_ = api_hub(
        tmp_path / "o",
        monkeypatch,
        env={"HUB_PUBLIC_URL": ISS, "HUB_ADMIN_ALLOWED_CIDRS": "10.0.0.0/8", "HUB_MCP_PUBLIC_NETWORK": "1"},
    )
    assert machine(open_).get("/.well-known/oauth-authorization-server").status_code == 200
    assert machine(open_).get("/api/v1/apps").status_code == 403  # /api segue restrito


def test_mcp_diagnostic_log_is_opt_in(
    hub: TestClient, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    machine(hub).get("/.well-known/oauth-authorization-server")
    assert "[mcp-diag]" not in capfd.readouterr().out
    monkeypatch.setenv("HUB_LOG_MCP_REQUESTS", "1")
    machine(hub).get("/.well-known/oauth-authorization-server", headers={"user-agent": "Langdock/1"})
    out = capfd.readouterr().out
    assert (
        "[mcp-diag] GET /.well-known/oauth-authorization-server" in out
        and "Langdock/1" in out
        and "auth=não" in out
    )
