"""Administration > Connections (F3a): CRUD, secrets, bindings and managed variables, broker and provisioning."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from admin_helper import PASSWORD, add_user, make_hub
from fastapi.testclient import TestClient

from hub.connections import broker as broker_mod
from hub.connections.broker import parse_cidrs, source_allowed, who_of
from hub.connections.provision import ObjectStore, names, policy
from hub.connections.store import Backend, ConnError, ConnStore, link_policy, valid_name
from hub.gateway.identity import sign_user
from hub.storage import Body, Item, StorageError

SECRET = "s3-secret-key-ULTRA-7788"
S3 = {
    "name": "docs-s3",
    "kind": "s3",
    "endpoint": "http://objstore:9000",
    "bucket": "docs",
    "access_key": "AKEY",
    "secret": SECRET,
    "bindings": [{"app": "ativos", "env": "producao"}],
}
SP = {
    "name": "contratos-sp",
    "kind": "sharepoint",
    "tenant_id": "contoso.onmicrosoft.com",
    "client_id": "11111111-2222-3333-4444-555555555555",
    "site": "https://contoso.sharepoint.com/sites/docs",
    "secret": "sp-client-secret-VALUE",
}


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return make_hub(tmp_path, monkeypatch)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def dele(c: TestClient, url: str) -> Any:
    return c.request("DELETE", url, headers={"content-type": "application/json"})


class FakeBackend:
    """In-memory provider in place of S3/Graph: records what the broker asks for."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {"a.txt": b"hello"}
        self.fail: StorageError | None = None
        self.calls: list[str] = []

    def _go(self, what: str) -> None:
        self.calls.append(what)
        if self.fail:
            raise self.fail

    async def ls(self, prefix: str = "") -> list[Item]:
        self._go(f"ls:{prefix}")
        return [Item(path=k, name=k, size=len(v)) for k, v in self.files.items()]

    async def meta(self, rel: str) -> Item:
        self._go(f"meta:{rel}")
        if rel not in self.files:
            raise StorageError("not_found", "Item: não encontrado")
        return Item(path=rel, name=rel, size=len(self.files[rel]))

    async def download(self, rel: str) -> tuple[str, int | None, AsyncIterator[bytes]]:
        self._go(f"download:{rel}")
        data = self.files[rel]

        async def gen() -> AsyncIterator[bytes]:
            yield data

        return "text/plain", len(data), gen()

    async def upload(self, rel: str, length: int, ctype: str, body: Body) -> Item:
        self._go(f"upload:{rel}:{length}:{ctype}")
        data = await body.read(length)
        await body.expect_end()
        self.files[rel] = data
        return Item(path=rel, name=rel, size=length, content_type=ctype)

    async def delete(self, rel: str) -> None:
        self._go(f"delete:{rel}")
        self.files.pop(rel, None)

    async def move(self, src: str, dst: str) -> Item:
        self._go(f"move:{src}:{dst}")
        self.files[dst] = self.files.pop(src)
        return Item(path=dst, name=dst)

    async def share_link(self, rel: str, kind: str, scope: str, expires_s: int | None) -> dict[str, Any]:
        self._go(f"link:{rel}:{kind}:{scope}:{expires_s}")
        return {"url": "https://x/l", "kind": kind, "expires": "2030-01-01T00:00:00Z"}

    async def test(self) -> list[dict[str, Any]]:
        return [
            {"key": "bucket", "ok": True, "detail": "ok", "ms": 1},
            {"key": "write", "ok": False, "detail": "negado", "ms": 2},
        ]


def fake(hub: TestClient, name: str) -> FakeBackend:
    store = cast(ConnStore, hub.app.state.conns)
    c = store.get(name)
    assert c is not None
    f = FakeBackend()
    store.clients[name] = (c["rev"], cast(Backend, f))
    return f


# ---- regras puras -------------------------------------------------------------------------------


