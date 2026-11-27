"""Remove an application (Containers › details): guards, the background job and nothing left behind in any store."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from admin_helper import make_hub, registry
from api_helper import api_hub, bearer, make_token
from fastapi.testclient import TestClient
from test_connections import FakeMc
from test_mcp_admin import call, tools

from hub.api.ops_admin import ADMIN_OPS
from hub.connections import provision as prov
from hub.connections.provision import ObjectStore
from hub.ops import model as m
from hub.ops import newapp

PW = "test-password"  # the Administrator's password (auth_helper)
NOVO = {"id": "novoapp", "name": "Novo App", "version": "1.0.0", "routes": ["/novoapp/"]}


# ---- named fakes ------------------------------------------------------------------------------------------
class RemoveDocker:
    """Containers and pinned images of the daemon (only what the removal touches)."""

    def __init__(self, names: list[str], tags: list[str]) -> None:
        self.names, self.tags, self.calls = set(names), set(tags), []
        self.containers = SimpleNamespace(list=self._list, get=self._get)
        self.images = SimpleNamespace(list=self._images, remove=self._untag)

    def _box(self, n: str) -> Any:
        return SimpleNamespace(name=n, status="running", attrs={"State": {}, "Config": {"Env": []}},
                               image=SimpleNamespace(tags=[]), stats=lambda stream=False: {},
                               stop=lambda timeout=10: None, remove=lambda force=False: self.names.discard(n))  # fmt: skip

    def _list(self, **kw: Any) -> list[Any]:
        return [self._box(n) for n in sorted(self.names)]

    def _get(self, name: str) -> Any:
        if name not in self.names:
            raise KeyError(name)
        return self._box(name)

    def _images(self, name: str) -> list[Any]:
        return [SimpleNamespace(tags=sorted(self.tags), attrs={})]

    def _untag(self, ref: str, noprune: bool = False) -> None:
        if ref.endswith(":in-use"):
            raise RuntimeError("image is being used by a running container")
        self.tags.discard(ref)

    def version(self) -> dict[str, Any]:
        return {"Version": "28"}

    def info(self) -> dict[str, Any]:
        return {"NCPU": 4, "MemTotal": 8 << 30}


class FakePostgres:
    """The apps' Postgres as seen by `Backups._psql`: roles and databases with their comments."""

    def __init__(self) -> None:
        self.roles: dict[str, str] = {}
        self.dbs: dict[str, tuple[str, str]] = {}  # name -> (owner, comment)
        self.sql: list[str] = []

    async def psql(self, db: str, *stmts: str) -> str:
        self.sql += stmts
        for s in stmts:
            self._apply(s)
        if stmts and stmts[0].startswith("SELECT 'role='"):
            found = re.search(r"rolname = '([^']+)'", stmts[0])
            assert found is not None
            n = found[1]
            owner, dbc = self.dbs.get(n, ("!", ""))
            return f"role={self.roles.get(n, '!')}\nowner={owner}\ndbc={dbc}\n"
        return ""

    def _apply(self, s: str) -> None:
        if m_ := re.match(r'COMMENT ON ROLE "([^"]+)" IS \'([^\']*)\'', s):
            self.roles[m_[1]] = m_[2]
        elif m_ := re.match(r'CREATE DATABASE "([^"]+)" OWNER "([^"]+)"', s):
            self.dbs[m_[1]] = (m_[2], "")
        elif m_ := re.match(r'COMMENT ON DATABASE "([^"]+)" IS \'([^\']*)\'', s):
            self.dbs[m_[1]] = (self.dbs[m_[1]][0], m_[2])
        elif m_ := re.match(r'DROP DATABASE IF EXISTS "([^"]+)"', s):
            self.dbs.pop(m_[1], None)
        elif m_ := re.match(r'DROP ROLE IF EXISTS "([^"]+)"', s):
            self.roles.pop(m_[1], None)


