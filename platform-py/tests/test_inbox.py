from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import httpx
import pytest
from auth_helper import client_for
from fastapi.testclient import TestClient

from hub.app import create_app
from hub.gateway.proxy import Gateway, GwOptions
from hub.gateway.registry import AppEntry, Registry
from hub.ux import search as srch
from hub.ux.inbox import InboxError, InboxStore, Who, approval_of, can_decide, item_of, targets
from hub.ux.inbox_routes import stream_events
from hub.ux.texts import resolve, tn, tr

HDR = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}
TOKEN = "t" * 32


@pytest.fixture(autouse=True)
def _fresh_search_state() -> None:
    srch._buckets.clear()
    srch._unsupported.clear()


def _entry(app_id: str, name: str) -> AppEntry:
    return AppEntry(
        id=app_id, name=name, upstream="http://upstream", display={"description": f"Dados de {name}"}
    )


def make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, handler: Any = None) -> TestClient:
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUB_INTERNAL_TOKEN_SERVICOS", TOKEN)
    monkeypatch.setenv("HUB_INTERNAL_TOKEN_CONTRATOS", TOKEN)
    reg = Registry([_entry("servicos", "Central de Serviços"), _entry("contratos", "Gestão de Contratos")])
    transport = httpx.MockTransport(handler or (lambda r: httpx.Response(404)))
    gw = Gateway(reg, GwOptions(), transport=transport)
    return client_for(create_app(gateway=gw))


def approval(client: TestClient, **kw: Any) -> int:
    item = item_of(
        "approval",
        "servicos",
        "Aprovar chamado SD-1",
        source="servicos:evt-1",
        by="operador@demo.local",
        by_id="u-op",
        to_roles=["Administrador"],
        approval=approval_of("ap-1"),
        **kw,
    )
    return int(client.app.state.inbox.push(item))  # type: ignore[attr-defined]


# ---- lógica pura ------------------------------------------------------------------------------


def test_targets_and_decide_rules() -> None:
    boss = Who(
        id="u1", email="boss@x", roles=["Gestor"], perms=["aprovar_solicitacoes", "gerenciar_usuarios"]
    )
    it = item_of(
        "approval", "servicos", "t", by="op@x", by_id="u2", to_roles=["gestor"], approval=approval_of("a")
    )
    assert targets(it, boss) and can_decide(it, boss)
    assert not targets(it, Who(id="u3", roles=["Outro"]))
    assert not targets(it, Who(id="u1", roles=["Gestor"], apps=["ativos"]))  # does not reach the app
    assert not can_decide(it | {"by": "boss@x"}, boss)  # nobody approves their own request
    own = item_of("notification", "servicos", "t", by="op@x")  # no recipient: only whoever caused it
    assert targets(own, Who(id="u9", email="OP@x")) and not targets(own, boss)


def test_store_push_dedupe_states_and_reload(tmp_path: Path) -> None:
    store = InboxStore(tmp_path / "inbox.json")
    who = Who(id="u1", email="a@x", roles=["R"])
    a = store.push(item_of("notification", "servicos", "um", source="s:1", to_roles=["R"]))
    assert store.push(item_of("notification", "servicos", "dup", source="s:1", to_roles=["R"])) == a
    entries = store.entries(who, "pt-BR", lambda _k: True, lambda x: x)
    assert [e["title"] for e in entries] == ["um"] and store.unread(entries) == 1
    assert store.set_state(who, [a, 999], True, None) == 1
    assert store.set_state(who, [a], None, True) == 1
    entries = store.entries(who, "pt-BR", lambda _k: True, lambda x: x)
    assert entries[0]["archived"] and entries[0]["read"] and store.unread(entries) == 0
    assert store.entries(who, "pt-BR", lambda _k: False, lambda x: x)[0]["in_app"] is False
    again = InboxStore(tmp_path / "inbox.json")
    assert again.items.keys() == store.items.keys() and again.next_id == store.next_id