def test_names_and_link_policy() -> None:
    assert (
        valid_name("contratos-docs")
        and not valid_name("Docs")
        and not valid_name("a")
        and not valid_name("x-")
    )
    assert names("ativos", "producao") == "app-ativos" and names("ativos", "staging") == "stg-ativos"
    for bad in ("Ativos", "a" * 40):
        with pytest.raises(ConnError):
            names(bad, "producao")
    c = {
        "kind": "sharepoint",
        "share_scope": "organization",
        "anonymous_link_apps": [],
        "allow_edit_links": False,
        "max_link_hours": 48,
    }
    assert link_policy(c, "ativos", "view", 24) == "organization"
    with pytest.raises(ConnError, match="de 1 a 48"):
        link_policy(c, "ativos", "view", 49)
    with pytest.raises(ConnError, match="Tipo de link"):
        link_policy(c, "ativos", "rw", 1)
    with pytest.raises(ConnError, match="edição não liberados"):
        link_policy(c, "ativos", "edit", 1)
    assert link_policy({**c, "allow_edit_links": True}, "ativos", "edit", 1) == "organization"
    anon = {**c, "share_scope": "anonymous", "anonymous_link_apps": ["ativos"], "allow_edit_links": True}
    assert (
        link_policy(anon, "ativos", "view", 1) == "anonymous"
        and link_policy(anon, "coletor", "view", 1) == "organization"
    )
    with pytest.raises(ConnError, match="edição não liberados"):
        link_policy(anon, "ativos", "edit", 1)  # never anonymous editing
    s3 = {**c, "kind": "s3", "anonymous_link_apps": ["ativos"]}
    assert link_policy(s3, "ativos", "view", 1) == "anonymous"
    with pytest.raises(ConnError, match="pré-assinados"):
        link_policy(s3, "coletor", "view", 1)
    assert "s3:DeleteObject" in policy("meu-bucket") and "arn:aws:s3:::meu-bucket/*" in policy("meu-bucket")


def test_who_of_and_source_guard() -> None:
    key, user = (
        b"k" * 32,
        base64.urlsafe_b64encode(json.dumps({"user": "ana@x"}).encode()).decode().rstrip("="),
    )
    sig = sign_user(key, "ativos", "producao", user, 1_000_000)
    assert who_of({"x-hub-user": user, "x-hub-user-sig": sig}, key, "ativos", "producao", 1_000_000) == (
        "ana@x",
        "",
    )
    actor, note = who_of({"x-hub-user": user, "x-hub-user-sig": sig}, key, "coletor", "producao", 1_000_000)
    assert actor == "app:coletor" and "não verificado" in note  # another app's signature is not valid
    assert who_of({"x-hub-user": user}, key, "ativos", "producao")[0] == "app:ativos"
    assert who_of({"x-hub-user": "%%%"}, key, "ativos", "producao") == ("app:ativos", "")
    assert who_of({}, key, "ativos", "producao") == ("app:ativos", "")
    nets = parse_cidrs("ativos=10.231.1.0/24, lixo, coletor=10.231.5.0/24, x=abc")
    assert source_allowed({}, "ativos", None)  # no list = only the token
    assert source_allowed(nets, "ativos", "10.231.1.9") and not source_allowed(nets, "ativos", "10.231.5.9")
    assert not source_allowed(nets, "servicos", "10.231.1.9") and not source_allowed(nets, "ativos", None)
    assert source_allowed(nets, "ativos", "::ffff:10.231.1.9") and not source_allowed(
        nets, "ativos", "fd00::1"
    )


# ---- connections: API and secrets ----


def test_connection_crud_masks_secret_everywhere(hub: TestClient, tmp_path: Path) -> None:
    r = hub.post("/api/admin/connections", json=S3)
    assert r.status_code == 201
    v = r.json()
    assert (
        v["secret"] == "••••••••"
        and SECRET not in r.text
        and v["allowed_ops"] == ["upload", "download", "list", "delete", "move"]
    )
    assert list(v)[:4] == ["name", "kind", "description", "tenant_id"] and v["max_upload_mb"] == 100
    listed = hub.get("/api/admin/connections").json()
    assert listed["connections"][0]["name"] == "docs-s3" and listed["broker"]["enabled"] is False
    assert listed["graph"]["default"] is True and SECRET not in json.dumps(listed)
    assert hub.post("/api/admin/connections", json=S3).status_code == 400  # nome repetido
    html = hub.get("/admin/connections").text
    assert SECRET not in html and 'data-conn-row="docs-s3"' in html and "AKEY · ••••••••" in html
    assert "Nunca testada" in html and "Ativos · Produção" in html and "Armazenamento provisionado" in html
    # nothing in clear text on disk or in the trail; the S3 key is encrypted in the vault
    disk = (tmp_path / "connections.json").read_text(encoding="utf-8")
    assert SECRET not in disk and '"secret": "' in disk
    assert SECRET not in (tmp_path / "audit" / "chain.jsonl").read_text(encoding="utf-8")
    assert SECRET not in (tmp_path / "admin.json").read_text(encoding="utf-8")
    titles = [e["title"] for e in run(hub.app.state.admin.cfg.read())["audit"]]  # type: ignore[attr-defined]
    assert "Conexão criada" in titles
    # editing without a credential keeps the current one; with a credential it replaces it and audits without the value
    r = hub.put(
        "/api/admin/connections/docs-s3", json={**S3, "secret": "", "description": "novo", "max_upload_mb": 5}
    )
    assert r.status_code == 200 and r.json()["description"] == "novo"
    assert hub.app.state.conns.get("docs-s3")["secret"] == SECRET  # type: ignore[attr-defined]
    r = hub.post("/api/admin/connections/docs-s3/rotate", json={"secret": "nova-chave-secreta-99"})
    assert r.status_code == 200 and "nova-chave" not in r.text
    assert hub.post("/api/admin/connections/docs-s3/rotate", json={"secret": ""}).status_code == 400
    audit = json.dumps(run(hub.app.state.admin.cfg.read())["audit"])  # type: ignore[attr-defined]
    assert "nova-chave-secreta-99" not in audit and SECRET not in audit
    assert dele(hub, "/api/admin/connections/docs-s3").status_code == 204
    assert dele(hub, "/api/admin/connections/docs-s3").status_code == 404


