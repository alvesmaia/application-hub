"""Remove an application from the hub: guards, the background job (one log line per step) and its audit entry.

Order: optional backup of the database (a failure aborts before anything is deleted) → `remove_steps.STEPS`. A step
that fails after the app left the catalog is reported and the rest still runs; the audit trail is never deleted."""

from __future__ import annotations

from typing import Any

from hub.ops import model as m
from hub.ops import newapp
from hub.ops.backup_jobs import add_record
from hub.ops.model import Data, OpsError, now_ms
from hub.ops.opsctx import OpsCtx
from hub.ops.remove_steps import STEPS, RemoveRun

NOTHING_TO_COPY = "Nada a copiar"


def busy_reason(ops: OpsCtx, d: Data, app: str) -> str | None:
    """Why the app cannot be removed now (deployment, rollout, backup/restore, maintenance), or None."""
    name = ops.name_of(app)
    if any(m.in_progress(d, app, env) for env in (m.PRODUCAO, m.STAGING)):
        return f"{name}: implantação em andamento; aguarde terminar para remover."
    if any(k.startswith(f"{app}|") and lock.locked() for k, lock in ops.rollout.locks.items()):
        return f"{name}: troca de versão (blue/green) em andamento; aguarde terminar para remover."
    if app in ops.jobs.busy:
        return f"{name}: backup, restauração ou remoção em andamento; aguarde terminar para remover."
    if any(k.startswith(f"{app}|") for k in d["maintenance"]):
        return f"{name} em manutenção: libere em Backups antes de remover."
    return None


def samples(ops: OpsCtx) -> list[str]:
    """Sample apps: those of the read-only registry file (not deployed through the hub)."""
    return [a for a in ops.registry.ids() if not newapp.is_dynamic(ops.data_dir, a)]


async def _claim_all(ops: OpsCtx, apps: list[str]) -> None:
    """Reserves every app (no backup/restore meanwhile); all or none."""
    d = ops.store.read()
    for a in apps:
        if why := busy_reason(ops, d, a):
            raise OpsError(why, 409)
    done: list[str] = []
    try:
        for a in apps:
            await ops.jobs.claim(a)
            done.append(a)
    except OpsError:
        ops.jobs.busy.difference_update(done)
        raise


async def start(ops: OpsCtx, apps: list[str], actor: str, backup: bool, keep_data: bool) -> dict[str, Any]:
    """Checks and reserves the apps, then runs the removal in the background; returns the job."""
    await _claim_all(ops, apps)
    names = [ops.name_of(a) for a in apps]
    job = await ops.jobs.create(
        "remove", f"Remoção de {', '.join(names)}", [], apps[0] if len(apps) == 1 else None
    )
    runs = [
        RemoveRun(a, n, actor, backup, keep_data, int(job["id"])) for a, n in zip(apps, names, strict=True)
    ]
    ops.spawn(_run_all(ops, runs))
    return job


def _say(ops: OpsCtx, run: RemoveRun, step: str, line: str, ok: bool = True) -> None:
    run.lines.append(line)
    ops.log("INFO" if ok else "WARN", run.app, run.actor, f"remoção: {line}")
    row = {"name": f"{run.name} · {step}", "state": "ok" if ok else "failed", "detail": line}

    def add(j: dict[str, Any]) -> None:
        j["steps"].append(row)
        j["message"] = line

    ops.jobs.update(run.job_id, add)


async def _backup(ops: OpsCtx, run: RemoveRun) -> None:
    """ "Pré-remoção" (pre-removal) backup of production; nothing to copy is a note, any other failure aborts."""
    pol = next((p for p in ops.store.read()["policies"] if p["app"] == run.app), None)
    vols, t0 = (pol["volumes"] if pol else ["data"]), now_ms()
    try:
        name, sha, man = await ops.backups.create_bundle(run.app, "producao", vols, False, lambda p, s: None)
    except OpsError as e:
        if not e.message.startswith(NOTHING_TO_COPY):
            raise
        return _say(ops, run, "Backup", "backup: nada a copiar")
    async with ops.store.locked(True) as dd:
        rid = add_record(dd, {"app": run.app, "kind": "Pré-remoção", "size": man["size"], "status": "Concluído",
                              "at": t0, "file": name, "sha256": sha, "files": man["count"], "duration_ms": now_ms() - t0,
                              "detail": f"{man['summary']} · {man['consistency']}", "source": None, "env": "producao"})  # fmt: skip
    run.kept_backup = name
    _say(ops, run, "Backup", f"backup Pré-remoção #{rid} ({name})")


async def _steps(ops: OpsCtx, run: RemoveRun) -> None:
    for title, step in STEPS:
        try:
            for line in await step(ops, run):
                _say(ops, run, title, line)
        except Exception as e:  # reported; the next steps still clean what they can
            run.failed.append(title)
            _say(
                ops,
                run,
                title,
                f"{title}: falhou ({e.message if isinstance(e, OpsError) else type(e).__name__}: {e})"[:300],
                False,
            )


async def _run_one(ops: OpsCtx, run: RemoveRun) -> str:
    """Removes one app; returns the summary for the job (empty = done without failures)."""
    try:
        if run.backup and not run.keep_data:
            await _backup(ops, run)
    except Exception as e:
        msg = e.message if isinstance(e, OpsError) else f"{type(e).__name__}: {e}"
        _say(ops, run, "Backup", f"backup falhou: {msg}"[:300], False)
        return f"{run.name} não removida: o backup falhou ({msg})."[:300]
    await _steps(ops, run)
    detail = " · ".join([run.name, *run.lines])
    await ops.admin.audit(run.actor, "Aplicação removida", detail, icon="trash", scope=run.app)
    return f"{run.name} removida com falhas em: {', '.join(run.failed)}." if run.failed else ""


async def _run_all(ops: OpsCtx, runs: list[RemoveRun]) -> None:
    problems: list[str] = []
    try:
        for run in runs:
            if p := await _run_one(ops, run):
                problems.append(p)
    finally:
        for run in runs:
            ops.jobs.busy.discard(run.app)
    msg = " ".join(problems) or f"Remoção concluída: {', '.join(r.name for r in runs)}."
    ops.jobs.update(runs[0].job_id, lambda j: j.update(done=True, ok=not problems, progress=100, message=msg))
