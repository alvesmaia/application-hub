"""F5b: `app-hub-*` Docker guard, blue/green (approve, readiness, rollback, failure), staging, supervision,
database provisioning and clone for staging. Simulated Docker; a real test sits behind `DOCKER_AVAILABLE=1`."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import zipfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from admin_helper import make_hub
from auth_helper import SESSION
from fastapi.testclient import TestClient

from hub.ops import model as m
from hub.ops import provision as pv
from hub.ops import rollout as ro
from hub.ops import runtime as rt
from hub.ops.model import OpsError

JSON = {"content-type": "application/json"}
IMG_NEW = "sha256:" + "bb" * 32
READY: dict[str, Any] = {"code": 200, "hits": []}


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/_hub/ready"):
        READY["hits"].append(str(request.url.host))
        return httpx.Response(READY["code"])
    return httpx.Response(404)


class FC:
    def __init__(self, cl: FD, name: str, attrs: dict[str, Any], status: str = "running") -> None:
        self.cl, self.name, self.attrs, self.status = cl, name, attrs, status
        self.image = SimpleNamespace(tags=["hub-ativos:latest"])

    def stop(self, timeout: int = 10) -> None:
        self.cl.calls.append(("stop", self.name))
        self.status = "exited"

    def start(self) -> None:
        self.cl.calls.append(("start", self.name))
        self.status = "running"
        self.attrs["State"] = {"Running": True}

    def restart(self) -> None:
        self.cl.calls.append(("restart", self.name))

    def remove(self, force: bool = False) -> None:
        self.cl.calls.append(("remove", self.name))
        del self.cl.cs[self.name]

    def rename(self, new: str) -> None:
        self.cl.calls.append(("rename", self.name, new))
        del self.cl.cs[self.name]
        self.name = new
        self.cl.cs[new] = self

    def put_archive(self, path: str, data: bytes) -> None:
        pass

    def exec_run(self, cmd: list[str], **kw: Any) -> Any:
        self.cl.calls.append(("exec", self.name, cmd))
        return SimpleNamespace(exit_code=0, output=b"")

    def logs(self, **kw: Any) -> bytes:
        return b"2026-10-06T10:00:00.123456789Z boom ERROR\n"

    def stats(self, stream: bool = False) -> dict[str, Any]:
        return {"cpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 2000, "online_cpus": 2},
                "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000},
                "memory_stats": {"usage": 5 << 20, "limit": 256 << 20}, "pids_stats": {"current": 4}}  # fmt: skip


def base_attrs() -> dict[str, Any]:
    return {
        "Image": "sha256:" + "aa" * 32, "State": {"Running": True, "StartedAt": "2026-10-06T10:00:00Z"},
        "Config": {"Image": "hub-ativos", "Env": ["HUB_DB_URL=postgres://prod", "PATH=/bin", "PORT=8080"],
                   "Labels": {"com.docker.compose.service": "x", "hub.py": "1"}},
        "HostConfig": {"NetworkMode": "app-hub_default", "PortBindings": {"8080/tcp": [{"HostPort": "9101"}]},
                       "ReadonlyRootfs": True, "CapDrop": ["ALL"], "Binds": ["scr:/scratch"]},
        "NetworkSettings": {"Networks": {"app-hub_default": {}, "hub-app-ativos": {}}},
    }  # fmt: skip


class FD:
    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.cs: dict[str, FC] = {}
        self.add("app-hub-app-ativos", base_attrs())
        self.add("app-hub-app-db", {"Image": "x", "Config": {"Env": []}, "State": {"Running": True}})
        self.add("hub-web", {"Image": "x", "Config": {"Env": []}, "State": {"Running": True}})
        self.add("apphub-devtunnel", {"Image": "x", "Config": {"Env": []}, "State": {"Running": True}})
        self.images = SimpleNamespace(get=self._img)
        self.containers = SimpleNamespace(get=self._get, list=lambda **kw: list(self.cs.values()))
        self.api = SimpleNamespace(create_container=self._create, connect_container_to_network=self._connect)
        self.psql: list[str] = []

    def add(self, name: str, attrs: dict[str, Any]) -> FC:
        self.cs[name] = FC(self, name, attrs)
        return self.cs[name]

    def _get(self, name: str) -> FC:
        if name not in self.cs:
            raise KeyError(name)
        return self.cs[name]

    def _img(self, ref: str) -> Any:
        if ref in (IMG_NEW, "hub-ativos@" + IMG_NEW, "hub-ativos:latest"):
            return SimpleNamespace(id=IMG_NEW, attrs={"Config": {"Env": ["PATH=/bin"]}})
        if ref.startswith("sha256:"):
            return SimpleNamespace(id=ref, attrs={"Config": {"Env": ["PATH=/bin"]}})
        raise KeyError(ref)

    def _create(self, image: str, name: str, **kw: Any) -> None:
        self.calls.append(("create", name, image, kw))
        self.add(name, {"Image": image, "State": {"Running": True}, "Config": {"Env": kw["environment"],
                  "Labels": kw["labels"]}, "HostConfig": kw["host_config"], "NetworkSettings": {"Networks": {}}})  # fmt: skip

    def _connect(self, container: str, network: str, **kw: Any) -> None:
        self.calls.append(("connect", network, container))

    def version(self) -> dict[str, Any]:
        return {"Platform": {"Name": "Docker"}, "Version": "28"}

    def info(self) -> dict[str, Any]:
        return {"NCPU": 8, "MemTotal": 16 << 30}


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("HUB_ROLLOUT_DRAIN_S", "0")
    monkeypatch.setenv("HUB_ROLLOUT_INTERVAL_S", "0.01")
    monkeypatch.setenv("HUB_ROLLOUT_TIMEOUT_S", "0.3")
    monkeypatch.setenv("HUB_SCRIPTS_ALLOW_SELF_APPROVAL", "1")
    READY.update(code=200, hits=[])
    with make_hub(tmp_path, monkeypatch, handler) as c:
        yield c


@pytest.fixture
def fake(hub: TestClient) -> FD:
    fd = FD()
    hub.app.state.ops_engine.factory = lambda: fd  # type: ignore[attr-defined]
    return fd


def pack(app: str = "ativos", version: str = "9.0.0") -> bytes:
    man = {
        "id": app,
        "name": "Ativos",
        "version": version,
        "routes": [f"/{app}/"],
        "permissions": [f"{app}.read"],
    }
    api = {"openapi": "3.0.0", "info": {"title": "t", "version": "1"},
           "paths": {f"/{app}/api/x": {"get": {"responses": {"200": {"description": "ok"}}}}}}  # fmt: skip
    body = {"manifest.json": json.dumps(man).encode(), "openapi.json": json.dumps(api).encode(),
            "runtime.json": json.dumps({"image": f"hub-ativos@{IMG_NEW}"}).encode(), "ui/index.html": b"<h1>x</h1>"}  # fmt: skip
    sums = "".join(f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in body.items())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("checksums.txt", sums)
        for n, b in body.items():
            z.writestr(n, b)
    return buf.getvalue()


def pcall(c: TestClient, fn: Any, *a: Any) -> Any:
    assert c.portal is not None
    return c.portal.call(fn, *a)


def upload(c: TestClient, env: str = "producao", version: str = "9.0.0") -> httpx.Response:
    return cast(httpx.Response, c.post(f"/api/ops/packages?env={env}&wait=true", files={"package": ("p.hubapp", pack(version=version))},
                  headers={"x-hub-request": "1"}))  # fmt: skip


# ---- guard: only app-hub-* -------------------------------------------------------------------------------------
def test_guard_refuses_everything_outside_hubpy(hub: TestClient, fake: FD) -> None:
    eng = hub.app.state.ops_engine  # type: ignore[attr-defined]
    for bad in (
        "hub-web",
        "hub-app-ativos",
        "apphub-devtunnel",
        "maia_web",
        "app-hub",
        "app-hubx-1",
        "../app-hub-x",
        "app-hub-a:b",
    ):
        with pytest.raises(OpsError) as e:
            rt.own(bad)
        assert e.value.status == 403, bad
    for op in ("start", "stop", "restart"):
        with pytest.raises(OpsError) as e:
            asyncio.run(eng.action("hub-web", op))
        assert e.value.status in (403, 503)
    for coro in (
        eng.tail("hub-web", 5),
        eng.inspect("hub-web"),
        eng.raw("apphub-devtunnel"),
        eng.remove("hub-web"),
        eng.rename("app-hub-app-ativos", "hub-web"),
        eng.update_limits("hub-web", 1, "64Mi", 10),
        eng.launch("hub-evil", "img", {}, {}, {}, ["n"]),
        rt.exec_in(eng, "hub-web", ["id"]),
    ):
        with pytest.raises(OpsError):
            asyncio.run(coro)
    assert not [c for c in fake.calls if c[1:2] and str(c[1]).startswith(("hub-", "apphub"))]
    assert not any(c[0] in ("stop", "remove", "rename") for c in fake.calls)


def test_guard_lists_only_owned(hub: TestClient, fake: FD) -> None:
    g = rt.Guarded(fake)
    assert sorted(c.name for c in g.containers.list(all=True)) == ["app-hub-app-ativos", "app-hub-app-db"]
    with pytest.raises(OpsError):
        g.build(".", "hub-evil:1")
    with pytest.raises(OpsError):
        g.create("img", "hub-web")
    with pytest.raises(OpsError):
        g.connect("net", "apphub-devtunnel")
    assert g.containers.get("app-hub-app-ativos").name == "app-hub-app-ativos"


# ---- pure pieces ----
def test_ready_staging_and_host_helpers() -> None:
    assert ro.ready_ok(200, {}) and not ro.ready_ok(503, {}) and not ro.ready_ok(200, {"x-hub-ready": "0"})
    assert ro.ready_path("ativos") == "/ativos/_hub/ready"
    b = lambda a, e: {"app": a, "env": e}  # noqa: E731
    shared = [
        {"name": "DB", "kind": "connection_string", "bindings": [b("a", "staging"), b("a", "producao")]}
    ]
    with pytest.raises(OpsError, match="vale em produção e em staging"):
        ro.staging_env_check(shared, "a")
    prod = [
        {
            "name": "DB",
            "inject_as": "HUB_DB_URL",
            "kind": "connection_string",
            "bindings": [b("a", "producao")],
        }
    ]
    with pytest.raises(OpsError, match="Staging sem banco próprio"):
        ro.staging_env_check(prod, "a")
    ro.staging_env_check(
        [*prod, {"name": "S", "inject_as": "HUB_DB_URL", "bindings": [b("a", "staging")]}], "a"
    )
    h = ro.host_for(base_attrs()["HostConfig"], {"cpu": 1, "memory": "512Mi", "pids": 50}, staging=True)
    assert (
        "PortBindings" not in h
        and h["Memory"] == 512 << 20
        and h["NanoCpus"] == 10**9
        and h["PidsLimit"] == 50
    )
    assert h["Binds"] == ["scr__staging:/scratch"] and h["ReadonlyRootfs"] and h["CapDrop"] == ["ALL"]


# ---- blue/green -----------------------------------------------------------------------------------------------
def test_approve_runs_bluegreen_then_rollback(hub: TestClient, fake: FD) -> None:
    reg = hub.app.state.gateway.registry  # type: ignore[attr-defined]
    dep = upload(hub).json()["result"]["deployment"]
    assert dep["status"] == m.AGUARDANDO
    r = hub.post(f"/api/ops/deployments/{dep['id']}/approve?wait=true", headers=JSON)
    assert r.status_code == 200 and r.json()["status"] == m.IMPLANTADO, r.text
    kinds = [c[0] for c in fake.calls]
    # candidate created and ready BEFORE the old one leaves; only then removal and renaming
    assert kinds.index("create") < kinds.index("remove") < kinds.index("rename")
    create = next(c for c in fake.calls if c[0] == "create")
    assert create[1] == "app-hub-app-ativos__new" and create[2] == IMG_NEW
    kw = create[3]
    assert "PortBindings" not in kw["host_config"] and "HUB_DB_URL=postgres://prod" in kw["environment"]
    assert kw["labels"]["hub.version"] == "9.0.0" and kw["host_config"]["NetworkMode"] == "app-hub_default"
    assert ("connect", "hub-app-ativos", "app-hub-app-ativos__new") in fake.calls
    assert "app-hub-app-ativos__new" in READY["hits"]
    assert (
        reg.get("ativos").upstream == "http://app-hub-app-ativos:8080"
        and reg.get("ativos").version == "9.0.0"
    )
    assert (
        fake.cs["app-hub-app-ativos"].attrs["Image"] == IMG_NEW and "app-hub-app-ativos__new" not in fake.cs
    )
    deps = hub.get("/api/ops/deployments").json()
    assert [d["status"] for d in deps if d["env"] == "Produção"] == [m.IMPLANTADO, m.SUBSTITUIDO]
    assert deps[-1]["origin"] == "Compose" and deps[-1]["image"].startswith("hub-ativos@sha256:")
    # rollback: new deployment with the previous image (the original from compose)
    fake.images.get = lambda ref: SimpleNamespace(id="sha256:" + "aa" * 32, attrs={"Config": {"Env": []}})  # type: ignore[method-assign]
    cur = next(d for d in deps if d["status"] == m.IMPLANTADO)
    rb = hub.post(f"/api/ops/deployments/{cur['id']}/rollback?wait=true", headers=JSON)
    assert (
        rb.status_code == 200 and rb.json()["origin"] == "Rollback" and rb.json()["status"] == m.IMPLANTADO
    ), rb.text
    assert fake.cs["app-hub-app-ativos"].attrs["Image"] == "sha256:" + "aa" * 32
    titles = [e["title"] for e in hub.get("/api/admin/audit?limit=50").json()]
    assert "Versão implantada" in titles and "Rollback solicitado" in titles


def test_candidate_without_ready_route_is_refused_and_active_stays(hub: TestClient, fake: FD) -> None:
    READY["code"] = 404
    dep = upload(hub).json()["result"]["deployment"]
    r = hub.post(f"/api/ops/deployments/{dep['id']}/approve?wait=true", headers=JSON)
    assert r.status_code == 422 and r.json()["status"] == m.FALHOU and "prontidão" in r.json()["note"]
    assert "app-hub-app-ativos__new" not in fake.cs  # candidato removido
    assert fake.cs["app-hub-app-ativos"].attrs["Image"] == base_attrs()["Image"]  # the active one stayed
    assert ("remove", "app-hub-app-ativos") not in fake.calls
    reg = hub.app.state.gateway.registry  # type: ignore[attr-defined]
    assert reg.get("ativos").upstream == "http://upstream"  # traffic never switched
    READY["code"] = 503  # never becomes ready: deadline overrun
    dep2 = upload(hub, version="9.0.1").json()["result"]["deployment"]
    r = hub.post(f"/api/ops/deployments/{dep2['id']}/approve?wait=true", headers=JSON)
    assert r.status_code == 422 and "não ficou pronto" in r.json()["note"]


def test_missing_image_and_async_job(hub: TestClient, fake: FD) -> None:
    fake.images.get = lambda ref: (_ for _ in ()).throw(KeyError(ref))  # type: ignore[method-assign]
    dep = upload(hub).json()["result"]["deployment"]
    r = hub.post(f"/api/ops/deployments/{dep['id']}/approve?wait=true", headers=JSON)
    assert r.status_code == 422 and "Imagem não encontrada" in r.json()["note"]
    fake.images.get = FD()._img  # type: ignore[method-assign]
    dep2 = upload(hub, version="9.0.2").json()["result"]["deployment"]
    job = hub.post(f"/api/ops/deployments/{dep2['id']}/approve", headers=JSON)
    assert job.status_code == 202
    for _ in range(100):
        j = hub.get(f"/api/ops/jobs/{job.json()['id']}").json()
        if j["done"]:
            break
        asyncio.run(asyncio.sleep(0.05))
    assert j["done"] and j["ok"] and "sem indisponibilidade" in j["message"]


def test_apply_recreates_with_current_variables_and_clears_pending(hub: TestClient, fake: FD) -> None:
    hub.post("/api/admin/env", headers=JSON,
             json={"name": "FOO", "value": "bar", "kind": "publica", "bindings": [{"app": "ativos", "env": "producao"}]})  # fmt: skip
    assert "ativos" in pcall(hub, hub.app.state.admin.cfg.pending_restart)
    r = hub.post("/api/ops/runtime/ativos/apply", headers=JSON)
    assert r.status_code == 200 and "blue/green" in r.json()["detail"]
    create = next(c for c in fake.calls if c[0] == "create")
    assert "FOO=bar" in create[3]["environment"]
    assert "ativos" not in pcall(hub, hub.app.state.admin.cfg.pending_restart)


def test_staging_deploys_alongside_and_needs_own_db(hub: TestClient, fake: FD) -> None:
    hub.post("/api/admin/env", headers=JSON, json={"name": "DATABASE_URL_ATIVOS", "value": "postgres://p@x/db",
             "kind": "connection_string", "inject_as": "HUB_DB_URL",
             "bindings": [{"app": "ativos", "env": "producao"}]})  # fmt: skip
    r = upload(hub, "staging")
    assert r.status_code == 422 and "Staging sem banco próprio" in r.json()["result"]["deployment"]["note"]
    hub.post("/api/admin/env", headers=JSON, json={"name": "DATABASE_URL_ATIVOS_STAGING", "value": "postgres://s@x/dbs",
             "kind": "connection_string", "inject_as": "HUB_DB_URL",
             "bindings": [{"app": "ativos", "env": "staging"}]})  # fmt: skip
    r = upload(hub, "staging", "9.1.0")
    assert r.status_code == 201, r.text
    dep = r.json()["result"]["deployment"]
    assert dep["status"] == m.IMPLANTADO and dep["env"] == "Staging"
    stg = fake.cs["app-hub-app-ativos__staging"]
    env = stg.attrs["Config"]["Env"]
    assert "HUB_DB_URL=postgres://s@x/dbs" in env and not any(
        "postgres://prod" in e for e in env
    )  # never the production database
    assert "scr__staging:/scratch" in stg.attrs["HostConfig"]["Binds"]
    assert fake.cs["app-hub-app-ativos"].attrs["Image"] == base_attrs()["Image"]  # production intact
    reg = hub.app.state.gateway.registry  # type: ignore[attr-defined]
    assert reg.staging["ativos"] == "http://app-hub-app-ativos__staging:8080"
    rows = hub.get("/api/ops/runtime").json()["apps"]
    assert {(x["id"], x["env"]) for x in rows if x["id"] == "ativos"} == {
        ("ativos", "producao"),
        ("ativos", "staging"),
    }
    page = hub.get("/admin/runtime").text
    assert "app-hub-app-ativos__staging" in page and "hub-env?to=staging" in page
    # F19: staging disappears (removed externally) -> supervision recreates it from the last staging deployment
    hub.app.state.leader.leading = lambda: True  # type: ignore[attr-defined]
    del fake.cs["app-hub-app-ativos__staging"]
    out = pcall(hub, hub.app.state.ops_supervisor.tick)  # type: ignore[attr-defined]
    assert "app-hub-app-ativos__staging" in fake.cs and any("recriado" in x for x in out), out


def test_staging_mode_cookie_routes_gateway_to_staging(hub: TestClient, fake: FD) -> None:
    seen: list[tuple[str, str]] = []
    reg = hub.app.state.gateway.registry  # type: ignore[attr-defined]
    reg.staging["ativos"] = "http://stg:8080"
    stg = {"cookie": f"hub_session={SESSION}; hub_env=staging"}  # the test client sets the cookie header
    gw = hub.app.state.gateway  # type: ignore[attr-defined]
    orig = gw.client._transport

    def spy(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers.get("x-hub-env", "")))
        return httpx.Response(200, stream=httpx.ByteStream(b"ok"))

    gw.client._transport = httpx.MockTransport(spy)
    try:
        hub.get("/apps/ativos")
        r = hub.get("/hub-env?to=staging&next=/apps/ativos", follow_redirects=False)
        assert (
            r.status_code == 303
            and r.headers["location"] == "/apps/ativos"
            and "hub_env=staging" in r.headers["set-cookie"]
        )
        assert (
            hub.get("/hub-env?to=staging&next=//evil.com", follow_redirects=False).headers["location"] == "/"
        )
        hub.get("/_frag/ativos/ativos/x", headers=stg)
        assert seen[-1] == ("stg", "staging")
        reg.staging.pop("ativos")
        assert hub.get("/_frag/ativos/ativos/x", headers=stg).status_code == 404
        hub.get("/hub-env?to=producao", follow_redirects=False)
        print("R", hub.get("/_frag/ativos/ativos/x").text[:300], flush=True)
        assert seen[-1][1] == "producao"
    finally:
        gw.client._transport = orig


# ---- supervision ----
def test_supervisor_revives_crashed_but_not_stopped(hub: TestClient, fake: FD) -> None:
    sup = hub.app.state.ops_supervisor  # type: ignore[attr-defined]
    hub.app.state.leader.leading = lambda: True  # type: ignore[attr-defined]
    fake.cs["app-hub-app-ativos"].status = "exited"
    out = pcall(hub, sup.tick)
    assert out and ("start", "app-hub-app-ativos") in fake.calls and sup.status("ativos")["restarts"] == 1
    assert "Container religado" in [e["title"] for e in hub.get("/api/admin/audit?limit=20").json()]
    # stopped by the operator: does not restart
    fake.calls.clear()
    assert hub.post("/api/ops/runtime/ativos/stop", headers=JSON).status_code == 200
    fake.cs["app-hub-app-ativos"].status = "exited"
    pcall(hub, sup.tick)
    assert ("start", "app-hub-app-ativos") not in fake.calls
    assert hub.post("/api/ops/runtime/ativos/start", headers=JSON).status_code == 200
    assert "ativos|producao" not in sup.stopped()
    # back-off: right after, it does not repeat; at the limit it becomes a crash loop (alert) and stops
    sup.state["ativos|producao"].update(next=0, times=[__import__("time").time()] * 5)
    fake.cs["app-hub-app-ativos"].status = "exited"
    fake.calls.clear()
    pcall(hub, sup.tick)
    assert not fake.calls and sup.status("ativos")["loop"] is True


def test_supervisor_feeds_metrics_logs_and_never_touches_foreign(hub: TestClient, fake: FD) -> None:
    sup = hub.app.state.ops_supervisor  # type: ignore[attr-defined]
    pcall(hub, sup.tick)
    pts = hub.app.state.insight.tel.metrics.points("ativos", "producao", 5)  # type: ignore[attr-defined]
    assert pts and pts[-1]["cpu"] == 0.2 and pts[-1]["mem"] == 5 << 20
    lines = hub.get("/api/admin/logs?minutes=9999999&source=container").json()
    assert any("boom" in x["message"] and x["source"] == "container" for x in lines)
    assert not any(c[1] in ("hub-web", "apphub-devtunnel") for c in fake.calls if len(c) > 1)


# ---- provisionar / rotacionar banco ----------------------------------------------------------------------------
class PsqlFake:
    """Answers the Postgres container's `psql`: role/database state and a record of the commands."""

    def __init__(self, role: str | None = None, owner: str | None = None, dbc: str = "") -> None:
        self.role, self.owner, self.dbc, self.sql, self.works = role, owner, dbc, [], True

    async def psql(self, db: str, *stmts: str) -> str:
        self.sql += list(stmts)
        if stmts and stmts[0].startswith("SELECT 'role='"):
            return (
                f"role={'!' if self.role is None else self.role}\nowner={self.owner or '!'}\ndbc={self.dbc}\n"
            )
        return ""