@pytest.mark.parametrize(
    ("patch", "msg"),
    [
        ({"name": "Docs"}, "Nome: 2 a 40"),
        ({"kind": "ftp"}, "Escolha o tipo"),
        ({"endpoint": "ftp://x"}, "começar com http"),
        ({"bucket": "Bad_Bucket"}, "Bucket inválido"),
        ({"secret": ""}, "secret key"),
        ({"access_key": ""}, "access key"),
        ({"base_folder": "../fora"}, "Pasta-base"),
        ({"allowed_ops": []}, "ao menos uma operação"),
        ({"max_upload_mb": 5000}, "de 1 a 2048"),
        ({"max_link_hours": 0}, "Validade máxima"),
        ({"bindings": [{"app": "nao", "env": "producao"}]}, "Aplicação desconhecida"),
        ({"bindings": [{"app": "ativos", "env": "dev"}]}, "Ambiente inválido"),
        ({"public_url": "ftp://x"}, "URL pública"),
        ({"endpoint": "http://169.254.169.254"}, "bloqueado"),
        ({"anonymous_link_apps": ["nao"]}, "Aplicação desconhecida"),
        ({"secret": "a\nb"}, "Segredo inválido"),
        ({"description": "x" * 300}, "Descrição"),
    ],
)
def test_s3_validation(hub: TestClient, patch: dict[str, Any], msg: str) -> None:
    r = hub.post("/api/admin/connections", json={**S3, **patch})
    assert r.status_code == 400 and msg in r.json()["error"]


@pytest.mark.parametrize(
    ("patch", "msg"),
    [
        ({"tenant_id": "a b"}, "Tenant ID"),
        ({"client_id": "nao-e-guid"}, "Client ID"),
        ({"site": "https://x.com/a?b"}, "Site:"),
        ({"share_scope": "todos"}, "Alcance do link"),
        ({"secret": ""}, "segredo do cliente"),
        ({"auth": "certificate"}, "certificado"),
        ({"share_scope": "anonymous"}, "Links anônimos: escolha"),
        (
            {"share_scope": "anonymous", "anonymous_link_apps": ["ativos"], "allow_edit_links": True},
            "não podem ser de edição",
        ),
    ],
)
def test_sharepoint_validation(hub: TestClient, patch: dict[str, Any], msg: str) -> None:
    r = hub.post("/api/admin/connections", json={**SP, **patch})
    assert r.status_code == 400 and msg in r.json()["error"]
    ok = hub.post(
        "/api/admin/connections", json={**SP, "share_scope": "anonymous", "anonymous_link_apps": ["ativos"]}
    )
    assert (
        ok.status_code == 201
        and ok.json()["allow_edit_links"] is False
        and "sp-client-secret-VALUE" not in ok.text
    )