def test_access_request_rules_and_grant(tmp_path: Path) -> None:
    store = InboxStore(tmp_path / "inbox.json")
    user = Who(id="u2", email="op@x", name="Op", perms=[])
    admin = Who(id="u1", email="adm@x", name="Adm", perms=["gerenciar_usuarios"])
    item_id = store.request_access(user, "ativos", "Gestão de Ativos", "preciso\x00")
    assert (store.item(item_id) or {})["body"] == "preciso"
    with pytest.raises(InboxError) as dup:
        store.request_access(user, "ativos", "Gestão de Ativos", "")
    assert dup.value.status == 409
    assert store.begin(admin, item_id, "approve", "ok")["approval"]["status"] == "approved"
    assert store.grants == {"u2": ["ativos"]}
    with pytest.raises(InboxError) as twice:
        store.begin(admin, item_id, "approve", "")
    assert twice.value.status == 409
    assert store.revoke_grant(item_id) and store.grants["u2"] == []
    other = store.request_access(user, "contratos", "Contratos", "")
    assert store.begin(admin, other, "reject", "não")["approval"]["status"] == "rejected"
    with pytest.raises(InboxError) as cool:
        store.request_access(user, "contratos", "Contratos", "")
    assert cool.value.status == 429  # recusado: espera 24 h
    with pytest.raises(InboxError) as bad:
        store.begin(admin, other, "talvez", "")
    assert bad.value.status == 409 or bad.value.status == 400


async def test_sse_delivers_unread_changes(tmp_path: Path) -> None:
    store = InboxStore(tmp_path / "inbox.json")
    who = Who(id="u1", roles=["R"])
    count = lambda: store.unread(store.entries(who, "pt-BR", lambda _k: True, lambda x: x))  # noqa: E731
    events = stream_events(store, count, debounce=0.01, keepalive=0.05)
    assert (await anext(events)).startswith("event: ready")
    assert json.loads((await anext(events)).split("data: ")[1]) == {"unread": 0}
    task = asyncio.ensure_future(anext(events))
    await asyncio.sleep(0.2)  # no change: only keepalive (the generator keeps waiting)
    assert not task.done() or task.result().startswith(":")
    if task.done():
        task = asyncio.ensure_future(anext(events))
    store.push(item_of("notification", "servicos", "novo", source="s:9", to_roles=["R"]))
    ev = await asyncio.wait_for(task, 2)
    while ev.startswith(":"):
        ev = await asyncio.wait_for(anext(events), 2)
    assert ev == 'event: inbox\ndata: {"unread": 1}\n\n'
    assert store.watchers
    assert isinstance(events, AsyncGenerator)
    await events.aclose()
    assert not store.watchers


# ---- API ---------------------------------------------------------------------------------------


def test_inbox_api_list_read_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = make_client(tmp_path, monkeypatch)
    item = approval(c)
    body = c.get("/api/me/inbox").json()
    assert body["unread"] == 1 and body["items"][0]["id"] == f"i:{item}"
    assert body["items"][0]["approval"]["can_decide"] is True
    assert c.get("/api/me/inbox?filter=archived").json()["items"] == []
    assert c.post("/api/me/inbox/read", json={"ids": [f"i:{item}"]}, headers=HDR).json() == {"marked": 1}
    assert c.get("/api/me/inbox?filter=unread").json()["items"] == []
    assert c.post("/api/me/inbox/archive", json={"ids": [f"i:{item}"]}, headers=HDR).json() == {"changed": 1}
    assert len(c.get("/api/me/inbox?filter=archived").json()["items"]) == 1
    assert c.post(
        "/api/me/inbox/archive", json={"ids": [f"i:{item}"], "archived": False}, headers=HDR
    ).json() == {"changed": 1}
    assert c.post("/api/me/inbox/read", headers=HDR).json() == {"marked": 1}  # no body = all
    assert c.post("/api/me/inbox/archive", content=b"{", headers=HDR).status_code == 400


def test_decide_calls_the_app_and_records_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"status": "approved"})

    c = make_client(tmp_path, monkeypatch, handler)
    # whoever asked is someone else; the admin (Administrator) decides
    item = approval(c)
    r = c.post(f"/api/me/inbox/i:{item}/decide", json={"decision": "approve", "comment": "ok"}, headers=HDR)
    assert r.json() == {"ok": True, "status": "approved"}
    req = seen[0]
    assert req.url.path == "/servicos/_hub/approvals/ap-1"
    assert req.headers["x-hub-internal-token"] == TOKEN and "x-hub-user" in req.headers
    assert json.loads(req.content) | {"decided_at": 0} == {
        "decision": "approve",
        "comment": "ok",
        "event_id": "evt-1",
        "decided_at": 0,
    }
    again = c.post(f"/api/me/inbox/i:{item}/decide", json={"decision": "reject"}, headers=HDR)
    assert again.status_code == 409
    done = c.get("/api/me/inbox").json()["items"][0]
    assert done["approval"]["status"] == "approved" and done["read"] is True


