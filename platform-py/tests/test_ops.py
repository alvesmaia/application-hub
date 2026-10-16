"""F3c: deployments (pipeline, scripts), containers (simulated Docker), tasks and backups; RBAC and audit of writes."""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from admin_helper import HDR, add_user, make_hub
from fastapi.testclient import TestClient

from hub.ops import model as m
from hub.ops import pipeline as pl
from hub.ops import runtime as rt
from hub.ops.backups import backup_role_sql, bucket_policy, parse_targets, redact
from hub.ops.jobs import can_cancel, can_retry, label, percent, saga_summary
from hub.ops.model import OpsError
from hub.ops.schedule import normalize

JSON = {"content-type": "application/json"}
DIGEST = "sha256:" + "ab" * 32
JOBS = [
    {"id": "job-1", "kind": "ativos.import", "status": "failed", "attempts": 2, "max_attempts": 3,
     "progress_done": 5, "progress_total": 10, "error": "boom", "user": "Ana", "created_at": 1_700_000_000_000,
     "elapsed_s": 3.2, "checkpoint": {"saga": {"state": "compensating", "steps": [{"name": "a", "state": "done"}]}}},
    {"id": "job-2", "kind": "ativos.export", "status": "running", "attempts": 1, "max_attempts": 3,
     "created_at": 1_700_000_100_000},
]  # fmt: skip