def test_provision_database_creates_marked_role_and_db(
    hub: TestClient, fake: FD, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = hub.app.state.ops_backups  # type: ignore[attr-defined]
    ps = PsqlFake()
    b._psql = ps.psql
    r = hub.post("/api/ops/provision/ativos/database", headers=JSON)
    assert r.status_code == 200, r.text
    p = r.json()["provisioned"]
    assert p == {"variable": "DATABASE_URL_ATIVOS", "database": "app_ativos", "changed": True,
                 "created_role": True, "created_database": True}  # fmt: skip
    sql = "\n".join(ps.sql)
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in sql and "hub-managed:app=ativos" in sql
    assert 'CREATE DATABASE "app_ativos" OWNER "app_ativos"' in sql and "REVOKE ALL ON DATABASE" in sql
    cfg = pcall(hub, hub.app.state.admin.cfg.read)
    v = next(x for x in cfg["env"] if x["name"] == "DATABASE_URL_ATIVOS")
    assert (
        v["managed"] and v["inject_as"] == "HUB_DB_URL" and v["value"].startswith("postgresql://app_ativos:")
    )
    assert "@app-hub-app-db:5432/app_ativos" in v["value"]
    listing = hub.get("/api/admin/env").text
    assert v["value"].split(":")[2].split("@")[0] not in listing  # password never in the API
    assert "provisionamento: banco app_ativos" in json.dumps(hub.get("/api/admin/logs?minutes=60").json())
    # staging: its own database/role/variable
    r = hub.post("/api/ops/provision/ativos/database?env=staging", headers=JSON)
    assert r.json()["provisioned"]["database"] == "app_ativos_staging"
    assert r.json()["provisioned"]["variable"] == "DATABASE_URL_ATIVOS_STAGING"
    # foreign role/database (without the hub mark) is refused
    b._psql = PsqlFake(role="outra-coisa").psql
    assert hub.post("/api/ops/provision/coletor/database", headers=JSON).status_code == 503
    b._psql = PsqlFake(role="", owner="postgres", dbc="").psql
    assert hub.post("/api/ops/provision/servicos/database", headers=JSON).status_code == 503
    assert hub.post("/api/ops/provision/zzz/database", headers=JSON).status_code == 404


def test_provision_is_idempotent_and_rotate_changes_password(hub: TestClient, fake: FD) -> None:
    b = hub.app.state.ops_backups  # type: ignore[attr-defined]
    ps = PsqlFake()
    b._psql = ps.psql
    hub.post("/api/ops/provision/ativos/database", headers=JSON)
    first = next(
        x for x in pcall(hub, hub.app.state.admin.cfg.read)["env"] if x["name"] == "DATABASE_URL_ATIVOS"
    )["value"]
    ps.role, ps.owner, ps.dbc = "hub-managed:app=ativos", "app_ativos", "hub-managed:app=ativos"
    ps.sql.clear()
    r = hub.post("/api/ops/provision/ativos/database", headers=JSON)  # already works (fake psql rc 0)
    assert r.json()["provisioned"]["changed"] is False and not ps.sql
    r = hub.post("/api/ops/rotate/ativos/database", headers=JSON)
    assert (
        r.status_code == 200
        and r.json()["variable"] == "DATABASE_URL_ATIVOS"
        and r.json()["restart_pending"] is True
    )
    second = next(
        x for x in pcall(hub, hub.app.state.admin.cfg.read)["env"] if x["name"] == "DATABASE_URL_ATIVOS"
    )["value"]
    assert second != first and "ALTER ROLE" in "\n".join(ps.sql)
    assert "Senha do banco rotacionada" in [e["title"] for e in hub.get("/api/admin/audit?limit=20").json()]
    # without provisioning: guidance instead of an obscure error
    assert hub.post("/api/ops/rotate/coletor/database", headers=JSON).status_code == 409


def test_db_names_and_markers() -> None:
    assert (
        pv.db_name("meu-app", "producao") == "app_meu_app" and pv.db_name("a", "staging") == "app_a_staging"
    )
    assert pv.variable("meu-app", "staging") == "DATABASE_URL_MEU_APP_STAGING"
    assert pv.marker("a", "staging") == "hub-managed:app=a:env=staging"
    with pytest.raises(OpsError):
        pv.db_name('x";drop', "producao")


# ---- clone p/ staging -----------------------------------------------------------------------------------------------
def test_clone_backup_into_staging_database_only(hub: TestClient, fake: FD) -> None:
    b = hub.app.state.ops_backups  # type: ignore[attr-defined]
    files = {"db/appdb.sql": b"CREATE TABLE t();", "files/data/a.txt": b"hi"}
    b.read_bundle = lambda app, env, name, sha: ({}, files)
    envs: dict[str, dict[str, str]] = {
        "producao": {"HUB_DB_URL": "postgres://u:p@db:5432/appdb"},
        "staging": {},
    }

    async def app_env(app: str, e: str) -> dict[str, str]:
        return envs[e]

    b.app_env = app_env
    aside: list[tuple[str, bytes]] = []

    async def fake_aside(db: str, sql: bytes) -> str:
        aside.append((db, sql))
        return f"banco {db} restaurado"

    b._restore_db_aside = fake_aside

    psqls: list[str] = []

    async def psql(db: str, *stmts: str) -> str:
        psqls.extend(stmts)
        return "app_ativos_staging\n"

    b._psql = psql
    ops = hub.app.state.ops  # type: ignore[attr-defined]
    d = ops.read()
    d["backups"].insert(0, {"id": 1, "app": "ativos", "kind": "Manual", "size": "1 KB", "status": "Concluído", "at": 1,
                            "file": "x.zip", "sha256": "", "env": "producao"})  # fmt: skip
    d["next_backup_id"] = 2
    ops.write(d)

    def clone() -> dict[str, Any]:
        r = hub.post("/api/ops/backups/records/1/clone", headers=JSON)
        assert r.status_code == 202, r.text
        for _ in range(100):
            j = hub.get(f"/api/ops/jobs/{r.json()['id']}").json()
            if j["done"]:
                return dict(j)
            asyncio.run(asyncio.sleep(0.05))
        raise AssertionError("job não terminou")

    j = clone()
    assert (
        not j["ok"] and "Staging sem banco próprio" in j["message"]
    )  # no staging database provisioned: refused
    envs["staging"] = {"HUB_DB_URL": "postgres://s:p@db:5432/app_ativos_staging"}
    j = clone()
    assert j["ok"], j
    assert aside == [("app_ativos_staging", b"CREATE TABLE t();")]
    assert (
        Path(hub.app.state.data_dir) / "apps" / "ativos__staging" / "data" / "a.txt"
    ).read_bytes() == b"hi"  # type: ignore[attr-defined]
    envs["staging"] = {
        "HUB_DB_URL": "postgres://u:p@db:5432/appdb"
    }  # staging pointing at the production database
    assert "não pode ser o de produção" in clone()["message"]
    assert len(aside) == 1


# ---- docker real (opcional) -----------------------------------------------------------------------------------
@pytest.mark.docker
@pytest.mark.skipif(os.getenv("DOCKER_AVAILABLE") != "1", reason="sem Docker")
def test_live_guard_blocks_foreign_containers() -> None:
    eng = rt.Engine()

    async def go() -> None:
        info = await eng.probe()
        assert info["reachable"] and all(x["name"].startswith("app-hub-") for x in info["live"])
        with pytest.raises(OpsError):
            await eng.action("hub-web", "stop")

    asyncio.run(go())


def test_restore_gives_objects_back_to_the_app_role(hub: TestClient, fake: FD) -> None:
    """Restore of a provisioned database (owner = app role): the dump comes in as administrator and is handed back to the owner."""
    b = hub.app.state.ops_backups  # type: ignore[attr-defined]
    stmts: list[str] = []
    loaded: list[bytes] = []

    async def psql(db: str, *s: str) -> str:
        stmts.extend(s)
        return "hub:aside=true\nhub:owner=app_contratos\n"

    async def load(db: str, sql: bytes, single: bool) -> tuple[int, bytes]:
        loaded.append(sql)
        return 0, b""

    b._psql, b._load = psql, load
    msg = asyncio.run(b._restore_db_aside("app_contratos", b"CREATE TABLE t();"))
    assert "app_contratos" in msg and loaded == [b"CREATE TABLE t();"]
    chown = next(q for q in stmts if "OWNER TO" in q)
    assert "'app_contratos'" in chown and "pg_tables" in chown
    stmts.clear()

    async def psql_admin(db: str, *s: str) -> str:
        stmts.extend(s)
        return "hub:aside=true\nhub:owner=hub\n"

    b._psql = psql_admin  # owner = administrator: nothing to hand back
    asyncio.run(b._restore_db_aside("hub", b"x"))
    assert not any("OWNER TO" in q for q in stmts)


def test_rollback_goes_to_previous_deployment_not_to_compose_baseline(hub: TestClient, fake: FD) -> None:
    """F7a: the compose version is the OLDEST (id lower than the 1st deployment); the rollback of the 2nd returns to the 1st."""
    ids: list[int] = []
    for v in ("9.0.0", "9.1.0"):
        dep = upload(hub, version=v).json()["result"]["deployment"]
        r = hub.post(f"/api/ops/deployments/{dep['id']}/approve?wait=true", headers=JSON)
        assert r.status_code == 200 and r.json()["status"] == m.IMPLANTADO, r.text
        ids.append(dep["id"])
    deps = {d["id"]: d for d in hub.get("/api/ops/deployments").json()}
    baseline = next(d for d in deps.values() if d["origin"] == "Compose")
    assert baseline["id"] < min(ids)
    fake.images.get = lambda ref: SimpleNamespace(id="sha256:" + "cc" * 32, attrs={"Config": {"Env": []}})  # type: ignore[method-assign]
    rb = hub.post(f"/api/ops/deployments/{ids[1]}/rollback?wait=true", headers=JSON)
    assert rb.status_code == 200 and rb.json()["version"] == "9.0.0", rb.text