def test_decide_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = make_client(tmp_path, monkeypatch, lambda r: httpx.Response(500, json={"error": "quebrou"}))
    item = approval(c)
    r = c.post(f"/api/me/inbox/i:{item}/decide", json={"decision": "approve"}, headers=HDR)
    assert r.status_code == 502 and "quebrou" in r.json()["error"]
    shown = c.get("/api/me/inbox").json()["items"][0]["approval"]
    assert shown["status"] == "error" and shown["can_decide"] is True  # can try again
    assert c.post("/api/me/inbox/i:999/decide", json={"decision": "approve"}, headers=HDR).status_code == 404
    assert c.post("/api/me/inbox/x/decide", json={"decision": "approve"}, headers=HDR).status_code == 400
    assert c.post(f"/api/me/inbox/i:{item}/decide", json={"x": 1}, headers=HDR).status_code == 400
    mismatch = make_client(
        tmp_path / "m", monkeypatch, lambda r: httpx.Response(200, json={"status": "rejected"})
    )
    other = approval(mismatch)
    bad = mismatch.post(f"/api/me/inbox/i:{other}/decide", json={"decision": "approve"}, headers=HDR)
    assert bad.status_code == 502 and "diverge" in bad.json()["error"]


def test_access_requests_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = make_client(tmp_path, monkeypatch)
    assert c.post("/api/me/access-requests", json={"app": "nada"}, headers=HDR).status_code == 404
    assert c.post("/api/me/access-requests", json={"app": "servicos"}, headers=HDR).status_code == 409
    assert c.post("/api/me/access-requests", json={}, headers=HDR).status_code == 400
    c.app.state.inbox.push(  # type: ignore[attr-defined]
        item_of(
            "access",
            "servicos",
            "pede",
            by_id="u2",
            by="op@x",
            to_perm="gerenciar_usuarios",
            approval=approval_of("r"),
        )
    )
    item = c.get("/api/me/inbox").json()["items"][0]
    assert item["approval"]["can_decide"] and item["kind"] == "access"
    r = c.post(f"/api/me/inbox/{item['id']}/decide", json={"decision": "approve"}, headers=HDR)
    assert r.json()["status"] == "approved"
    assert c.app.state.inbox.grants == {"u2": ["servicos"]}  # type: ignore[attr-defined]
    assert c.post("/api/admin/grants/revoke", json={"item": item["id"]}, headers=HDR).json() == {"ok": True}
    assert c.post("/api/admin/grants/revoke", json={"item": 99}, headers=HDR).status_code == 404


def test_sse_route_limits_connections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = make_client(tmp_path, monkeypatch)
    c.app.state.inbox.streams["u-test"] = 12  # type: ignore[attr-defined]
    assert c.get("/api/me/stream").status_code == 429


# ---- pages, prefs, i18n ----


def test_inbox_page_bell_and_prefs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = make_client(tmp_path, monkeypatch)
    assert "Nenhum item neste filtro." in c.get("/inbox").text
    item = approval(c)
    html = c.get("/inbox").text
    assert f'data-item="i:{item}"' in html and "Aprovar chamado SD-1" in html
    assert 'data-decide="approve"' in html and "Não lidas · 1" in html
    assert 'data-hub-notifications="1"' in c.get("/").text  # sino
    assert "Aprovar chamado SD-1" in c.get("/").text  # Home widget
    r = c.post("/account/notifications/read?next=%2Finbox", headers=HDR)
    assert r.status_code == 303 and r.headers["location"] == "/inbox"
    assert "Não lidas · 0" in c.get("/inbox").text
    c.put("/api/me/prefs", json={"channels": {"approval": {"in_app": False}}}, headers=HDR)
    assert (
        'name="approval.in_app"' in c.get("/account").text
        and 'name="approval.in_app" checked' not in c.get("/account").text
    )