def upstream(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    if p == "/ativos/_hub/jobs" and request.headers.get("x-hub-internal-token"):
        return httpx.Response(200, json={"jobs": JOBS})
    if p.startswith("/ativos/_hub/jobs/job-1/"):
        return httpx.Response(200, json={"job": {**JOBS[0], "status": "queued"}})
    return httpx.Response(404)


def package(files: dict[str, bytes] | None = None, *, scripts: bool = False, secret: bool = False) -> bytes:
    man: dict[str, Any] = {"id": "novoapp", "name": "Novo App", "version": "1.2.3", "routes": ["/novoapp/*"],
                           "permissions": ["novoapp.read"]}  # fmt: skip
    man["routes"] = ["/novoapp/"]
    if scripts:
        man["scripts"] = [{"path": "ui/assets/x.js", "reason": "gráfico interativo da tela inicial"}]
    api = {"openapi": "3.0.0", "info": {"title": "t", "version": "1"},
           "paths": {"/novoapp/api/items": {"get": {"responses": {"200": {"description": "ok"}}}}}}  # fmt: skip
    body: dict[str, bytes] = {
        "manifest.json": json.dumps(man).encode(),
        "runtime.json": json.dumps({"image": f"ghcr.io/org/novo@{DIGEST}"}).encode(),
        "openapi.json": json.dumps(api).encode(),
        "ui/index.html": b"<h1>ola</h1>",
    }
    if scripts:
        body["ui/assets/x.js"] = b"console.log(1)"
    if secret:
        body["ui/leak.html"] = b"<p>AKIAABCDEFGHIJKLMNOP</p>"
    body.update(files or {})
    sums = "".join(f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in body.items())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("checksums.txt", sums)
        for n, b in body.items():
            z.writestr(n, b)
    return buf.getvalue()


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("HUB_SCRIPTS_ALLOW_SELF_APPROVAL", raising=False)
    with make_hub(
        tmp_path, monkeypatch, upstream
    ) as c:  # single portal: background tasks survive between requests
        yield c


def upload(c: TestClient, data: bytes, env: str = "producao") -> httpx.Response:
    return cast(httpx.Response, c.post(f"/api/ops/packages?env={env}&wait=true", files={"package": ("p.hubapp", data)},
                  headers={"x-hub-request": "1"}))  # fmt: skip


def audit_titles(c: TestClient) -> list[str]:
    return [e["title"] for e in c.get("/api/admin/audit?limit=100").json()]


# ---- Docker fakes ----
class FakeContainer:
    def __init__(self, cl: FakeDocker, name: str, status: str = "running") -> None:
        self.cl, self.name, self.status = cl, name, status
        self.image = SimpleNamespace(tags=["hub-ativos:latest"])
        self.attrs: dict[str, Any] = {
            "State": {"StartedAt": "2026-10-06T10:00:00.5Z", "Health": {"Status": "healthy"}},
            "Config": {"Env": ["SECRET=abc", "A=b"]},
            "HostConfig": {"NetworkMode": "hub-app-ativos", "ReadonlyRootfs": True, "CapDrop": ["ALL"]},
            "NetworkSettings": {"Networks": {"hub-app-ativos": {}}},
        }

    def stats(self, stream: bool = False) -> dict[str, Any]:
        return {"cpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 2000, "online_cpus": 2},
                "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000},
                "memory_stats": {"usage": 5 << 20, "limit": 256 << 20}, "pids_stats": {"current": 4}}  # fmt: skip

    def logs(self, tail: int = 100, timestamps: bool = False) -> bytes:
        return b"INFO ok\nERROR falha\n"

    def update(self, **kw: Any) -> None:
        self.cl.calls.append(("update", self.name, kw))

    def start(self) -> None:
        self.cl.calls.append(("start", self.name))

    def stop(self) -> None:
        self.cl.calls.append(("stop", self.name))

    def restart(self) -> None:
        self.cl.calls.append(("restart", self.name))

    def put_archive(self, path: str, data: bytes) -> None:
        self.cl.calls.append(("put", self.name))

    def exec_run(self, cmd: list[str], **kw: Any) -> Any:
        self.cl.calls.append(("exec", self.name, cmd, kw.get("environment")))
        out = b"-- dump --\nCREATE TABLE t();\n" if cmd[0] == "pg_dump" else b""
        code = self.cl.fail.get(cmd[0], 0)
        return SimpleNamespace(exit_code=code, output=b"ERRO sql" if code else out)


class FakeDocker:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.fail: dict[str, int] = {}
        self.names = ["app-hub-app-ativos", "app-hub-app-db"]
        self.containers = SimpleNamespace(list=self._list, get=self._get)

    def _list(self, all: bool = True, filters: Any = None) -> list[FakeContainer]:
        return [FakeContainer(self, n) for n in self.names]

    def _get(self, name: str) -> FakeContainer:
        if name not in self.names:
            raise KeyError(name)
        return FakeContainer(self, name)

    def version(self) -> dict[str, Any]:
        return {"Platform": {"Name": "Docker Desktop"}, "Version": "28.5"}

    def info(self) -> dict[str, Any]:
        return {"NCPU": 8, "MemTotal": 16 << 30}


@pytest.fixture
def docker(hub: TestClient) -> FakeDocker:
    fake = FakeDocker()
    hub.app.state.ops_engine.factory = lambda: fake  # type: ignore[attr-defined]
    return fake


# ---- RBAC, pages and API ----
def test_pages_render_and_rbac(hub: TestClient, docker: FakeDocker) -> None:
    for p, title in (
        ("deploys", "Implantações"),
        ("runtime", "Containers"),
        ("tasks", "Tarefas"),
        ("backups", "Backups"),
    ):
        r = hub.get(f"/admin/{p}")
        assert r.status_code == 200 and f"{title} · " in r.text, p
    colab = add_user(hub, "colab", ["Colaborador"])
    for p in ("deploys", "runtime", "tasks", "backups"):
        assert colab.get(f"/admin/{p}").status_code == 403, p
    for method, url in (("get", "/api/ops/deployments"), ("get", "/api/ops/runtime"), ("get", "/api/ops/tasks"),
                        ("get", "/api/ops/backups"), ("post", "/api/ops/backups/ativos/run"),
                        ("post", "/api/ops/runtime/ativos/restart"), ("put", "/api/ops/runtime/ativos/limits")):  # fmt: skip
        assert getattr(colab, method)(url, headers=JSON).status_code == 403, url
    assert hub.get("/api/ops/deployments").headers["cache-control"] == "no-store"
    assert hub.post("/api/ops/backups/ativos/run", content=b"x").status_code == 415  # only application/json


def test_upload_pipeline_and_audit(hub: TestClient) -> None:
    r = upload(hub, package())
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["ok"] and [s["state"] for s in job["steps"]][:5] == ["ok", "ok", "warn", "ok", "ok"]
    dep = job["result"]["deployment"]
    assert (dep["app"], dep["status"], dep["env"], dep["submitted_by"]) == (
        "novoapp",
        m.AGUARDANDO,
        "Produção",
        "Administrador",
    )
    assert (Path(hub.app.state.data_dir) / "packages" / dep["package"]).is_file()  # type: ignore[attr-defined]
    assert "Pacote validado, aguardando aprovação" in audit_titles(hub)
    assert hub.get(f"/api/ops/jobs/{job['id']}").json()["done"] is True
    assert hub.get("/api/ops/jobs/9999").status_code == 404
    page = hub.get("/admin/deploys").text
    assert "novoapp" in page and 'data-ops-approve="' in page
    assert (
        page.count("data-detail-row=") == 1 and "data-ops-ver" in page
    )  # one row per app + version selector
    # staging is deployed right away; the app "novoapp" is not in the registry: the deployment fails with a clear reason
    st = upload(hub, package(), "staging").json()
    assert not st["ok"] and st["result"]["deployment"]["env"] == "Staging"
    assert st["result"]["deployment"]["status"] == m.FALHOU and "não encontrada" in st["message"]


def test_upload_rejections(hub: TestClient) -> None:
    r = upload(hub, package(secret=True))
    assert r.status_code == 422 and "AWS" in r.json()["message"]
    assert r.json()["result"]["deployment"]["status"] == m.REJEITADO
    assert "Pacote rejeitado pelo pipeline de validação" in audit_titles(hub)
    assert upload(hub, b"nao-e-zip").status_code == 422
    h = {"x-hub-request": "1"}
    assert hub.post("/api/ops/packages?env=producao", content=b"x", headers=h).status_code == 415
    assert hub.post("/api/ops/packages?env=producao", files={"package": ("p", b"x")}).status_code == 400
    assert hub.post("/api/ops/packages?env=zzz", files={"package": ("p", b"x")}, headers=h).status_code == 400
    assert (
        hub.post(
            "/api/ops/packages?env=staging&commit=zz", files={"package": ("p", b"x")}, headers=h
        ).status_code
        == 400
    )
    assert (
        hub.post("/api/ops/packages?env=staging", files={"other": ("p", b"x")}, headers=h).status_code == 400
    )
    assert (
        hub.post("/api/ops/packages?env=staging", files={"package": ("p", b"")}, headers=h).status_code == 400
    )


def test_upload_async_job(hub: TestClient) -> None:
    r = hub.post("/api/ops/packages?env=producao&origin=ci&commit=ABCDEF1234567", files={"package": ("p.hubapp", package())},
                 headers={"x-hub-request": "1"})  # fmt: skip
    assert r.status_code == 202
    jid = r.json()["id"]
    for _ in range(100):
        j = hub.get(f"/api/ops/jobs/{jid}").json()
        if j["done"]:
            break
        time.sleep(0.05)
    assert j["ok"] and j["result"]["deployment"]["origin"] == "GitHub Actions"
    assert j["result"]["deployment"]["commit"] == "abcdef123456"


def test_scripts_approval_flow(hub: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    dep = upload(hub, package(scripts=True)).json()["result"]["deployment"]
    assert dep["scripts"][0]["sha384"].startswith("sha384-")
    assert "REVISE" in upload(hub, package(scripts=True)).json()["steps"][4]["detail"]
    assert "Revisar: o app traz JavaScript próprio" in hub.get("/admin/deploys").text
    # approving the deployment before the security review: refused (400)
    assert hub.post(f"/api/ops/deployments/{dep['id']}/approve", headers=JSON).status_code == 400
    # whoever submitted does not approve their own JavaScript
    r = hub.post(f"/api/ops/deployments/{dep['id']}/scripts/approve", headers=JSON)
    assert r.status_code == 400 and "outra pessoa" in r.json()["error"]
    assert "Aprovação de JavaScript próprio recusada" in audit_titles(hub)
    # outra pessoa aprova
    other = add_user(hub, "revisor", ["Administrador"])
    ok = other.post(f"/api/ops/deployments/{dep['id']}/scripts/approve", headers=JSON)
    assert ok.status_code == 200 and ok.json()["approved"][0]["path"] == "ui/assets/x.js"
    assert "JavaScript próprio aprovado (revisão de segurança)" in audit_titles(hub)
    assert (
        other.post(f"/api/ops/deployments/{dep['id']}/scripts/approve", headers=JSON).status_code == 400
    )  # nothing pending
    # self-approval only with the explicit configuration
    d2 = upload(hub, package(scripts=True, files={"ui/assets/x.js": b"console.log(2)"})).json()["result"][
        "deployment"
    ]
    monkeypatch.setenv("HUB_SCRIPTS_ALLOW_SELF_APPROVAL", "1")
    assert hub.post(f"/api/ops/deployments/{d2['id']}/scripts/approve", headers=JSON).status_code == 200


def test_deployment_actions(hub: TestClient) -> None:
    dep = upload(hub, package()).json()["result"]["deployment"]
    assert hub.post("/api/ops/deployments/999/approve", headers=JSON).status_code == 404
    assert hub.post(f"/api/ops/deployments/{dep['id']}/zzz", headers=JSON).status_code == 404
    # approve: the transition applies; the app outside the registry fails in blue/green with a clear reason
    r = hub.post(f"/api/ops/deployments/{dep['id']}/approve?wait=true", headers=JSON)
    assert r.status_code == 422 and r.json()["status"] == m.FALHOU and "não encontrada" in r.json()["note"]
    assert hub.post(f"/api/ops/deployments/{dep['id']}/rollback", headers=JSON).status_code == 400
    assert hub.post(f"/api/ops/deployments/{dep['id']}/promote", headers=JSON).status_code == 400
    # promoting an active version in staging creates the production record awaiting approval
    ops = hub.app.state.ops  # type: ignore[attr-defined]
    d = ops.read()
    stg = {**dep, "id": d["next_deploy_id"], "env": "Staging", "status": m.IMPLANTADO, "version": "9.9.9"}
    d["deployments"].insert(0, stg)
    d["next_deploy_id"] += 1
    ops.write(d)
    r = hub.post(f"/api/ops/deployments/{stg['id']}/promote?wait=true", headers=JSON)
    assert r.status_code == 200 and r.json()["status"] == m.AGUARDANDO and r.json()["origin"] == "Promoção"
    assert "Versão promovida para produção" in audit_titles(hub)
    assert (
        hub.post(f"/api/ops/deployments/{stg['id']}/promote", headers=JSON).status_code == 400
    )  # already waiting
    assert hub.post(f"/api/ops/deployments/{stg['id']}/promote", headers=JSON).status_code == 400


def test_model_transitions() -> None:
    now = 1000
    d: m.Data = {"deployments": [], "next_deploy_id": 1, "script_approvals": []}
    a = m.push(d, {"app": "x", "version": "1", "env": m.PRODUCAO, "status": m.SUBSTITUIDO, "image": "i"})
    b = m.push(
        d,
        {"app": "x", "version": "2", "env": m.PRODUCAO, "status": m.IMPLANTADO, "image": "j", "origin": "o"},
    )
    rb = m.rollback(d, b["id"], now)
    assert (rb["version"], rb["status"], rb["origin"]) == ("1", m.IMPLANTANDO, "Rollback") and a["id"] != rb[
        "id"
    ]
    with pytest.raises(OpsError, match="em andamento"):
        m.rollback(d, b["id"], now)
    # rollback to the chosen version (any production replaced), not just the previous one
    d2: m.Data = {"deployments": [], "next_deploy_id": 1, "script_approvals": []}
    old = m.push(d2, {"app": "x", "version": "1", "env": m.PRODUCAO, "status": m.SUBSTITUIDO, "image": "i"})
    m.push(d2, {"app": "x", "version": "2", "env": m.PRODUCAO, "status": m.SUBSTITUIDO, "image": "j"})
    act = m.push(d2, {"app": "x", "version": "3", "env": m.PRODUCAO, "status": m.IMPLANTADO, "image": "k"})
    pick = m.rollback(d2, old["id"], now)
    assert pick["version"] == "1" and pick["status"] == m.IMPLANTANDO and "de 3 para 1" in pick["note"]
    with pytest.raises(OpsError, match="em andamento"):
        m.rollback(d2, act["id"], now)
    with pytest.raises(OpsError, match="aguardando"):
        m.approve(d, b["id"])
    assert m.actor_person("Ana (token:ci)") == "Ana" and m.actor_person("Ana") == "Ana"
    assert m.human(2048) == "2,0 KB" and m.human(5) == "5 B"
    assert m.active_scripts(d, "x", m.PRODUCAO) == []
    with pytest.raises(OpsError):
        m.approve_scripts(d, b["id"], "n", "", False, now, True)  # no user id
    with pytest.raises(OpsError, match="token"):
        m.approve_scripts(d, b["id"], "n", "u", True, now, True)


def test_pipeline_units() -> None:
    assert pl.peek_app_id(package()) == "novoapp" and pl.peek_app_id(b"x") is None
    for bad in ("../x", "/abs", "a:b"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(bad, b"x")
        with pytest.raises(ValueError, match="inseguro"):
            pl.read_zip(buf.getvalue())
    assert pl._inline_script("<script>x</script>") and not pl._inline_script('<script src="a.js"></script>')
    assert pl._inline_handler('<a onclick="x">') and not pl._inline_handler("<p>onde = 1</p>")
    assert pl._is_secret_file("a/.env") and pl._is_secret_file("k.pem") and not pl._is_secret_file("a.txt")
    assert pl.sri_sha384(b"x").startswith("sha384-")
    with pytest.raises(ValueError, match="openapi"):
        pl._openapi("a", b'{"openapi":"2.0"}')
    with pytest.raises(ValueError, match="fora da API"):
        pl._openapi("a", json.dumps({"openapi": "3.0.0", "info": {"title": "t", "version": "1"},
                                     "paths": {"/b/x": {"get": {"responses": {"200": {}}}}}}).encode())  # fmt: skip


@pytest.mark.parametrize(
    ("files", "needle"),
    [
        ({"ui/a.js": b"x"}, "JavaScript não declarado"),
        ({"ui/p.html": b"<script>x</script>"}, "script inline"),
        ({"ui/p.html": b'<img onerror="x">'}, "evento inline"),
        ({"evil.txt": b"x"}, "fora de ui/"),
        ({"runtime.json": b'{"image":"x:latest"}'}, "digest imutável"),
        ({"manifest.json": b"{"}, "inválido"),
        ({"ui/.env": b"A=1"}, "segredo"),
    ],
)
async def test_pipeline_failures(files: dict[str, bytes], needle: str) -> None:
    rep = await pl.run_all(package(files), "producao", [])
    assert not rep["passed"] and needle in rep["reason"], rep["reason"]
    assert rep["steps"][-1]["state"] == "skipped"


# ---- Containers -----------------------------------------------------------------------------------
def test_runtime_api_and_page(hub: TestClient, docker: FakeDocker) -> None:
    j = hub.get("/api/ops/runtime").json()
    assert j["engine"]["reachable"] and j["host"]["cpu"] == 8
    app = next(a for a in j["apps"] if a["id"] == "ativos")
    assert app["container"] == "app-hub-app-ativos" and app["image"] == "sem imagem implantada"
    live = next(x for x in j["engine"]["live"] if x["name"] == "app-hub-app-ativos")
    assert (
        live["status"].startswith("Up ")
        and "(healthy)" in live["status"]
        and live["cpu_used"] == pytest.approx(0.2)
    )
    page = hub.get("/admin/runtime").text
    assert "Em execução" in page and "Docker Desktop 28.5" in page and "Arquivos somente leitura" in page
    assert 'data-ops-container="restart"' in page and "cap-drop ALL" in page
    assert (
        "runtime=podman" in page
        and "/run/podman/podman.sock" in hub.get("/admin/runtime?runtime=podman").text
    )


def test_runtime_unreachable(hub: TestClient) -> None:
    def boom() -> Any:
        raise OSError("sem docker")

    hub.app.state.ops_engine.factory = boom  # type: ignore[attr-defined]
    assert hub.get("/api/ops/runtime").json()["engine"]["reachable"] is False
    page = hub.get("/admin/runtime").text
    assert "Runtime indisponível" in page and "Não criado" not in page and "Sem conexão" in page
    assert hub.post("/api/ops/runtime/ativos/restart", headers=JSON).status_code == 503
    assert hub.get("/api/ops/runtime/ativos/logs").status_code == 503


def test_runtime_actions_logs_inspect_limits(
    hub: TestClient, docker: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = hub.post("/api/ops/runtime/ativos/restart", headers=JSON)
    assert (
        r.json() == {"container": "app-hub-app-ativos", "action": "restart"}
        and ("restart", "app-hub-app-ativos") in docker.calls
    )
    assert "Container reiniciado" in audit_titles(hub)
    assert hub.post("/api/ops/runtime/ativos/zzz", headers=JSON).status_code == 404
    assert hub.post("/api/ops/runtime/nao/stop", headers=JSON).status_code == 404
    assert (
        hub.post("/api/ops/runtime/ativos/apply", headers=JSON).status_code == 422
    )  # no local image (simulated Docker)
    assert hub.post("/api/ops/runtime/coletor/stop", headers=JSON).status_code == 503  # container inexistente
    lg = hub.get("/api/ops/runtime/ativos/logs?tail=10").json()
    assert [(i["level"], i["text"]) for i in lg["items"]] == [("INFO", "INFO ok"), ("ERROR", "ERROR falha")]
    env = hub.get("/api/ops/runtime/ativos/inspect").json()["Config"]["Env"]
    assert env == ["SECRET=••••", "A=••••"]
    assert hub.get("/api/ops/runtime/nao/logs/stream").status_code == 404
    # limits: validation, admission control, application to the live container and audit
    ok = {"cpu": 0.5, "memory": "512Mi", "pids": 200}
    r = hub.put("/api/ops/runtime/ativos/limits", json=ok)
    assert r.status_code == 200 and "aplicado ao container app-hub-app-ativos" in r.json()["applied"]
    assert any(c[0] == "update" and c[2]["pids_limit"] == 200 for c in docker.calls)
    assert "Limites de container alterados" in audit_titles(hub)
    for bad in ({"cpu": 9, "memory": "1Gi", "pids": 1}, {"cpu": 1, "memory": "1GB", "pids": 1}, {"cpu": 1}):
        assert hub.put("/api/ops/runtime/ativos/limits", json=bad).status_code == 400
    monkeypatch.setenv("HUB_HOST_CPU", "4")
    assert (
        hub.put("/api/ops/runtime/ativos/limits", json={"cpu": 4, "memory": "8Gi", "pids": 10}).status_code
        == 400
    )  # capacidade
    assert hub.get("/api/ops/runtime").json()["apps"][0]["edited"] is True


def test_runtime_units(monkeypatch: pytest.MonkeyPatch) -> None:
    assert rt.uptime_of("Up 3 hours (healthy)") == "3 h" and rt.uptime_of("Exited") == "—"
    assert rt.human_duration(0) == "Less than a second" and rt.human_duration(7200) == "2 hours"
    assert rt.human_duration(86400 * 3) == "3 days" and rt.human_duration(86400 * 90) == "3 months"
    assert rt.container_name("a", "staging") == "app-hub-app-a__staging"
    monkeypatch.setenv("HUB_CONTAINER_PREFIX", "x-")
    assert rt.container_name("a", "producao") == "x-a"
    assert rt.level_of("stderr", "ok") == "ERROR" and rt.level_of("stdout", "warning") == "WARN"


# ---- Tarefas ---------------------------------------------------------------------------------------
def test_tasks_list_page_and_actions(hub: TestClient) -> None:
    hub.app.state.admin.tokens["ativos"] = "t" * 20  # type: ignore[attr-defined]
    j = hub.get("/api/ops/tasks").json()
    assert [t["id"] for t in j["tasks"]] == ["job-2", "job-1"]  # mais recentes primeiro
    assert j["tasks"][1]["saga"]["state"] == "compensating" and "checkpoint" not in j["tasks"][1]
    assert any(e[0] == "coletor" for e in j["errors"])  # app mudo (404)
    page = hub.get("/admin/tasks").text
    assert "2 tarefas · 1 em execução · 1 com falha" in page and "Saga:" in page and "compensando" in page
    assert (
        'data-ops-task="ativos|job-1|retry|producao"' in page
        and 'data-ops-task="ativos|job-2|cancel|producao"' in page
    )
    assert "Sem resposta de:" in page
    assert (
        "job-1" not in hub.get("/admin/tasks?status=running").text
        and "job-2" in hub.get("/admin/tasks?status=running").text
    )
    assert "job-2" not in hub.get("/admin/tasks?app=ativos&status=failed").text
    r = hub.post("/api/ops/tasks/ativos/job-1/retry", headers=JSON)
    assert r.status_code == 200 and r.json()["job"]["status"] == "queued"
    assert "Tarefa reprocessada" in audit_titles(hub)
    assert hub.post("/api/ops/tasks/ativos/job-1/zzz", headers=JSON).status_code == 409
    assert (
        hub.post("/api/ops/tasks/ativos/job-9/cancel", headers=JSON).status_code == 409
    )  # app respondeu 404
    assert hub.post("/api/ops/tasks/ativos/job-1/retry?env=staging", headers=JSON).status_code == 404
    assert hub.post("/api/ops/tasks/nao/job-1/retry", headers=JSON).status_code == 404
    assert hub.post("/api/ops/tasks/ativos/a%20b/retry", headers=JSON).status_code == 409


def test_jobs_helpers() -> None:
    j = JOBS[0]
    assert (label(j), percent(j), can_retry(j), can_cancel(j)) == ("Falhou", 50, True, False)
    assert label({"status": "queued", "attempts": 1}) == "Na fila (nova tentativa)"
    assert (
        percent({"status": "done"}) == 100
        and saga_summary(None) is None
        and saga_summary({"saga": 1}) is None
    )


# ---- Backups ---------------------------------------------------------------------------------------
@pytest.fixture
def bk(hub: TestClient, docker: FakeDocker) -> FakeDocker:
    b = hub.app.state.ops_backups  # type: ignore[attr-defined]

    async def env(app: str, e: str) -> dict[str, str]:
        return {"HUB_DB_URL": "postgres://u:p@db:5432/appdb"}

    b.app_env = env
    return docker


def wait_job(c: TestClient, jid: int) -> dict[str, Any]:
    for _ in range(200):
        j = c.get(f"/api/ops/jobs/{jid}").json()
        if j["done"]:
            return j  # type: ignore[no-any-return]
        time.sleep(0.05)
    raise AssertionError("job não terminou")


def test_backup_run_least_privilege_and_vault(hub: TestClient, bk: FakeDocker, tmp_path: Path) -> None:
    r = hub.post("/api/ops/backups/ativos/run", headers=JSON)
    assert r.status_code == 202
    j = wait_job(hub, r.json()["id"])
    assert j["ok"], j["message"]
    rec = hub.get("/api/ops/backups").json()["history"][0]
    assert rec["status"] == "Concluído" and rec["file"].startswith("ativos-") and "appdb" in rec["detail"]
    f = Path(hub.app.state.data_dir) / "backups" / "ativos" / rec["file"]  # type: ignore[attr-defined]
    assert hashlib.sha256(f.read_bytes()).hexdigest() == rec["sha256"]
    # pg_dump ran with the read-only role and the vault password (never in clear text in ops.json)
    dump = next(c for c in bk.calls if c[0] == "exec" and c[2][0] == "pg_dump")
    assert dump[2][dump[2].index("-U") + 1] == "hub_bkp_appdb"
    pw = dump[3]["PGPASSWORD"]
    raw = (Path(hub.app.state.data_dir) / "ops.json").read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert pw not in raw and "vault:v1:" in raw
    assert hub.app.state.ops.secret("backup.pg:appdb") == pw  # type: ignore[attr-defined]  # stable across backups
    assert "Backup concluído" in audit_titles(hub)
    assert (
        "Backup concluído" in hub.get("/admin/backups").text or "Concluído" in hub.get("/admin/backups").text
    )
    # restore, test (temporary database) and clone (F5)
    rid = rec["id"]
    r = hub.post(f"/api/ops/backups/records/{rid}/restore", headers=JSON)
    assert wait_job(hub, r.json()["id"])["ok"]
    r = hub.post(f"/api/ops/backups/records/{rid}/test", headers=JSON)
    assert wait_job(hub, r.json()["id"])["ok"]
    cmds = [c[2][0] for c in bk.calls if c[0] == "exec"]
    assert {"psql", "createdb", "dropdb"} <= set(cmds)
    assert (
        hub.post(f"/api/ops/backups/records/{rid}/clone", headers=JSON).status_code == 202
    )  # job (failure: no staging database)
    assert hub.post(f"/api/ops/backups/records/{rid}/zzz", headers=JSON).status_code == 404
    assert hub.post("/api/ops/backups/records/999/restore", headers=JSON).status_code == 404
    titles = audit_titles(hub)
    assert "Restauração concluída" in titles and "Teste de restore concluído" in titles
    page = hub.get("/admin/backups").text
    assert 'data-ops-restore="restore"' in page and "OK em" in page  # last restore test


def test_backup_failure_and_busy(hub: TestClient, bk: FakeDocker) -> None:
    bk.fail["pg_dump"] = 1
    r = hub.post("/api/ops/backups/ativos/run", headers=JSON)
    j = wait_job(hub, r.json()["id"])
    assert not j["ok"] and "pg_dump" in j["message"]
    rec = hub.get("/api/ops/backups").json()["history"][0]
    assert rec["status"] == "Falhou" and rec["file"] is None
    assert (
        "Backup falhou" in audit_titles(hub) and "Falhas nas últimas 24 h" in hub.get("/admin/backups").text
    )
    assert hub.post("/api/ops/backups/zzz/run", headers=JSON).status_code == 404
    # app in maintenance blocks the backup until released
    ops = hub.app.state.ops  # type: ignore[attr-defined]
    d = ops.read()
    d["maintenance"]["ativos|producao"] = {"kind": "restauracao_falhou", "since": 1, "detail": "x"}
    ops.write(d)
    assert hub.post("/api/ops/backups/ativos/run", headers=JSON).status_code == 409
    assert "Liberar (dados conferidos)" in hub.get("/admin/backups").text
    assert "Em manutenção" in hub.get("/admin/runtime").text
    assert hub.post("/api/ops/runtime/ativos/restart", headers=JSON).status_code == 409
    assert hub.post("/api/ops/maintenance/ativos/clear", headers=JSON).status_code == 200
    assert hub.post("/api/ops/maintenance/ativos/clear", headers=JSON).status_code == 409
    assert "Manutenção liberada" in audit_titles(hub)


def test_backup_policy_and_retention(hub: TestClient, bk: FakeDocker) -> None:
    pol = {"schedule": "A cada 6 h", "keep": 1, "volumes": ["data"], "hooks": False, "destination": "Local"}
    r = hub.put("/api/ops/backups/policies/ativos", json=pol)
    assert r.status_code == 200 and r.json()["envs"] == ["producao"]
    assert "Política de backup alterada" in audit_titles(hub)
    for bad in (
        {**pol, "schedule": ""}, {**pol, "schedule": "99 99 * * *"}, {**pol, "keep": 0}, {**pol, "keep": 91},
        {**pol, "volumes": []}, {**pol, "volumes": ["A/B"]}, {**pol, "destination": ""}, {**pol, "envs": ["x"]},
        {**pol, "missed": "x"}, {**pol, "tz": "Nao/Existe"},
    ):  # fmt: skip
        assert hub.put("/api/ops/backups/policies/ativos", json=bad).status_code == 400, bad
    assert hub.put("/api/ops/backups/policies/zzz", json=pol).status_code == 404
    ids = []
    for _ in range(2):  # keep=1: the second backup expires the first
        ids.append(
            wait_job(hub, hub.post("/api/ops/backups/ativos/run", headers=JSON).json()["id"])["result"][
                "record"
            ]
        )
    hist = {h["id"]: h for h in hub.get("/api/ops/backups").json()["history"]}
    assert hist[ids[0]]["status"] == "Expirado" and hist[ids[1]]["status"] == "Concluído"
    assert (
        "manual" in hub.get("/admin/backups").text
        or hub.put("/api/ops/backups/policies/ativos", json={**pol, "schedule": "Manual"}).status_code == 200
    )
    assert "produção: manual" in hub.get("/admin/backups").text


def test_backup_credentials_rotation_and_aside(hub: TestClient, bk: FakeDocker) -> None:
    pw1 = hub.app.state.ops.secret("backup.pg:appdb")  # type: ignore[attr-defined]
    r = hub.post("/api/ops/backups/credentials/rotate", headers=JSON)
    assert r.status_code == 200 and r.json()["rotated"] >= 1 and r.json()["failed"] == 0
    assert hub.app.state.ops.secret("backup.pg:appdb") != pw1  # type: ignore[attr-defined]
    assert "Credenciais de backup rotacionadas" in audit_titles(hub)
    assert hub.get("/api/ops/backups/aside").json() == {"available": True, "aside": []}
    assert hub.post("/api/ops/backups/aside/x/drop", headers=JSON).status_code == 404
    assert hub.post("/api/ops/backups/aside/x/zzz", headers=JSON).status_code == 404
    bk.fail["psql"] = 3
    r = hub.post("/api/ops/backups/credentials/rotate", headers=JSON)
    assert r.json()["failed"] >= 1 and "falhou" in " ".join(r.json()["items"])


def test_backup_units() -> None:
    sql = backup_role_sql("appdb", "a" * 24)
    assert "hub_bkp_appdb" in sql and "default_transaction_read_only = on" in sql and "HUB-ALIEN-ROLE" in sql
    for db, pw in (("postgres", "a" * 24), ("a-b", "a" * 24), ("ok", "short"), ("ok", "a" * 15 + "'")):
        with pytest.raises(OpsError):
            backup_role_sql(db, pw)
    pol = json.loads(bucket_policy("b"))
    assert pol["Statement"][1]["Action"] == ["s3:GetObject"]
    assert "s3:PutObject" in bucket_policy("b", True)
    assert parse_targets({"HUB_DB_URL": "postgres://u:p@h/d", "HUB_S3_BUCKET": "bk"}) == (
        "d",
        "bk",
    ) and parse_targets({}) == (None, None)
    assert "hunter2" not in redact("PASSWORD 'hunter2' x hunter2", "hunter2")
    assert (
        normalize("Manual") is None
        and normalize("Diário 02:30") == "30 2 * * *"
        and normalize("A cada 6 h") == "0 */6 * * *"
    )
    assert (
        normalize("A cada 15 min") == "*/15 * * * *"
        and normalize("Semanal seg 03:00") == "0 3 * * 1"
        and normalize("0 2 * * 1-5")
    )
    for bad in ("Diário 25:00", "Semanal xx 01:00", "A cada 99 h", "a cada x", "* * *"):
        with pytest.raises(OpsError):
            normalize(bad)


def test_bundle_with_bucket_and_volumes(hub: TestClient, bk: FakeDocker, tmp_path: Path) -> None:
    """Bucket via `mc mirror` (read-only user) and disk volumes go into the package; the restore hands them back."""
    from hub.connections.provision import ObjectStore

    backups = hub.app.state.ops_backups  # type: ignore[attr-defined]
    seen: list[list[str]] = []

    async def runner(args: list[str], env: dict[str, str]) -> tuple[int, str]:
        seen.append(args)
        if "mirror" in args and "--overwrite" in args and args[-2].startswith("bk/"):
            (Path(args[-1]) / "sub").mkdir()
            (Path(args[-1]) / "sub" / "f.txt").write_bytes(b"obj")
        return 0, ""

    backups.objstore = ObjectStore("http://admin:pw@objstore:9000", runner)

    async def env(app: str, e: str) -> dict[str, str]:
        return {"HUB_S3_BUCKET": "ativos-bkt"}

    backups.app_env = env
    vol = Path(hub.app.state.data_dir) / "apps" / "ativos" / "data"  # type: ignore[attr-defined]
    vol.mkdir(parents=True)
    (vol / "a.txt").write_text("v1", encoding="utf-8")
    jid = hub.post("/api/ops/backups/ativos/run", headers=JSON).json()["id"]
    j = wait_job(hub, jid)
    assert j["ok"], j["message"]
    assert any("bkp-ativos-bkt" in " ".join(a) for a in seen)  # read-only user created
    rec = hub.get("/api/ops/backups").json()["history"][0]
    assert "bucket ativos-bkt" in rec["detail"] and "volume data" in rec["detail"]
    (vol / "a.txt").write_text("alterado", encoding="utf-8")
    assert wait_job(
        hub, hub.post(f"/api/ops/backups/records/{rec['id']}/restore", headers=JSON).json()["id"]
    )["ok"]
    assert (vol / "a.txt").read_text(encoding="utf-8") == "v1"
    assert any("mirror" in a and a[-1] == "hub/ativos-bkt" for a in seen)
    # tampered file: sha256 does not match
    f = Path(hub.app.state.data_dir) / "backups" / "ativos" / rec["file"]  # type: ignore[attr-defined]
    f.write_bytes(f.read_bytes() + b"x")
    j = wait_job(hub, hub.post(f"/api/ops/backups/records/{rec['id']}/test", headers=JSON).json()["id"])
    assert not j["ok"] and "sha256" in j["message"]


def test_bundle_without_targets(hub: TestClient, bk: FakeDocker) -> None:
    async def env(app: str, e: str) -> dict[str, str]:
        return {}

    hub.app.state.ops_backups.app_env = env  # type: ignore[attr-defined]
    j = wait_job(hub, hub.post("/api/ops/backups/servicos/run", headers=JSON).json()["id"])
    assert not j["ok"] and "Nada a copiar" in j["message"]


# ---- Docker real (opcional: `pytest -m docker`, DOCKER_AVAILABLE=1) -----------------------------------------
@pytest.mark.docker
async def test_real_engine_probe() -> None:
    import os

    if os.getenv("DOCKER_AVAILABLE") != "1":
        pytest.skip("DOCKER_AVAILABLE=1 não definido")
    info = await rt.Engine().probe()
    assert info["reachable"], info["error"]
    assert all(x["name"].startswith(rt.prefix()) for x in info["live"])
    assert HDR  # helper headers imported


def test_sdk_page_and_manifest_validate(hub: TestClient) -> None:
    """F7a: /admin/sdk existed only in the menu (404); manifest validation serves the screen."""
    assert hub.get("/admin/sdk").headers["location"] == "/admin/deploys"
    page = hub.get("/admin/deploys")
    assert (
        page.status_code == 200
        and "data-ops-validate" in page.text
        and "Validar um manifest.json" in page.text
    )
    ok = hub.post(
        "/api/ops/manifest/validate",
        content=json.dumps({"id": "novoapp", "name": "N", "version": "1", "routes": ["/novoapp/*"]}),
        headers=JSON | HDR,
    )
    assert ok.status_code == 200 and ok.json()["valid"] and ok.json()["registered"] is False
    assert ok.json()["manifest"]["resources"]["memory"] and ok.json()["manifest"]["routes"]
    bad = hub.post("/api/ops/manifest/validate", content="{", headers=JSON | HDR)
    assert bad.status_code == 200 and bad.json()["valid"] is False and bad.json()["error"]
    conflict = hub.post(
        "/api/ops/manifest/validate",
        content=json.dumps({"id": "novoapp", "name": "N", "routes": ["/ativos/x"]}),
        headers=JSON | HDR,
    )
    assert conflict.json()["valid"] is False


def test_i18n_json_errors_and_js_catalog(hub: TestClient) -> None:
    """F7a: JSON error in the browser's language and hub-i18n.js catalog at /assets/i18n/<lang>.json."""
    es = hub.get("/api/v1/jobs/999999", headers={"accept-language": "es-ES"})  # `es` was dropped: becomes en
    assert (
        es.status_code == 404 and es.json()["title"] == "Not found" and es.headers["content-language"] == "en"
    )
    assert (
        hub.get("/api/v1/jobs/999999", headers={"accept-language": "pt-BR"}).json()["title"]
        == "Não encontrado"
    )
    cat = hub.get("/assets/i18n/en.json").json()
    assert (
        cat["lang"] == "en" and cat["exact"]["Variável excluída."] == "Variable deleted." and cat["templates"]
    )
    assert hub.get("/assets/i18n/pt-BR.json").status_code == 404
    assert hub.get("/assets/i18n/es.json").status_code == 404


def test_same_origin_post_with_opaque_origin(hub: TestClient) -> None:
    """F7a: `Referrer-Policy: no-referrer` makes the browser send `Origin: null` on a POST from the hub itself
    (OAuth consent); `Sec-Fetch-Site: same-origin` is enough. Cross-origin remains blocked."""
    ok = hub.post("/api/me/prefs", headers={"origin": "null", "sec-fetch-site": "same-origin"})
    assert ok.status_code != 403, ok.text
    bad = hub.post(
        "/oauth/authorize", headers={"origin": "null", "sec-fetch-site": "cross-site"}, data={"x": "1"}
    )
    assert bad.status_code == 403


def test_bearer_with_stray_session_cookie_skips_csrf_check(hub: TestClient) -> None:
    """F7a: API client with `Authorization: Bearer` and a session cookie alongside (inherited context) is not blocked
    by the same-origin test - the identity comes from the token; without a token, cross-origin stays 403."""
    tok = hub.post(
        "/api/admin/tokens", json={"name": "f7a", "actions": ["mcp"], "apps": [], "envs": []}
    ).json()["token"]
    h = {"origin": "http://evil.example", "sec-fetch-site": "cross-site"}
    ok = hub.put("/api/me/prefs", json={"theme": ""}, headers=h | {"authorization": f"Bearer {tok}"})
    assert ok.status_code != 403, ok.text
    assert hub.put("/api/me/prefs", json={"theme": ""}, headers=h).status_code == 403


def test_forbidden_page_is_inside_the_shell_and_loader_text(hub: TestClient) -> None:
    """F7a: 403 page with page header (section eyebrow + title) and the loader text."""
    reader = add_user(hub, "leitor-f7a", ["Leitor"])
    r = reader.get("/admin/users")
    assert r.status_code == 403
    assert 'class="page-title">Acesso negado<' in r.text and 'class="eyebrow">Acesso<' in r.text
    assert 'id="app-root"' in r.text
    page = hub.get("/apps/ativos")
    assert page.status_code == 200 and "Preparando sua aplicação" in page.text


def test_tour_state_reaches_the_shell(hub: TestClient) -> None:
    """F7a: the <body>'s `data-hub-tour` comes from the preference (it was fixed to "done" before and the guided tour never opened)."""
    assert hub.put("/api/me/onboarding", json={"tour": ""}).status_code == 200
    assert 'data-hub-tour=""' in hub.get("/").text
    assert hub.put("/api/me/onboarding", json={"tour": "step:1"}).status_code == 200
    assert 'data-hub-tour="step:1"' in hub.get("/").text
    assert hub.put("/api/me/onboarding", json={"tour": "done"}).status_code == 200
    assert 'data-hub-tour="done"' in hub.get("/").text


def test_rail_mode_cookie_is_per_context_and_loader_texts(hub: TestClient) -> None:
    """F7a: the server draws the page in the rail mode stored in the cookie (`hub_rail_<ctx>`, separate in the apps)."""

    def get(path: str, **cookies: str) -> str:
        jar = "; ".join(["hub_session=test-session", *[f"hub_rail_{k}={v}" for k, v in cookies.items()]])
        return str(hub.get(path, headers={"cookie": jar}).text)

    assert 'class="rail-expanded' in get("/applications") and 'data-rail-ctx="hub"' in get("/applications")
    app_page = get("/apps/ativos")
    assert 'class="rail-hidden in-app' in app_page and 'data-rail-ctx="app"' in app_page
    assert "Carregando workspace" in app_page
    assert 'class="rail-collapsed in-app' in get("/apps/ativos", app="collapsed")
    assert 'class="rail-expanded' in get(
        "/applications", app="collapsed"
    )  # what is chosen inside does not leak outside
    assert 'class="rail-expanded' in get("/applications", hub="bogus")


def test_approved_script_enters_the_page_csp(hub: TestClient) -> None:
    """F7a: the hash of the approved app's own JavaScript goes into the app page's CSP (it was an empty stub before)."""
    from hub.gateway.csp import approved_scripts

    ops = hub.app.state.ops  # type: ignore[attr-defined]
    d = ops.read()
    sha = "sha384-" + "A" * 64
    dep = {"id": d["next_deploy_id"], "app": "ativos", "version": "9.9.9", "env": "Produção", "origin": "Import manual",
           "commit": "—", "status": m.IMPLANTADO, "at": 1, "package": None, "sha256": "", "image": "x", "note": "",
           "runtime": "", "scripts": [{"path": "ui/assets/x.js", "sha384": sha, "reason": "gráfico interativo da tela"}],
           "submitted_by": "", "submitted_by_id": ""}  # fmt: skip
    d["next_deploy_id"] += 1
    d["deployments"].insert(0, dep)
    ops.write(d)
    assert approved_scripts("ativos", False) == []  # still without the security review
    d = ops.read()
    d["script_approvals"].append(
        {"app": "ativos", "path": "ui/assets/x.js", "sha384": sha, "by": "r", "by_id": "r1", "at": 2}
    )
    ops.write(d)
    assert approved_scripts("ativos", False) == [("x.js", sha)]
    csp = hub.get("/apps/ativos").headers["content-security-policy"]
    assert f"'{sha}'" in csp and "/_frag/ativos/assets/x.js" in csp
