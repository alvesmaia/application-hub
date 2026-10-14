"""Deployment jobs: package import (validation pipeline + provisioning), image build and blue/green deploy."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hub.ops import model as m
from hub.ops import newapp
from hub.ops import pipeline as pl
from hub.ops import provision as pv
from hub.ops import runtime as rt
from hub.ops.model import Data, OpsError, now_ms
from hub.ops.opsctx import OpsCtx, rollout_env

IMPORT_TITLES = {m.REJEITADO: "Pacote rejeitado pelo pipeline de validação",
                 m.AGUARDANDO: "Pacote validado — aguardando aprovação",
                 m.IMPLANTANDO: "Pacote validado — implantando em staging"}  # fmt: skip


@dataclass
class Upload:
    """A received `.hubapp` package and who sent it."""

    job_id: int
    data: bytes
    env: str  # "staging" | "producao"
    origin: str
    commit: str
    file_name: str
    actor: str
    uid: str
    config: dict[str, dict[str, str]]


# ---- import -------------------------------------------------------------------------------------------
async def _validate(ops: OpsCtx, up: Upload) -> dict[str, Any]:
    """Validation pipeline report; each finished step goes to the job and to the logs."""

    async def probe(url: str) -> int:
        return (await ops.client.get(url, timeout=5)).status_code

    def on_step(steps: list[dict[str, Any]]) -> None:
        ops.jobs.update(up.job_id, lambda j: j.update(steps=json.loads(json.dumps(steps))))

    # new app still without a production container: no upstream smoke test (the candidate proves readiness)
    regs = [e for e in ops.registry.entries()
            if not (newapp.is_dynamic(ops.data_dir, e.id) and await ops.eng.raw(rt.container_name(e.id, "producao")) is None)]  # fmt: skip
    report = await pl.run_all(up.data, up.env, regs, probe, on_step=on_step,
                              allow_without_api=os.getenv("HUB_ALLOW_WITHOUT_API") == "1")  # fmt: skip
    for st in report["steps"]:
        if st["state"] in ("ok", "warn", "failed"):
            ops.log("ERROR" if st["state"] == "failed" else "INFO", report["app"] or "platform", up.actor,
                    f"pipeline · {st['name']}: {st['detail']}")  # fmt: skip
    return report


async def _provision(ops: OpsCtx, up: Upload, report: dict[str, Any]) -> None:
    """F22: a new app joins the registry and its `requires` are provisioned before the deployment."""
    report["provisioned"] = {}
    if not (report["passed"] and report["app"] and report.get("manifest")):
        return
    app_id, mf = report["app"], report["manifest"]

    async def db(a: str, e: str) -> dict[str, Any]:
        return await pv.provision(ops.backups, ops.admin.cfg, up.actor, a, e)

    try:
        if newapp.register(ops.data_dir, ops.registry, ops.admin.cfg.apps, mf):
            ops.log("INFO", app_id, up.actor, "app novo registrado pela implantação")
        report["provisioned"] = await newapp.apply_requires(app_id, up.env, mf, up.config, up.actor, ops.admin.cfg, db,
                                                            getattr(ops.app.state, "storage_provision", None))  # fmt: skip
    except (OpsError, ValueError) as ex:
        msg = ex.message if isinstance(ex, OpsError) else str(ex)[:200]
        report.update(passed=False, reason=f"Provisionamento: {msg}")


def _keep_package(data_dir: Path, report: dict[str, Any], data: bytes) -> tuple[str, str | None]:
    """(sha256, stored package name) — only a package that passed is kept."""
    sha = pl.hashlib.sha256(data).hexdigest()
    if not report["passed"]:
        return sha, None
    package = f"{report['app']}-{report['version']}-{sha[:12]}.hubapp"
    (data_dir / "packages").mkdir(parents=True, exist_ok=True)
    (data_dir / "packages" / package).write_bytes(data)
    return sha, package


def _import_status(report: dict[str, Any], env: str) -> str:
    if not report["passed"]:
        return m.REJEITADO
    return (
        m.IMPLANTANDO if env == "staging" else m.AGUARDANDO
    )  # staging is approved by the pipeline itself (step 7)


async def _record_import(ops: OpsCtx, up: Upload, report: dict[str, Any], status: str) -> Data:
    sha, package = _keep_package(ops.data_dir, report, up.data)
    label = m.STAGING if up.env == "staging" else m.PRODUCAO
    dep = {"id": 0, "app": report["app"] or "desconhecido", "version": report["version"] or "—", "env": label,
           "origin": up.origin, "commit": up.commit, "status": status, "at": now_ms(), "image": report["image"],
           "package": package, "sha256": sha, "note": report["reason"], "runtime": "", "scripts": report["scripts"],
           "build": bool(report.get("build")), "submitted_by": up.actor, "submitted_by_id": up.uid}  # fmt: skip
    if report["passed"] and report["app"]:
        await ensure_baseline(
            ops, report["app"], "producao"
        )  # before the `push`: the compose version is the oldest (rollback)
    async with ops.store.locked(True) as d:
        return m.push(d, dep)


async def _audit_import(ops: OpsCtx, up: Upload, report: dict[str, Any], saved: Data, status: str) -> None:
    failed = "" if report["passed"] else f" · {report['reason']}"
    detail = f"{ops.name_of(saved['app'])} {saved['version']} · {saved['env']} · {up.origin} · {up.file_name}{failed}"
    scope = saved["app"] if ops.registry.get(saved["app"]) else "platform"
    await ops.admin.audit(up.actor, IMPORT_TITLES[status], detail, icon="file", scope=scope)


def _finish_import(
    ops: OpsCtx, job_id: int, good: bool, msg: str, saved: Data, report: dict[str, Any]
) -> None:
    def fin(j: dict[str, Any]) -> None:
        st = (j.get("steps") or [{}] * 6)[5]
        if st.get("state") == "running":  # build/candidate step in staging
            ready = f"Imagem {saved['image']} pronta e candidato respondeu à prontidão em staging"
            st.update(state="ok" if good else "failed", detail=ready if good else msg)
        j.update(done=True, ok=good, message=msg, progress=100,
                 result={"deployment": saved, "report": report}, provisioned=report["provisioned"])  # fmt: skip

    ops.jobs.update(job_id, fin)


async def run_import(ops: OpsCtx, up: Upload) -> None:
    """Pipeline → provisioning → record → audit; staging deploys right away, production waits for approval."""
    report = await _validate(ops, up)
    await _provision(ops, up, report)
    status = _import_status(report, up.env)
    saved = await _record_import(ops, up, report, status)
    await _audit_import(ops, up, report, saved, status)
    good = bool(report["passed"])
    msg = (
        "Pacote validado — aguardando aprovação para produção."
        if good
        else f"Pacote rejeitado — {report['reason']}"
    )
    if status == m.IMPLANTANDO:
        good, msg, saved = await run_deploy(ops, saved["id"], up.job_id, up.actor)
    _finish_import(ops, up.job_id, good, msg, saved, report)


# ---- baseline, build, deploy --------------------------------------------------------------------------
async def ensure_baseline(ops: OpsCtx, app_id: str, k: str) -> None:
    """First deployment of an app: records the version already running (compose), so rollback has somewhere to go back to."""
    raw = await ops.eng.raw(rt.container_name(app_id, k))
    e = ops.registry.get(app_id)
    if raw is None or e is None or k != "producao":
        return
    await ops.eng.pin(
        str(raw.get("Image", "")), app_id, f"baseline-{str(raw.get('Image', ''))[7:19]}"
    )  # rollback
    async with ops.store.locked(True) as d:
        live = (x for x in d["deployments"] if x["app"] == app_id and x["env"] == m.PRODUCAO)
        if any(x["status"] == m.IMPLANTADO for x in live):
            return
        base = m.push(d, {"id": 0, "app": app_id, "version": e.version or "—", "env": m.PRODUCAO, "origin": "Compose",
                          "commit": "—", "status": m.IMPLANTADO, "at": now_ms(), "package": None, "sha256": "",
                          "image": f"{str(raw['Config'].get('Image', 'imagem')).split(':')[0]}@{raw['Image']}",
                          "note": "versão em execução antes da primeira implantação", "runtime": "", "scripts": [],
                          "submitted_by": "", "submitted_by_id": ""})  # fmt: skip
        d["deployments"].append(
            d["deployments"].pop(d["deployments"].index(base))
        )  # the oldest one stays last


def _write_build_context(tmp: Path, files: dict[str, bytes], dockerfile: str) -> None:
    for n, b in pl.build_context(files, dockerfile).items():
        if n.startswith("build/"):
            f = tmp / n.removeprefix("build/")
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b)


def _build_logger(ops: OpsCtx, dep: Data, job_id: int, actor: str, lines: list[str]) -> Any:
    """Each build line goes to the job (last 200), to the logs console and to the deploy step detail."""
    tag, app_id = dep["image"], dep["app"]

    def on_line(ln: str) -> None:
        lines.append(ln[:300])
        ops.log("INFO", app_id, actor, f"build · {ln[:300]}")

        def up(j: dict[str, Any]) -> None:
            j.update(build_log=lines[-200:], message=f"Construindo a imagem · {ln[:120]}")
            if len(j.get("steps") or []) > 5:
                j["steps"][5].update(state="running", detail=f"Construindo a imagem {tag} · {ln[:120]}")

        ops.jobs.update(job_id, up)

    return on_line


async def build_image(ops: OpsCtx, dep: Data, job_id: int, actor: str) -> None:
    """Builds the package's image (`build/**`) on the daemon, with a memory cap; the log goes to the job, to the logs console and
    to the deployment record. The image of an already existing version (rollback, resubmission) is not rebuilt."""
    tag, app_id = dep["image"], dep["app"]
    if await ops.eng.has_image(tag) and dep.get("origin") == "Rollback":
        return
    if not dep.get("package"):
        raise OpsError("Pacote da implantação não está mais no hub: reenvie-o.", 422)
    files = pl.read_zip((ops.data_dir / "packages" / dep["package"]).read_bytes())
    dockerfile = str((json.loads(files["runtime.json"]).get("build") or {}).get("dockerfile", "Dockerfile"))
    lines: list[str] = []
    ops.jobs.update(job_id, lambda j: j.update(progress=2, message=f"Construindo a imagem {tag}"))
    tmp = ops.data_dir / "build" / f"{app_id}-{dep['id']}"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        _write_build_context(tmp, files, dockerfile)
        await ops.eng.build(
            str(tmp), tag, dockerfile, rt.memory_bytes(os.getenv("HUB_BUILD_MEMORY", "1Gi")), _build_logger(ops, dep, job_id, actor, lines),
            int(os.getenv("HUB_BUILD_CPUS", "2") or 0), float(os.getenv("HUB_BUILD_TIMEOUT_S", "900") or 0),
        )  # fmt: skip
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        async with ops.store.locked(True) as d:
            m._find(d, dep["id"])["build_log"] = lines[-200:]
    ops.log("INFO", app_id, actor, f"build concluído · {tag} ({len(lines)} linhas)")


async def _apply(ops: OpsCtx, dep: Data, job_id: int, actor: str) -> tuple[str, dict[str, Any]]:
    """(error, rollout result): baseline, optional build, then the blue/green rollout."""
    app_id, k = dep["app"], rollout_env(dep["env"])
    e = ops.registry.get(app_id)
    if e is None:
        return "Aplicação não encontrada no registro.", {}

    def phase(p: int, msg: str) -> None:
        ops.jobs.update(job_id, lambda j: j.update(progress=p, message=msg))

    try:
        await ensure_baseline(ops, app_id, k)
        lim = rt.resources_of(e, ops.store.read()["limits"])
        if dep.get("build"):
            await build_image(ops, dep, job_id, actor)
        return "", await ops.rollout.apply(app_id, k, lim, dep["version"], dep["image"] or None, phase)
    except OpsError as ex:
        return ex.message, {}
    except Exception as ex:
        return f"{type(ex).__name__}: {ex}"[:300], {}


async def _save_deploy(ops: OpsCtx, dep_id: int, err: str, res: dict[str, Any]) -> Data:
    """Marks the deployment deployed (and the previous active one replaced) or failed."""
    async with ops.store.locked(True) as d:
        dep = m._find(d, dep_id)
        if err:
            dep.update(status=m.FALHOU, note=err)
            return dict(dep)
        for x in d["deployments"]:
            if (
                x is not dep
                and x["app"] == dep["app"]
                and x["env"] == dep["env"]
                and x["status"] == m.IMPLANTADO
            ):
                x["status"] = m.SUBSTITUIDO
        dep.update(status=m.IMPLANTADO, runtime=f"container {res['container']}", at=now_ms())
        return dict(dep)


async def run_deploy(ops: OpsCtx, dep_id: int, job_id: int, actor: str) -> tuple[bool, str, Data]:
    """Blue/green of the `Implantando` (Deploying) deployment; returns (ok, message, record). The active one only leaves after the candidate is ready."""
    dep = m._find(ops.store.read(), dep_id)
    app_id, k = dep["app"], rollout_env(dep["env"])
    err, res = await _apply(ops, dep, job_id, actor)
    saved = await _save_deploy(ops, dep_id, err, res)
    what = f"{ops.name_of(app_id)} {saved['version']} · {dep['env']}"
    await ops.admin.audit(actor, "Implantação falhou" if err else "Versão implantada", what + (f" · {err}" if err else ""),
                          icon="alert" if err else "check", scope=app_id)  # fmt: skip
    ops.log(
        "ERROR" if err else "INFO", app_id, actor, f"implantação {what}: {err or 'concluída (blue/green)'}"
    )
    if not err and k == "producao":
        await ops.admin.cfg.clear_pending(app_id)
    msg = f"Implantação falhou — {err}" if err else f"{what} implantada (blue/green, sem indisponibilidade)."
    return not err, msg, saved