def test_search_endpoints_federate_and_rank(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.startswith("/contratos/"):
            assert req.headers["x-hub-internal-token"] == TOKEN and req.url.params["limit"] == "5"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Contrato 1",
                            "subtitle": "CT-1",
                            "url": "/contratos/c?x=1",
                            "kind": "Contrato",
                            "score": 1,
                        },
                        {"title": "fora", "url": "/admin/users"},
                        {"title": "", "url": "/apps/contratos/x"},
                    ]
                },
            )
        if req.url.path.startswith("/servicos/"):
            return httpx.Response(404)  # no search
        return httpx.Response(500)

    c = make_client(tmp_path, monkeypatch, handler)
    srch._buckets.clear()
    body = c.get("/api/me/search?q=contrato").json()
    assert [g["id"] for g in body["groups"]][:2] == ["contratos", "platform"]  # app records first
    assert body["groups"][0]["hits"] == [
        {
            "title": "Contrato 1",
            "subtitle": "CT-1",
            "url": "/apps/contratos/c?x=1",
            "kind": "Contrato",
            "score": 1.0,
        }
    ]
    assert "servicos" in srch._unsupported and body["partial"] is False
    assert c.get("/api/me/search?q=").json() == {"q": "", "groups": [], "recent": []}
    final = c.post("/api/me/search", json={"q": " contrato "}, headers=HDR).json()
    assert final["ok"] is True and c.get("/api/me/prefs").json()["searches"] == ["contrato"]
    assert c.post("/api/me/search", json={"q": " "}, headers=HDR).status_code == 400
    page = c.get("/search?q=contrato").text
    assert (
        "<mark>Contrato</mark>" in page
        and "Contrato · CT-1" in page
        and 'data-hub-search-record="contrato"' in page
    )
    assert "hub-search-more" not in page  # fewer than 5 records: no "see all"
    srch._buckets.clear()
    quiet = make_client(tmp_path / "q", monkeypatch)  # no app responds with anything
    assert "Nenhum resultado para “zzzz”." in quiet.get("/search?q=zzzz").text
    assert "Administração (interno)" not in page or "hub-search-admin" in page


def test_search_failure_marks_partial_and_see_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.startswith("/servicos/"):
            raise httpx.ConnectError("down")
        n = int(req.url.params["limit"])
        return httpx.Response(
            200, json={"results": [{"title": f"Contrato {i}", "url": f"/contratos/{i}"} for i in range(n)]}
        )

    c = make_client(tmp_path, monkeypatch, handler)
    srch._buckets.clear()
    srch._unsupported.clear()
    body = c.get("/api/me/search?q=contrato").json()
    assert body["partial"] is True and body["unavailable"] == ["servicos"]
    more = next(g for g in body["groups"] if g["id"] == "contratos")["more_url"]
    assert more == "/search?q=contrato&app=contratos"
    page = c.get("/search?q=contrato&app=contratos").text
    assert (
        page.count("<li>") == 50
        and "Voltar a todos os resultados" in page
        and 'data-hub-search-record=""' in page
    )
    assert "Algumas aplicações não responderam a tempo." in c.get("/search?q=contrato").text


def test_search_rate_limit_and_helpers() -> None:
    srch._buckets.clear()
    assert all(srch.search_allowed("u", 0.0) for _ in range(20)) and not srch.search_allowed("u", 0.0)
    assert srch.search_allowed("u", 1.0)  # refills 4 tokens per second
    assert srch.score("Gestão de Ativos", "gestao") == 0.8
    assert srch.score("Gestão de Ativos", "ATIVOS") == 0.6
    assert srch.score("Gestão de Ativos", "tão") == 0.4 and srch.score("x", " ") == 0.0
    assert srch.mark_split("Gestão de Contratos", "gestao") == ("", "Gestão", " de Contratos")
    assert srch.mark_split("Contrato 12", "TRATO") == ("Con", "trato", " 12")
    assert srch.mark_split("abc", "z") == ("abc", "", "")
    assert srch.safe_app_link("servicos", "/servicos/r/1") == "/apps/servicos/r/1"
    for bad in (
        "/admin/users",
        "https://evil.example/servicos",
        "/servicos/%2e%2e/admin",
        "javascript:x",
        "/apps/servicos/a b",
    ):
        assert srch.safe_app_link("servicos", bad) == "/apps/servicos"
    assert srch.internal_token("nao-existe") is None