class FakeBundle:
    """`Backups.create_bundle`: writes the zip where the real one does and remembers the call."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir, self.calls = data_dir, []

    async def __call__(self, app: str, env: str, volumes: list[str], hooks: bool, progress: Any) -> Any:
        self.calls.append((app, env))
        name = f"{app}-999.zip"
        (self.data_dir / "backups" / app).mkdir(parents=True, exist_ok=True)
        (self.data_dir / "backups" / app / name).write_bytes(b"zip")
        return name, "ab" * 32, {"size": 3, "count": 1, "summary": "banco app_novoapp", "consistency": "ok"}


# ---- helpers ------------------------------------------------------------------------------------------------
def wait_job(c: TestClient, jid: int) -> dict[str, Any]:
    for _ in range(300):
        j = c.get(f"/api/ops/jobs/{jid}").json()
        if j["done"]:
            return j  # type: ignore[no-any-return]
        time.sleep(0.02)
    raise AssertionError("job não terminou")


def pcall(c: TestClient, fn: Any, *a: Any) -> Any:
    assert c.portal is not None
    return c.portal.call(fn, *a)


def remove(c: TestClient, app: str, **body: Any) -> Any:
    return c.post(f"/api/ops/apps/{app}/remove", json={"password": PW, **body})


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with make_hub(tmp_path, monkeypatch) as c:
        st = c.app.state  # type: ignore[attr-defined]
        st.fake_pg = FakePostgres()
        st.ops_backups._psql = st.fake_pg.psql
        st.fake_mc = FakeMc()
        st.ops_backups.objstore = ObjectStore("http://hubadmin:pw@objstore:9000", st.fake_mc)
        st.fake_docker = RemoveDocker(["app-hub-app-novoapp", "app-hub-app-novoapp__staging", "app-hub-app-inventory"],
                                      ["app-hub-app-novoapp:keep-1", "app-hub-app-novoapp:baseline-2"])  # fmt: skip
        st.ops_engine.factory = lambda: st.fake_docker
        yield c


def deployed(c: TestClient, tmp: Path) -> None:
    """A new app with everything a real one accumulates: registry, token, database, storage, data and preferences."""
    st = c.app.state  # type: ignore[attr-defined]
    assert newapp.register(tmp, st.gateway.registry, st.admin.cfg.apps, NOVO)
    pcall(c, st.admin.cfg.ensure_internal_tokens, "t", lambda a: None)
    assert c.post("/api/ops/provision/novoapp/database", json={}).status_code == 200
    pcall(
        c,
        prov.provision,
        st.conns,
        st.ops_backups.objstore,
        "novoapp",
        "producao",
        "t",
        "http://hub-web:8081",
    )
    ops = json.loads((tmp / "ops.json").read_text(encoding="utf-8")) if (tmp / "ops.json").exists() else {}
    ops["deployments"] = [{"id": 1, "app": "novoapp", "env": m.PRODUCAO, "status": m.IMPLANTADO, "version": "1.0.0",
                           "image": "app-hub-app-novoapp:keep-1", "package": "novoapp-1.0.0-abc.hubapp"}]  # fmt: skip
    ops["backups"] = [
        {"id": 1, "app": "novoapp", "kind": "Agendado", "file": "novoapp-1.zip", "status": "Concluído"}
    ]
    ops["limits"] = {"novoapp": {"cpu": 0.5, "memory": "256Mi", "pids": 50}}
    (tmp / "ops.json").write_text(json.dumps(ops), encoding="utf-8")
    for p in ("apps/novoapp/data/x.txt", "apps/novoapp__staging/data/y.txt", "backups/novoapp/novoapp-1.zip",
              "outbox/novoapp.json", "packages/novoapp-1.0.0-abc.hubapp"):  # fmt: skip
        (tmp / p).parent.mkdir(parents=True, exist_ok=True)
        (tmp / p).write_text("x", encoding="utf-8")
    st.ux.toggle_favorite("u-test", "novoapp", True)
    st.ux.visit("u-test", "/apps/novoapp/items", "Itens", 1)
    st.inbox.grants["u-other"] = ["novoapp", "inventory"]
    tok = c.post("/api/me/tokens", json={"name": "so-novo", "apps": ["novoapp"], "actions": ["api:read"]})
    assert tok.status_code == 201, tok.text


# ---- guards ---------------------------------------------------------------------------------------------------
def test_guards_confirm_password_unknown_and_busy(hub: TestClient) -> None:
    assert remove(hub, "inventory", confirm="outro").status_code == 400
    assert remove(hub, "inventory", confirm="inventory", password="errada").status_code == 403
    assert hub.post("/api/ops/apps/inventory/remove", json={"confirm": "inventory"}).status_code == 403
    assert remove(hub, "inventory", confirm="inventory", extra=1).status_code == 400  # closed body
    assert remove(hub, "nao-existe", confirm="nao-existe").status_code == 404
    ops = hub.app.state.ops  # type: ignore[attr-defined]

    async def busy(kind: str) -> None:
        async with ops.locked(True) as d:
            if kind == "deploy":
                d["deployments"].append(
                    {"id": 9, "app": "inventory", "env": m.STAGING, "status": m.IMPLANTANDO}
                )
            else:
                d["maintenance"]["inventory|producao"] = {"kind": "restauracao"}

    pcall(hub, busy, "deploy")
    r = remove(hub, "inventory", confirm="App Inventory")
    assert r.status_code == 409 and "implantação" in r.json()["error"].lower()
    hub.app.state.ops.write({**ops.read(), "deployments": []})  # type: ignore[attr-defined]
    pcall(hub, busy, "maintenance")
    assert remove(hub, "inventory", confirm="inventory").status_code == 409
    hub.app.state.ops.write({**ops.read(), "maintenance": {}})  # type: ignore[attr-defined]
    hub.app.state.ops_jobs.busy.add("inventory")  # type: ignore[attr-defined]
    assert remove(hub, "inventory", confirm="inventory").status_code == 409
    assert hub.app.state.gateway.registry.get("inventory") is not None  # type: ignore[attr-defined]


def test_wrong_confirm_in_english_is_translated(hub: TestClient) -> None:
    body = {"confirm": "x", "password": PW}
    r = hub.post("/api/ops/apps/inventory/remove", json=body, headers={"accept-language": "en"})
    assert r.status_code == 400 and "type the application's name" in r.json()["error"]


# ---- the whole removal -------------------------------------------------------------------------------------------
def test_removing_a_deployed_app_leaves_nothing_behind(hub: TestClient, tmp_path: Path) -> None:
    deployed(hub, tmp_path)
    st = hub.app.state  # type: ignore[attr-defined]
    st.ops_backups.create_bundle = bundle = FakeBundle(tmp_path)
    r = remove(hub, "novoapp", confirm=" novo app ")
    assert r.status_code == 202, r.text
    j = wait_job(hub, r.json()["id"])
    assert j["ok"], j
    assert bundle.calls == [("novoapp", "producao")]
    # registry, files of the registry and the next boot
    assert st.gateway.registry.get("novoapp") is None and "novoapp" not in st.admin.apps
    assert "novoapp" not in st.ops.apps
    assert json.loads((tmp_path / "apps.dynamic.json").read_text(encoding="utf-8")) == []
    again = registry()
    newapp.load_dynamic(tmp_path, again)
    newapp.apply_removed(tmp_path, again)
    assert again.get("novoapp") is None
    # admin.json: no variable, token or binding of the app
    env = pcall(hub, st.admin.cfg.read)["env"]
    assert not [
        v["name"] for v in env if "NOVOAPP" in v["name"] or any(b["app"] == "novoapp" for b in v["bindings"])
    ]
    assert "novoapp" not in st.admin.tokens
    # ops.json: only the pre-removal backup stays
    ops = st.ops.read()
    assert not [x for x in ops["deployments"] if x["app"] == "novoapp"] and "novoapp" not in ops["limits"]
    assert [(b["kind"], b["file"]) for b in ops["backups"] if b["app"] == "novoapp"] == [
        ("Pré-remoção", "novoapp-999.zip")
    ]
    assert not [p for p in ops["policies"] if p["app"] == "novoapp"]
    # connections, object store, database, docker
    assert st.conns.all() == [] and st.conns.storage == []
    assert st.fake_mc.buckets == set() and st.fake_mc.users == set()
    assert "app_novoapp" not in st.fake_pg.dbs and "app_novoapp" not in st.fake_pg.roles
    assert st.fake_docker.names == {"app-hub-app-inventory"} and st.fake_docker.tags == set()
    # files
    assert sorted(p.name for p in (tmp_path / "backups" / "novoapp").iterdir()) == ["novoapp-999.zip"]
    for p in (
        "apps/novoapp",
        "apps/novoapp__staging",
        "outbox/novoapp.json",
        "packages/novoapp-1.0.0-abc.hubapp",
    ):
        assert not (tmp_path / p).exists(), p
    # people: token revoked (no app left = would mean all), favorites, recents, Inbox grants
    tokens = [t for t in hub.get("/api/me/tokens").json()["items"] if t["name"] == "so-novo"]
    assert len(tokens) == 1 and tokens[0]["active"] is False and tokens[0]["apps"] == []
    prefs = st.ux.prefs("u-test")
    assert prefs.favorites == [] and not [x for x in prefs.recents if "novoapp" in x.path]
    assert st.inbox.grants["u-other"] == ["inventory"]
    # one audit entry with the steps (the trail itself is never touched)
    audit = [e for e in hub.get("/api/admin/audit?limit=200").json() if e["title"] == "Aplicação removida"]
    assert (
        len(audit) == 1
        and audit[0]["scope"] == "novoapp"
        and "banco app_novoapp apagado" in audit[0]["detail"]
    )
    assert "remoção:" in json.dumps(hub.get("/api/admin/logs?minutes=60").json(), ensure_ascii=False)
    assert hub.get("/admin/runtime").text.count('data-ops-row="novoapp|') == 0


def test_keeping_the_data_only_takes_the_app_out(hub: TestClient, tmp_path: Path) -> None:
    deployed(hub, tmp_path)
    st = hub.app.state  # type: ignore[attr-defined]
    st.ops_backups.create_bundle = bundle = FakeBundle(tmp_path)
    r = remove(hub, "novoapp", confirm="novoapp", backup=True, keep_data=True)
    assert wait_job(hub, r.json()["id"])["ok"]
    assert bundle.calls == []  # nothing is deleted: no backup needed
    assert st.gateway.registry.get("novoapp") is None
    assert "app_novoapp" in st.fake_pg.dbs and st.fake_mc.buckets == {"app-novoapp"}
    assert (tmp_path / "apps/novoapp/data/x.txt").exists() and (
        tmp_path / "backups/novoapp/novoapp-1.zip"
    ).exists()
    assert [c["bindings"] for c in st.conns.all()] == [[]]  # kept, but bound to nothing
    assert [b["file"] for b in st.ops.read()["backups"]] == ["novoapp-1.zip"]


def test_a_failed_backup_removes_nothing(hub: TestClient, tmp_path: Path) -> None:
    deployed(hub, tmp_path)
    st = hub.app.state  # type: ignore[attr-defined]

    async def broken(*a: Any) -> Any:
        raise m.OpsError("pg_dump falhou")

    st.ops_backups.create_bundle = broken
    j = wait_job(hub, remove(hub, "novoapp", confirm="novoapp").json()["id"])
    assert not j["ok"] and "pg_dump falhou" in j["message"]
    assert st.gateway.registry.get("novoapp") is not None and "app_novoapp" in st.fake_pg.dbs
    assert "novoapp" not in st.ops_jobs.busy


def test_an_image_the_daemon_refuses_is_reported_and_the_rest_goes(hub: TestClient) -> None:
    st = hub.app.state  # type: ignore[attr-defined]
    st.fake_docker.tags = {"app-hub-app-inventory:keep-1", "app-hub-app-inventory:in-use"}
    j = wait_job(hub, remove(hub, "inventory", confirm="inventory", backup=False).json()["id"])
    assert j["ok"] and st.fake_docker.tags == {"app-hub-app-inventory:in-use"}
    assert any("mantidas: app-hub-app-inventory:in-use" in s["detail"] for s in j["steps"])


def test_a_failing_step_is_reported_and_the_others_still_run(hub: TestClient) -> None:
    st = hub.app.state  # type: ignore[attr-defined]

    async def down(db: str, *stmts: str) -> str:
        raise m.OpsError("psql: servidor fora do ar")

    st.ops_backups._psql = down
    j = wait_job(hub, remove(hub, "inventory", confirm="inventory", backup=False).json()["id"])
    assert not j["ok"] and "Banco de dados" in j["message"]
    assert st.gateway.registry.get("inventory") is None and "inventory" not in st.admin.apps
    audit = [e for e in hub.get("/api/admin/audit?limit=50").json() if e["title"] == "Aplicação removida"]
    assert len(audit) == 1 and "servidor fora do ar" in audit[0]["detail"]


def test_a_running_rollout_blocks_the_removal(hub: TestClient) -> None:
    import asyncio

    rollout = hub.app.state.ops_rollout  # type: ignore[attr-defined]

    async def hold() -> None:
        lock = rollout.locks.setdefault("inventory|producao", asyncio.Lock())
        await lock.acquire()

    pcall(hub, hold)
    r = remove(hub, "inventory", confirm="inventory")
    assert r.status_code == 409 and "blue/green" in r.json()["error"]


def test_a_database_without_the_hub_marker_is_kept(hub: TestClient) -> None:
    st = hub.app.state  # type: ignore[attr-defined]
    st.fake_pg.roles["app_inventory"] = "criado por outra pessoa"
    st.fake_pg.dbs["app_inventory"] = ("app_inventory", "")
    j = wait_job(hub, remove(hub, "inventory", confirm="inventory", backup=False).json()["id"])
    assert j["ok"] and "app_inventory" in st.fake_pg.dbs
    assert any("não foi criado pelo hub" in s["detail"] for s in j["steps"])


# ---- sample apps and the page ---------------------------------------------------------------------------------------
def test_page_has_the_remove_actions(hub: TestClient) -> None:
    page = hub.get("/admin/runtime").text
    detail = page.split('data-ops-detail="inventory|producao"', 1)[1].split('class="ops-detail"', 1)[0]
    assert 'data-ops-remove="inventory"' in detail and 'data-name="App Inventory"' in detail
    assert "Remover aplicação" in detail and 'data-requires="editar_configuracoes"' in detail
    assert "data-ops-remove-samples" in page and "Remover apps de exemplo" in page
    en = hub.get("/admin/runtime", headers={"accept-language": "en"}).text
    assert "Remove application" in en and "Remove sample apps" in en


def test_removing_the_sample_apps_one_after_the_other(hub: TestClient, tmp_path: Path) -> None:
    st = hub.app.state  # type: ignore[attr-defined]
    assert newapp.register(tmp_path, st.gateway.registry, st.admin.cfg.apps, NOVO)  # deployed: not a sample
    bad = hub.post("/api/ops/apps/remove-samples", json={"confirm": "novoapp", "password": PW})
    assert bad.status_code == 400
    r = hub.post(
        "/api/ops/apps/remove-samples", json={"confirm": " Apps de Exemplo ", "password": PW, "backup": False}
    )
    assert r.status_code == 202, r.text
    assert wait_job(hub, r.json()["id"])["ok"]
    assert st.gateway.registry.ids() == ["novoapp"]
    assert newapp.removed_ids(tmp_path) == ["collector", "inventory", "servicedesk", "suppliers"]
    titles = [e["title"] for e in hub.get("/api/admin/audit?limit=200").json()]
    assert titles.count("Aplicação removida") == 4
    assert "data-ops-remove-samples" not in hub.get("/admin/runtime").text
    assert (
        hub.post("/api/ops/apps/remove-samples", json={"confirm": "sample apps", "password": PW}).status_code
        == 404
    )


# ---- public API and MCP -----------------------------------------------------------------------------------------
def test_api_op_is_destructive_and_a_token_needs_no_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    op = next(o for o in ADMIN_OPS if o.id == "apps_remove")
    assert op.destructive and op.method == "POST" and op.target == ("POST", "/api/ops/apps/{app}/remove")
    with api_hub(tmp_path, monkeypatch) as hub:
        st = hub.app.state  # type: ignore[attr-defined]
        st.ops_backups._psql = FakePostgres().psql
        st.ops_engine.factory = lambda: RemoveDocker([], [])
        agent = bearer(hub, make_token(hub, ["mcp", "api:write"]))
        t = tools(agent)["apps_remove"]
        assert (
            t["annotations"]["destructiveHint"] is True and "confirm_token" in t["inputSchema"]["properties"]
        )
        assert (
            hub.post("/api/v1/apps/inventory/remove", json={"confirm": "outro", "password": PW}).status_code
            == 400
        )
        job = call(
            agent, "apps_remove", {"app": "servicedesk", "body": {"confirm": "servicedesk", "backup": False}}
        )
        assert wait_job(hub, job["id"])["ok"]
        assert st.gateway.registry.get("servicedesk") is None