def test_connection_rbac_and_json_only(hub: TestClient) -> None:
    hub.post("/api/admin/connections", json=S3)
    colab, po = add_user(hub, "colab", ["Colaborador"]), add_user(hub, "po", ["Product Owner"])
    for c in (colab, po):
        assert c.get("/api/admin/connections").status_code == 403
        assert c.post("/api/admin/connections", json=S3).status_code == 403
        assert c.post("/api/admin/connections/docs-s3/test", json={}).status_code == 403
        assert c.get("/admin/connections").status_code == 403
        assert c.post("/api/admin/connections/storage/ativos", json={}).status_code == 403
    lead = add_user(hub, "lead", ["Tech Lead"])  # editar_configuracoes
    assert (
        lead.get("/api/admin/connections").status_code == 200
        and lead.get("/admin/connections").status_code == 200
    )
    assert (
        hub.post("/api/admin/connections", content="x", headers={"content-type": "text/plain"}).status_code
        == 415
    )
    assert (
        hub.post(
            "/api/admin/connections", content="[1]", headers={"content-type": "application/json"}
        ).status_code
        == 400
    )
    assert hub.get("/api/admin/connections", headers={"authorization": "Bearer hubt_x"}).status_code == 401
    # no body (actions such as "provision storage") does not require Content-Type; with a non-JSON body it still returns 415
    empty = hub.post("/api/admin/connections/storage/ativos", headers={"content-type": ""})
    assert empty.status_code != 415
    assert hub.post("/api/admin/connections/storage/ativos", content="x=1",
                    headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 415  # fmt: skip


def test_bindings_sync_managed_variables_and_pending_restart(hub: TestClient) -> None:
    hub.post("/api/admin/connections", json={**S3, "bindings": []})
    names_before = {v["name"] for v in hub.get("/api/admin/env").json()}
    assert not any(n.startswith("HUB_STORAGE") or n.startswith("HUB_CONNECTIONS") for n in names_before)
    r = hub.put(
        "/api/admin/connections/docs-s3/bindings",
        json={"bindings": [{"app": "ativos", "env": "producao"}, {"app": "ativos", "env": "staging"}]},
    )
    assert r.status_code == 200 and len(r.json()["bindings"]) == 2
    env = {v["name"]: v for v in hub.get("/api/admin/env").json()}
    lst = env["HUB_CONNECTIONS_ATIVOS_PRODUCAO"]
    assert lst["value"] == "docs-s3=s3" and lst["managed"] is True and lst["inject_as"] == "HUB_CONNECTIONS"
    tok = env["HUB_STORAGE_TOKEN_ATIVOS_STAGING"]
    assert (
        tok["kind"] == "secreta"
        and tok["inject_as"] == "HUB_STORAGE_TOKEN"
        and tok["value"].startswith("••••••••")
    )
    assert env["HUB_STORAGE_URL"]["value"] == "http://hub-web:8081"
    assert "Reinício pendente" in hub.get("/admin/env").text
    assert (
        "Aplicar (reiniciar)" in hub.get("/admin/connections").text
    )  # app card with a connection and a pending restart
    # managed broker variables are reserved to the platform
    assert (
        hub.put(
            "/api/admin/env/HUB_STORAGE_URL", json={"value": "http://evil", "kind": "publica", "bindings": []}
        ).status_code
        == 400
    )
    assert (
        hub.post(
            "/api/admin/env", json={"name": "HUB_STORAGE_TOKEN", "value": "x" * 20, "kind": "secreta"}
        ).status_code
        == 400
    )
    # unbinding removes the variables from the app; with no bindings left, the broker address also disappears
    hub.put(
        "/api/admin/connections/docs-s3/bindings", json={"bindings": [{"app": "ativos", "env": "producao"}]}
    )
    env = {v["name"] for v in hub.get("/api/admin/env").json()}
    assert "HUB_STORAGE_TOKEN_ATIVOS_STAGING" not in env and "HUB_STORAGE_TOKEN_ATIVOS_PRODUCAO" in env
    hub.put("/api/admin/connections/docs-s3/bindings", json={"bindings": []})
    assert "HUB_STORAGE_URL" not in {v["name"] for v in hub.get("/api/admin/env").json()}
    assert hub.put("/api/admin/connections/docs-s3/bindings", json={"bindings": "x"}).status_code == 400
    assert hub.put("/api/admin/connections/nao-existe/bindings", json={"bindings": []}).status_code == 404


def test_test_connection_records_last_test_and_audits(hub: TestClient) -> None:
    hub.post("/api/admin/connections", json=S3)
    fake(hub, "docs-s3")
    r = hub.post("/api/admin/connections/docs-s3/test", json={})
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["steps"][1]["detail"] == "negado"
    assert hub.post("/api/admin/connections/nao-existe/test", json={}).status_code == 404
    audit = run(hub.app.state.admin.cfg.read())["audit"]  # type: ignore[attr-defined]
    assert any(
        e["title"] == "Teste de conexão falhou" and "falhou em write: negado" in e["detail"] for e in audit
    )
    html = hub.get("/admin/connections").text
    assert "Falhou · agora" in html and "status-maintenance" in html
    # editing invalidates the last test (the revision changes)
    hub.put("/api/admin/connections/docs-s3", json={**S3, "secret": ""})
    assert "Nunca testada" in hub.get("/admin/connections").text


def test_connections_page_markup(hub: TestClient) -> None:
    html = hub.get("/admin/connections").text
    assert (
        'data-conn-new-menu=""' in html
        and 'data-conn-new="sharepoint"' in html
        and 'data-conn-new="s3"' in html
    )
    assert "data-conn-provision-jump" in html and "Nenhuma conexão cadastrada." in html
    assert 'data-conn-storage-form=""' in html and "/assets/connections.js" in html
    assert "O serviço de armazenamento do hub está desligado" in html  # broker without HUB_BROKER_ADDR
    en = hub.get("/admin/connections", headers={"accept-language": "en"}).text
    assert "New connection" in en and "Never tested" not in en and "Provision storage" in en


# ---- broker -----------------------------------------------------------------------------------------


def broker_for(hub: TestClient) -> TestClient:
    app = broker_mod.build_broker(hub.app.state.conns, hub.app.state.admin, b"k" * 32)  # type: ignore[attr-defined]
    return TestClient(app)


def token_of(hub: TestClient, app: str = "ativos", env: str = "producao") -> str:
    return str(
        run(hub.app.state.admin.cfg.managed_var(f"HUB_STORAGE_TOKEN_{app.upper()}_{env.upper()}"))["value"]
    )  # type: ignore[attr-defined]


def test_broker_authorizes_by_binding_and_operation_and_audits(hub: TestClient) -> None:
    hub.post("/api/admin/connections", json={**S3, "allowed_ops": ["list", "download", "upload"]})
    f = fake(hub, "docs-s3")
    b = broker_for(hub)
    tok = token_of(hub)
    auth = {"authorization": f"Bearer {tok}"}
    assert b.get("/healthz").text == "ok"
    assert b.get("/_hub/storage").status_code == 401
    assert b.get("/_hub/storage", headers={"authorization": "Bearer " + "z" * 40}).status_code == 401
    r = b.get("/_hub/storage", headers=auth).json()
    assert r["app"] == "ativos" and r["env"] == "producao"
    assert r["connections"] == [
        {
            "name": "docs-s3",
            "kind": "s3",
            "ops": ["upload", "download", "list"],
            "max_upload_mb": 100,
            "max_link_hours": 168,
        }
    ]
    assert SECRET not in json.dumps(r)
    assert b.get("/_hub/storage/docs-s3/list", headers=auth).json()["items"][0]["name"] == "a.txt"
    assert b.get("/_hub/storage/docs-s3/meta", params={"path": "a.txt"}, headers=auth).json()["size"] == 5
    d = b.get("/_hub/storage/docs-s3/content", params={"path": "a.txt"}, headers=auth)
    assert (
        d.content == b"hello"
        and d.headers["content-type"].startswith("text/plain")
        and d.headers["x-content-type-options"] == "nosniff"
    )
    up = b.put(
        "/_hub/storage/docs-s3/content",
        params={"path": "n.txt"},
        content=b"12345",
        headers={**auth, "content-type": "text/plain"},
    )
    assert up.status_code == 201 and f.files["n.txt"] == b"12345"
    # operation not released, unknown connection and payload above the limit
    assert (
        b.delete("/_hub/storage/docs-s3/content", params={"path": "a.txt"}, headers=auth).status_code == 403
    )
    assert (
        b.post("/_hub/storage/docs-s3/move", json={"from": "a", "to": "b"}, headers=auth).status_code == 403
    )
    assert b.get("/_hub/storage/outra/list", headers=auth).status_code == 404
    hub.put(
        "/api/admin/connections/docs-s3",
        json={**S3, "secret": "", "max_upload_mb": 1, "allowed_ops": ["list", "download", "upload"]},
    )
    f = fake(hub, "docs-s3")
    big = b.put(
        "/_hub/storage/docs-s3/content",
        params={"path": "g"},
        content=b"x" * 10,
        headers={**auth, "content-length": str(2 * 1024 * 1024)},
    )
    assert big.status_code in (400, 413)
    assert (
        b.put(
            "/_hub/storage/docs-s3/content", params={"path": "../x"}, content=b"1", headers=auth
        ).status_code
        == 400
    )
    # audit: every app operation, with app, environment, connection and result; never credentials
    audit = run(hub.app.state.admin.cfg.read())["audit"]  # type: ignore[attr-defined]
    mine = [e for e in audit if e["title"].startswith("Armazenamento")]
    titles = {e["title"] for e in mine}
    assert {
        "Armazenamento: listar",
        "Armazenamento: baixar",
        "Armazenamento: enviar",
        "Armazenamento: operação negada",
        "Armazenamento: acesso negado",
    } <= titles
    assert all(
        e["scope"] == "ativos" and e["actor"] == "app:ativos" and "Produção" in e["detail"] for e in mine
    )
    blob = json.dumps(audit)
    assert SECRET not in blob and tok not in blob


def test_broker_isolation_between_apps_environments_and_rotation(hub: TestClient) -> None:
    hub.post("/api/admin/connections", json=S3)
    hub.post(
        "/api/admin/connections",
        json={**S3, "name": "outra", "bindings": [{"app": "coletor", "env": "producao"}]},
    )
    fake(hub, "docs-s3")
    fake(hub, "outra")
    b = broker_for(hub)
    ativos, coletor = token_of(hub, "ativos"), token_of(hub, "coletor")
    assert ativos != coletor
    h = lambda t: {"authorization": f"Bearer {t}"}  # noqa: E731
    assert b.get("/_hub/storage/docs-s3/list", headers=h(ativos)).status_code == 200
    assert (
        b.get("/_hub/storage/docs-s3/list", headers=h(coletor)).status_code == 404
    )  # other app: does not even know whether it exists
    assert b.get("/_hub/storage/outra/list", headers=h(ativos)).status_code == 404
    # the production token is not valid for staging (exact app x environment binding)
    hub.put(
        "/api/admin/connections/docs-s3/bindings",
        json={"bindings": [{"app": "ativos", "env": "producao"}, {"app": "ativos", "env": "staging"}]},
    )
    stg = token_of(hub, "ativos", "staging")
    assert stg != ativos and b.get("/_hub/storage", headers=h(stg)).json()["env"] == "staging"
    # rotation: the old token stops working immediately
    assert hub.post("/api/admin/connections/tokens/ativos/producao/rotate", json={}).status_code == 200
    assert b.get("/_hub/storage", headers=h(ativos)).status_code == 401
    new = token_of(hub, "ativos")
    assert new != ativos and b.get("/_hub/storage", headers=h(new)).status_code == 200
    assert hub.post("/api/admin/connections/tokens/servicos/producao/rotate", json={}).status_code == 404
    # unbinding cuts access right away (before any app restart)
    hub.put("/api/admin/connections/docs-s3/bindings", json={"bindings": []})
    assert b.get("/_hub/storage/docs-s3/list", headers=h(new)).status_code in (401, 404)
    # an ordinary variable with the same name as the token does NOT authenticate
    hub.post(
        "/api/admin/env",
        json={
            "name": "TOKEN_FALSO",
            "value": "f" * 40,
            "kind": "secreta",
            "bindings": [{"app": "servicos", "env": "producao"}],
        },
    )
    assert b.get("/_hub/storage", headers=h("f" * 40)).status_code == 401


def test_broker_share_link_policy_move_and_provider_errors(hub: TestClient) -> None:
    hub.post(
        "/api/admin/connections",
        json={
            **S3,
            "allowed_ops": ["share_link", "move", "list", "delete"],
            "anonymous_link_apps": ["ativos"],
            "max_link_hours": 48,
        },
    )
    f = fake(hub, "docs-s3")
    b = broker_for(hub)
    auth = {"authorization": f"Bearer {token_of(hub)}"}
    ok = b.post("/_hub/storage/docs-s3/share-link", json={"path": "a.txt", "expires_hours": 24}, headers=auth)
    assert ok.status_code == 200 and "link:a.txt:view:anonymous:86400" in f.calls
    assert (
        b.post("/_hub/storage/docs-s3/share-link", json={"path": "a.txt"}, headers=auth).status_code == 200
    )  # default 24 h
    assert (
        b.post(
            "/_hub/storage/docs-s3/share-link", json={"path": "a.txt", "expires_hours": 99}, headers=auth
        ).status_code
        == 400
    )
    assert (
        b.post(
            "/_hub/storage/docs-s3/share-link", json={"path": "a.txt", "kind": "edit"}, headers=auth
        ).status_code
        == 403
    )
    assert b.post("/_hub/storage/docs-s3/share-link", content="não é json", headers=auth).status_code == 400
    mv = b.post("/_hub/storage/docs-s3/move", json={"from": "a.txt", "to": "b/c.txt"}, headers=auth)
    assert mv.status_code == 200 and "b/c.txt" in f.files
    assert b.post("/_hub/storage/docs-s3/move", json={"from": "a.txt"}, headers=auth).status_code == 400
    # provider errors become the contract status, without the provider's text for the app
    f.fail = StorageError("unavailable", "provedor limitando", 9, detail="corpo interno do provedor")
    r = b.get("/_hub/storage/docs-s3/list", headers=auth)
    assert r.status_code == 503 and r.headers["retry-after"] == "9" and "corpo interno" not in r.text
    f.fail = StorageError("forbidden", "sem permissão no site", detail="Sites.Selected faltando")
    r = b.get("/_hub/storage/docs-s3/list", headers=auth)
    assert r.status_code == 502 and "Sites.Selected" not in r.text
    audit = json.dumps(run(hub.app.state.admin.cfg.read())["audit"])  # type: ignore[attr-defined]
    assert "Sites.Selected faltando" in audit  # the provider detail stays only in the audit


def test_broker_attributes_verified_user_and_concurrency_limit(
    hub: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub.post("/api/admin/connections", json=S3)
    fake(hub, "docs-s3")
    b = broker_for(hub)
    auth = {"authorization": f"Bearer {token_of(hub)}"}
    user = base64.urlsafe_b64encode(json.dumps({"user": "ana@demo.local"}).encode()).decode().rstrip("=")
    import time

    sig = sign_user(b"k" * 32, "ativos", "producao", user, int(time.time()))
    b.get("/_hub/storage/docs-s3/list", headers={**auth, "x-hub-user": user, "x-hub-user-sig": sig})
    b.get("/_hub/storage/docs-s3/meta", params={"path": "a.txt"}, headers={**auth, "x-hub-user": user})
    audit = run(hub.app.state.admin.cfg.read())["audit"]  # type: ignore[attr-defined]
    assert any(e["actor"] == "ana@demo.local" and e["title"] == "Armazenamento: listar" for e in audit)
    assert any(e["actor"] == "app:ativos" and "não verificado" in e["detail"] for e in audit)


async def test_broker_per_app_concurrency_limit(hub: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_BROKER_PER_APP", "1")
    hub.post("/api/admin/connections", json=S3)
    f = fake(hub, "docs-s3")
    gate, entered = asyncio.Event(), asyncio.Event()
    original = f.ls

    async def slow(prefix: str = "") -> list[Item]:
        entered.set()
        await gate.wait()
        return await original(prefix)

    monkeypatch.setattr(f, "ls", slow)
    app = broker_mod.build_broker(hub.app.state.conns, hub.app.state.admin, b"k" * 32)  # type: ignore[attr-defined]
    managed = await hub.app.state.admin.cfg.managed_var("HUB_STORAGE_TOKEN_ATIVOS_PRODUCAO")  # type: ignore[attr-defined]
    auth = {"authorization": f"Bearer {managed['value']}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://broker") as c:
        first = asyncio.create_task(c.get("/_hub/storage/docs-s3/list", headers=auth))
        await asyncio.wait_for(entered.wait(), 5)
        busy = await c.get(
            "/_hub/storage/docs-s3/list", headers=auth
        )  # no slot: the app already has an operation in progress
        assert busy.status_code == 429 and busy.headers["retry-after"] == "2"
        gate.set()
        assert (await first).status_code == 200
        assert (await c.get("/_hub/storage/docs-s3/list", headers=auth)).status_code == 200  # slot is back
    audit = (await hub.app.state.admin.cfg.read())["audit"]  # type: ignore[attr-defined]
    assert any(e["title"] == "Armazenamento: limite de operações simultâneas" for e in audit)


def test_broker_network_guard(hub: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    hub.post("/api/admin/connections", json=S3)
    fake(hub, "docs-s3")
    monkeypatch.setenv("HUB_BROKER_APP_CIDRS", "ativos=10.231.1.0/24")
    b = broker_for(hub)  # TestClient arrives as "testclient": outside the app's network
    r = b.get("/_hub/storage", headers={"authorization": f"Bearer {token_of(hub)}"})
    assert r.status_code == 403 and "Origem não permitida" in r.json()["error"]
    audit = run(hub.app.state.admin.cfg.read())["audit"]  # type: ignore[attr-defined]
    assert any(
        e["title"] == "Armazenamento: acesso negado" and "fora da rede do app" in e["detail"] for e in audit
    )


# ---- provisionamento ------------------------------------------------------------------------------------


class FakeMc:
    def __init__(self) -> None:
        self.buckets: set[str] = set()
        self.users: set[str] = set()
        self.log: list[list[str]] = []
        self.fail_on: str | None = None

    async def __call__(self, args: list[str], env: dict[str, str]) -> tuple[int, str]:
        a = [x for x in args if x not in ("--quiet",)][2:]  # without --config-dir <dir>
        self.log.append(a)
        assert env["MC_HOST_hub"].startswith("http://hubadmin:")
        if self.fail_on and a[: len(self.fail_on.split())] == self.fail_on.split():
            return 1, f"falha em {' '.join(a)} com segredo {a[-1]}"
        match a:
            case ["stat", target]:
                return (0, "") if target.removeprefix("hub/") in self.buckets else (1, "nao existe")
            case ["mb", "--ignore-existing", target]:
                self.buckets.add(target.removeprefix("hub/"))
            case ["admin", "user", "info", "hub", user]:
                return (0, "") if user in self.users else (1, "")
            case ["admin", "user", "add", "hub", user, _]:
                self.users.add(user)
            case ["admin", "user", "remove", "hub", user]:
                self.users.discard(user)
            case ["rb", *_, target]:
                self.buckets.discard(target.removeprefix("hub/"))
        return 0, ""


def provisioner(hub: TestClient, mc: FakeMc) -> ObjectStore:
    return ObjectStore("http://hubadmin:pw@objstore:9000", mc)


def test_provision_idempotent_with_undo_and_deprovision(hub: TestClient) -> None:
    from hub.connections import provision as prov

    conns = cast(ConnStore, hub.app.state.conns)
    mc = FakeMc()
    obj = provisioner(hub, mc)
    status, out = run(prov.provision(conns, obj, "ativos", "producao", "admin", "http://hub-web:8081"))
    assert status == 201 and out["changed"] and out["storage"]["bucket"] == "app-ativos"
    c = conns.get("app-ativos")
    assert (
        c is not None
        and c["kind"] == "s3"
        and c["endpoint"] == "http://objstore:9000"
        and c["bindings"] == [{"app": "ativos", "env": "producao"}]
    )
    assert c["secret"] and "http://hubadmin" not in json.dumps(c)
    assert {"app-ativos"} == mc.buckets and {"app-ativos"} == mc.users
    status, again = run(prov.provision(conns, obj, "ativos", "producao", "admin", "http://hub-web:8081"))
    assert status == 200 and again["changed"] is False and len(conns.all()) == 1
    # the connection appears on the page and the app receives the broker variables
    html = hub.get("/admin/connections").text
    assert 'data-conn-storage-row="ativos|producao"' in html and "app-ativos" in html
    assert "HUB_STORAGE_TOKEN_ATIVOS_PRODUCAO" in {v["name"] for v in hub.get("/api/admin/env").json()}
    # deprovisioning requires the bucket name; with objects, force and the password of whoever asks
    fake_be = FakeBackend()
    conns.clients["app-ativos"] = (c["rev"], cast(Backend, fake_be))

    async def step_up(pw: str) -> str | None:
        return None if pw == PASSWORD else "Senha incorreta"

    with pytest.raises(ConnError, match="Confirme digitando"):
        run(prov.deprovision(conns, obj, "ativos", "producao", "admin", {"confirm": "x"}, step_up, "u"))
    with pytest.raises(ConnError, match="tem objetos"):
        run(
            prov.deprovision(
                conns, obj, "ativos", "producao", "admin", {"confirm": "app-ativos"}, step_up, "u"
            )
        )
    with pytest.raises(ConnError, match="Senha incorreta"):
        run(
            prov.deprovision(
                conns,
                obj,
                "ativos",
                "producao",
                "admin",
                {"confirm": "app-ativos", "force": True, "password": "errada"},
                step_up,
                "u",
            )
        )
    res = run(
        prov.deprovision(
            conns,
            obj,
            "ativos",
            "producao",
            "admin",
            {"confirm": "app-ativos", "force": True, "password": PASSWORD},
            step_up,
            "u",
        )
    )
    assert res["removed"] and not conns.all() and not mc.buckets and not mc.users and not conns.storage
    assert "HUB_STORAGE_TOKEN_ATIVOS_PRODUCAO" not in {v["name"] for v in hub.get("/api/admin/env").json()}
    with pytest.raises(ConnError, match="Nenhum armazenamento"):
        run(
            prov.deprovision(
                conns, obj, "ativos", "producao", "admin", {"confirm": "app-ativos"}, step_up, "u"
            )
        )
    # failure midway rolls back in reverse order and leaves no trace; the secret never leaks into the message
    mc2 = FakeMc()
    mc2.fail_on = "admin policy attach"
    with pytest.raises(ConnError) as e:
        run(prov.provision(conns, provisioner(hub, mc2), "coletor", "staging", "admin", "u"))
    assert "Object store" in str(e.value) and not conns.all() and not conns.storage
    assert not mc2.buckets and not mc2.users and "coletor" not in json.dumps(conns.storage)
    secret_tail = next(c for c in mc2.log if c[:3] == ["admin", "user", "add"])[-1]
    assert secret_tail not in str(e.value)
    # refuses what the hub did not create
    mc3 = FakeMc()
    mc3.buckets.add("app-servicos")
    with pytest.raises(ConnError, match="não foi provisionado pelo hub"):
        run(prov.provision(conns, provisioner(hub, mc3), "servicos", "producao", "admin", "u"))
    with pytest.raises(ConnError, match="não encontrada"):
        run(prov.provision(conns, obj, "nao-existe", "producao", "admin", "u"))
    with pytest.raises(ConnError, match="não configurado"):
        run(prov.provision(conns, ObjectStore(""), "ativos", "producao", "admin", "u"))


def test_storage_api_unavailable_without_object_store_and_env_validation(hub: TestClient) -> None:
    r = hub.get("/api/admin/connections/storage").json()
    assert r["available"] is False and "HUB_S3_ADMIN_URL" in r["reason"] and r["storage"] == []
    assert hub.post("/api/admin/connections/storage/ativos", json={}).status_code == 503
    assert hub.post("/api/admin/connections/storage/ativos?env=dev", json={}).status_code == 400
    assert hub.post("/api/admin/connections/storage/ativos/deprovision", json={}).status_code == 404
    assert (
        hub.post(
            "/api/admin/connections/storage/ativos/deprovision",
            content="x",
            headers={"content-type": "application/json"},
        ).status_code
        == 400
    )