def test_settings_pages_and_prefs_form(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = make_client(tmp_path, monkeypatch)
    html = c.get("/account").text
    for frag in ("admin@demo.local", 'data-channel-form=""', 'name="analytics_opt_out"'):
        assert frag in html
    sec = c.get("/account/security").text
    assert 'id="seguranca"' in sec and "/account/password?next=%2Faccount%2Fsecurity" in sec
    assert c.get("/account/settings", follow_redirects=False).headers["location"] == "/account"
    assert (
        'aria-checked="true" class="hub-seg-btn hub-theme-btn active"' in html.replace("\n", "")
        or "hub-theme-btn active" in html
    )
    r = c.post(
        "/account/prefs?next=%2Faccount",
        content="theme=dark&lang=en",
        headers=HDR | {"content-type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 303 and r.headers["location"] == "/account"
    page = c.get("/account").text  # theme and language come from the server (no flash of light)
    assert (
        '<html lang="en" data-theme="dark">' in page and '<meta name="color-scheme" content="dark"/>' in page
    )
    assert "Appearance" in page and "Change password" in c.get("/account/security").text
    bad = c.post(
        "/account/prefs",
        content="theme=neon",
        headers=HDR | {"content-type": "application/x-www-form-urlencoded"},
    )
    assert bad.status_code == 400
    c.put("/api/me/prefs", json={"theme": "auto", "lang": ""}, headers=HDR)
    assert 'data-theme="auto"' in c.get("/inbox").text and 'content="light dark"' in c.get("/inbox").text
    assert c.get("/account").status_code == 200


def test_tokens_page_create_and_revoke_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = make_client(tmp_path, monkeypatch)
    assert 'data-tokens-empty=""' in c.get("/account/access").text
    made = c.post(
        "/api/me/tokens",
        json={"name": "ci", "actions": ["api:read"], "apps": [], "expires_days": 30},
        headers=HDR,
    )
    assert made.status_code == 201
    token_id = made.json()["item"]["id"]
    page = c.get("/account/access").text
    assert f'data-token-row="{token_id}"' in page and "<code>api:read</code>" in page
    assert 'value="secrets:reveal"' not in page and 'value="30"' in page
    assert c.post(f"/api/me/tokens/{token_id}/revoke", headers=HDR).status_code == 200
    assert 'data-tokens-empty=""' in c.get("/account/access").text


def test_authorized_apps_page_and_revoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = make_client(tmp_path, monkeypatch)
    assert 'data-oauth-empty=""' in c.get("/account/access").text
    old = c.get("/account/tokens", follow_redirects=False)
    assert old.status_code == 308 and old.headers["location"] == "/account/access#tokens"
    old = c.get("/account/authorized-apps?msg=x", follow_redirects=False)
    assert old.headers["location"] == "/account/access?msg=x#apps"
    r = c.post("/account/authorized-apps/revoke", content="grant=g1", headers=HDR)
    assert r.status_code == 303 and "msg=not_found" in r.headers["location"]
    assert "Autorização não encontrada." in c.get("/account/access?msg=not_found").text
    row: dict[str, object] = {"id": "g1", "name": "Langdock", "unverified": True, "host": "x.com", "scopes": "mcp",
           "created": "há 1 h", "last": "—"}  # fmt: skip

    async def grants_view(_uid: str, _lang: str = "pt-BR") -> list[dict[str, object]]:
        return [row]

    async def revoke_for_user(_uid: str, gid: str, _actor: str = "") -> bool:
        return gid == "g1"

    c.app.state.oauth.grants_view = grants_view
    c.app.state.oauth.revoke_for_user = revoke_for_user
    page = c.get("/account/access").text
    assert "Langdock" in page and "Não verificado" in page and 'name="grant" value="g1"' in page
    r = c.post("/account/authorized-apps/revoke", content="grant=g1", headers=HDR)
    assert "msg=revoked" in r.headers["location"]


def test_password_roundtrip_returns_to_settings_with_new_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_client(tmp_path, monkeypatch)
    page = c.get("/account/password?next=%2Faccount%2Fsecurity").text
    assert 'name="next" value="/account/security"' in page and 'data-pw-cancel=""' in page
    form = {
        "current": "test-password",
        "new": "Another-Password-9!",
        "confirm": "Another-Password-9!",
        "next": "/account/security",
    }
    r = c.post("/account/password", data=form, headers=HDR)
    assert r.status_code == 303 and r.headers["location"] == "/account/security"
    assert "hub_session=" in r.headers["set-cookie"]
    fresh = {"cookie": "hub_session=" + r.headers["set-cookie"].split("hub_session=")[1].split(";")[0]}
    assert c.get("/account/security", headers=fresh).status_code == 200  # the new session is valid
    assert c.get("/account/security").status_code == 303  # the old one dropped
    mismatch = c.post(
        "/account/password",
        data=form | {"confirm": "x", "current": "Another-Password-9!"},
        headers=HDR | fresh,
    )
    assert mismatch.status_code == 400 and "As senhas não coincidem." in mismatch.text
    wrong = c.post("/account/password", data=form | {"current": "nope"}, headers=HDR | fresh)
    assert wrong.status_code == 400 and "Senha atual incorreta." in wrong.text
    evil = c.get("/account/password?next=//evil.example", headers=fresh).text
    assert 'name="next" value="/"' in evil


def test_language_resolution_and_translation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve("", "en-US,en;q=0.9,pt;q=0.8") == "en"
    assert resolve("es", "pt-BR") == "en" and resolve("", "fr, es;q=0.5") == "en"  # `es` dropped: now en
    assert resolve("", "en;q=0") == "pt-BR" and resolve("", None) == "pt-BR" and resolve("xx", "") == "pt-BR"
    assert tn("home.subtitle", 1, "en").startswith("1 ") and tr("Cancelar", "en") == "Cancel"
    assert (
        tr("Cancelar", "pt-BR") == "Cancelar"
        and tr("texto sem tradução xyz", "en") == "texto sem tradução xyz"
    )
    assert tn("inbox.by", "ana", "en") == "Requested by ana"
    c = make_client(tmp_path, monkeypatch)
    en = c.get("/inbox", headers={"accept-language": "en"}).text
    assert '<html lang="en"' in en and "Alertas da plataforma" not in en and "Notification preferences" in en
    assert 'aria-label="Platform navigation"' in en and "Skip to content" in en and ">Home<" in en
    assert 'class="hub-search-label">Search apps, pages and records<' in en
    assert "Pular para o conteúdo" in c.get("/inbox").text  # default: pt-BR
    assert c.put("/api/me/prefs", json={"lang": "es"}, headers=HDR).status_code == 400
    c.put("/api/me/prefs", json={"lang": "en"}, headers=HDR)
    en = c.get("/search?q=zzz", headers={"accept-language": "pt-BR"}).text  # the preference beats the browser
    assert '<html lang="en"' in en and "Sin resultados para" not in en
    state = json.loads(
        c.get("/").text.split('data-hub-i18n="')[1].split('" data-hub-palette')[0].replace("&#34;", '"')
    )
    assert state["lang"] == "en"


def test_legacy_agent_items_are_dropped_on_load(tmp_path: Path) -> None:
    """Action requests from an agent (approval in the Inbox, now removed) recorded earlier do not show up again."""
    store = InboxStore(tmp_path / "inbox.json")
    keep = store.push(item_of("app", "ativos", "fica", to_users=["u1"]))
    gone = store.push(item_of("agent", "platform", "sai", to_users=["u1"], approval=approval_of("x")))
    again = InboxStore(tmp_path / "inbox.json")
    assert keep in again.items and gone not in again.items


def test_access_decision_notifies_the_requester(tmp_path: Path) -> None:
    """F7a: approving/refusing an access request notifies whoever asked ("Acesso a X liberado") in the Inbox, once."""
    store = InboxStore(tmp_path / "inbox.json")
    boss = Who(
        id="b",
        name="Chefe",
        email="chefe@x",
        perms=["gerenciar_usuarios"],
        roles=["Administrador"],
        session=True,
    )
    i = store.push(item_of("access", "servicos", "pede acesso", by_id="u9", by="Ana", to_perm="gerenciar_usuarios",
                           approval=approval_of("r"), source="req1"))  # fmt: skip
    store.begin(boss, i, "approve", "ok", "Central de Serviços")
    mine = [x for x in store.items.values() if x["kind"] == "notification"]
    assert [x["title"] for x in mine] == ["Acesso a Central de Serviços liberado"] and mine[0][
        "to_users"
    ] == ["u9"]
    j = store.push(item_of("access", "servicos", "pede acesso", by_id="u8", by="Beto", to_perm="gerenciar_usuarios",
                           approval=approval_of("r2"), source="req2"))  # fmt: skip
    store.begin(boss, j, "reject", "", "Central de Serviços")
    assert "Pedido de acesso a Central de Serviços recusado" in [x["title"] for x in store.items.values()]
