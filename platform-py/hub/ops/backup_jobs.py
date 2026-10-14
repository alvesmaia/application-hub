"""Backup and restore jobs (run in the background): records, maintenance window, off-site copy, audit and alerts."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hub.admin.store import log_stamp
from hub.ops import model as m
from hub.ops.model import Data, OpsError, now_ms
from hub.ops.opsctx import OpsCtx, env_key
from hub.ux.inbox import item_of

Phase = Callable[[int, str], None]
Bundle = tuple[str, str, dict[str, Any]]  # (file, sha256, manifest)
RESTORE_ACTIONS = {"restore": ("Restauração", "Restauração", "concluída"),
                   "clone": ("Clone p/ staging", "Clone para staging", "concluído"),
                   "test": ("Teste de restore", "Teste de restore", "concluído")}  # fmt: skip


def maintenance_error(ops: OpsCtx, d: Data, app_id: str, env: str) -> str | None:
    mt = d["maintenance"].get(f"{app_id}|{env}")
    if not mt:
        return None
    failed = mt["kind"] == "restauracao_falhou"
    lbl = "Em manutenção (restauração falhou)" if failed else "Em manutenção (restauração)"
    return f"{lbl}: {ops.name_of(app_id)} bloqueado até a restauração terminar ou ser liberada em Backups."


async def alert(
    ops: OpsCtx, level: str, title: str, detail: str, app_id: str, link: str = "/admin/backups"
) -> None:
    """Platform alert for the administrators (Inbox + SSE); at most one identical alert per hour."""
    ops.app.state.inbox.push(item_of("alert", app_id, title, body=detail[:500], level=level, to_perm="editar_configuracoes",
                                     link=link, source=f"alert:{app_id}:{title}:{now_ms() // 3_600_000}"))  # fmt: skip


async def set_maintenance(ops: OpsCtx, app_id: str, k: str, kind: str | None, detail: str) -> None:
    """Turns "Em manutenção (restauração)" (Under maintenance, restore) on or off for the app×environment (`None` = releases it)."""
    async with ops.store.locked(True) as dd:
        if kind is None:
            dd["maintenance"].pop(f"{app_id}|{k}", None)
        else:
            dd["maintenance"][f"{app_id}|{k}"] = {"kind": kind, "since": now_ms(), "detail": detail[:300],
                                                  "replica": ops.app.state.leader.replica}  # fmt: skip


def add_record(dd: Data, rec: dict[str, Any]) -> int:
    """New history record on top (newest first); returns its id."""
    rid = dd["next_backup_id"]
    dd["next_backup_id"] += 1
    dd["backups"].insert(0, {"id": rid, **rec})
    return int(rid)


def _error_text(e: Exception) -> str:
    return e.message if isinstance(e, OpsError) else f"{type(e).__name__}: {e}"[:300]


def _phase(ops: OpsCtx, job_id: int) -> Phase:
    def phase(p: int, msg: str) -> None:
        ops.jobs.update(job_id, lambda j: j.update(progress=p, message=msg))

    return phase


async def safety_snapshot(ops: OpsCtx, app_id: str, k: str, phase: Phase) -> str:
    """Backup "Pré-restauração" (Pre-restore) of the current state (restorable like any other); a failure becomes a note, not an abort."""
    if os.getenv("HUB_RESTORE_SAFETY_SNAPSHOT", "1").strip() == "0":
        return ""
    pol = next((p for p in ops.store.read()["policies"] if p["app"] == app_id), None)
    phase(5, "Snapshot de segurança (Pré-restauração)")
    t0 = now_ms()
    try:
        name, sha, man = await ops.backups.create_bundle(
            app_id, k, pol["volumes"] if pol else ["data"], False, phase
        )
    except Exception as e:
        return f" · sem snapshot de segurança: {e.message if isinstance(e, OpsError) else type(e).__name__}"
    async with ops.store.locked(True) as dd:
        rid = add_record(dd, {"app": app_id, "kind": "Pré-restauração", "size": man["size"], "status": "Concluído",
                              "at": t0, "file": name, "sha256": sha, "files": man["count"], "duration_ms": now_ms() - t0,
                              "detail": f"{man['summary']} · {man['consistency']}", "source": None, "env": k})  # fmt: skip
    return f" · snapshot Pré-restauração #{rid}"


# ---- backup -------------------------------------------------------------------------------------------
@dataclass
class BackupRun:
    app_id: str
    k: str
    actor: str
    pol: dict[str, Any]
    job_id: int
    rid: int
    label: str  # app name + " (staging)"


async def start_backup(ops: OpsCtx, app_id: str, k: str, kind: str, actor: str) -> dict[str, Any]:
    """Creates the backup job and runs it in the background (manual or from the scheduler); `OpsError` if it cannot start."""
    ops.need_app(app_id)
    d = ops.store.read()
    pol = next((p for p in d["policies"] if p["app"] == app_id), None)
    if pol is None:
        raise OpsError("Aplicação sem política de backup.", 404)
    if err := maintenance_error(ops, d, app_id, k):
        raise OpsError(err, 409)
    await ops.jobs.claim(app_id)
    label = f"{ops.name_of(app_id)}{' (staging)' if k == 'staging' else ''}"
    job = await ops.jobs.create("backup", f"Backup de {label}", [], app_id, k)
    async with ops.store.locked(True) as dd:
        rid = add_record(dd, {"app": app_id, "kind": kind, "size": 0, "status": "Em andamento", "at": now_ms(), "file": None,
                              "sha256": "", "files": 0, "duration_ms": 0, "detail": "", "source": None, "env": k})  # fmt: skip
    ops.spawn(_run_backup(ops, BackupRun(app_id, k, actor, pol, job["id"], rid, label)))
    return job


async def _offsite_note(ops: OpsCtx, run: BackupRun, file: str, phase: Phase) -> str:
    """Off-site copy of a finished bundle: the local backup still counts if it fails (note + alert)."""
    phase(90, "Cópia externa")
    try:
        dest = await ops.backups.offsite(run.app_id, run.k, file)
    except OpsError as e:
        await alert(
            ops,
            "error",
            f"Cópia externa do backup de {ops.name_of(run.app_id)} falhou",
            e.message,
            run.app_id,
        )
        return f" · cópia externa FALHOU: {e.message}"
    return f" · cópia externa em {dest}" if dest else ""


async def _save_backup(
    ops: OpsCtx, run: BackupRun, res: Bundle | None, err: str, note: str, elapsed: int
) -> list[str]:
    async with ops.store.locked(True) as dd:
        rec = next(x for x in dd["backups"] if x["id"] == run.rid)
        rec["duration_ms"] = elapsed
        if not res:
            rec.update(status="Falhou", detail=err)
            return []
        name, sha, man = res
        rec.update(status="Concluído", size=man["size"], file=name, sha256=sha, files=man["count"],
                   detail=f"{man['summary']} · {man['consistency']}{note}")  # fmt: skip
        return ops.backups.prune(dd, run.app_id, run.k, run.pol["keep"])


async def _report_backup(
    ops: OpsCtx, run: BackupRun, res: Bundle | None, err: str, note: str, pruned: list[str]
) -> None:
    ok = res is not None
    sizes = f"{res[2]['summary']} · {m.human(res[2]['size'])} · sha256 {res[1][:12]}…{note}" if res else err
    detail = f"{run.label} · {sizes}"
    await ops.admin.audit(
        run.actor, "Backup concluído" if ok else "Backup falhou", detail, icon="clock", scope=run.app_id
    )
    ops.log("INFO" if ok else "ERROR", run.app_id, run.actor, f"backup: {detail}")
    if not ok:
        await alert(ops, "error", f"Backup de {run.label} falhou", err, run.app_id)
    ops.jobs.busy.discard(run.app_id)
    msg = (
        f"Backup de {run.label} concluído ({m.human(res[2]['size'])})."
        if res
        else f"Backup de {run.label} falhou: {err}"
    )
    ops.jobs.update(run.job_id, lambda j: j.update(done=True, ok=ok, progress=100, message=msg,
                                                   result={"record": run.rid, "pruned": pruned}))  # fmt: skip


async def _run_backup(ops: OpsCtx, run: BackupRun) -> None:
    phase, t0 = _phase(ops, run.job_id), now_ms()
    res: Bundle | None = None
    err = note = ""
    try:
        res = await ops.backups.create_bundle(run.app_id, run.k, run.pol["volumes"], run.pol["hooks"], phase)
    except Exception as e:
        err = _error_text(e)
    if res:
        note = await _offsite_note(ops, run, res[0], phase)
    pruned = await _save_backup(ops, run, res, err, note, now_ms() - t0)
    await _report_backup(ops, run, res, err, note, pruned)


# ---- restore / clone / test ---------------------------------------------------------------------------
@dataclass
class RestoreRun:
    rec: dict[str, Any]
    action: str
    k: str
    actor: str
    job_id: int
    label: str

    @property
    def app_id(self) -> str:
        return str(self.rec["app"])


async def start_restore(ops: OpsCtx, rec_id: int, action: str, actor: str) -> dict[str, Any]:
    """Restore, clone to staging or restore test of a finished backup, in the background; `OpsError` if it cannot start."""
    rec = next((x for x in ops.store.read()["backups"] if x["id"] == rec_id), None)
    if rec is None:
        raise OpsError("Backup não encontrado.", 404)
    if action not in RESTORE_ACTIONS:
        raise OpsError("Ação inválida.", 404)
    if rec["status"] != "Concluído" or not rec.get("file"):
        raise OpsError("Só backups concluídos (com arquivo disponível) podem ser usados.")
    app_id, k = rec["app"], env_key(rec.get("env"))
    if action == "clone" and k == "staging":
        raise OpsError("O clone parte de um backup de produção.")
    await ops.jobs.claim(app_id)
    label = f"{ops.name_of(app_id)}{' (staging)' if k == 'staging' else ''}"
    job = await ops.jobs.create("restore", f"{RESTORE_ACTIONS[action][1]} de {label}", [], app_id, None)
    ops.spawn(_run_restore(ops, RestoreRun(rec, action, k, actor, job["id"], label)))
    return job


async def _enter_maintenance(ops: OpsCtx, run: RestoreRun, phase: Phase) -> str:
    """Maintenance window before the restore; the real restore also takes a safety snapshot (returns its note)."""
    if run.action == "clone":
        await set_maintenance(
            ops, run.app_id, "staging", "restauracao", f"clonando o backup #{run.rec['id']} para staging"
        )
    if run.action != "restore":
        return ""
    await set_maintenance(ops, run.app_id, run.k, "restauracao", f"restaurando o backup #{run.rec['id']}")
    return await safety_snapshot(ops, run.app_id, run.k, phase)


async def _leave_maintenance(ops: OpsCtx, run: RestoreRun, ok: bool, err: str) -> None:
    if run.action == "clone":
        await set_maintenance(ops, run.app_id, "staging", None, "")
    if run.action == "restore":
        await set_maintenance(ops, run.app_id, run.k, None if ok else "restauracao_falhou", err)


async def _report_restore(
    ops: OpsCtx, run: RestoreRun, res: tuple[int, str] | None, err: str, safety: str, elapsed: int
) -> None:
    kind, verb, done_word = RESTORE_ACTIONS[run.action]
    ok, rec = res is not None, run.rec
    result = f"{res[1]} · {elapsed / 1000:.1f} s" if res else err
    detail = f"{run.label} · backup de {log_stamp(rec['at'])} · {result}{safety}"
    if run.action == "restore" and not ok:
        await alert(ops, "error", f"Restauração de {run.label} falhou", err, run.app_id)
    async with ops.store.locked(True) as dd:
        add_record(dd, {"app": run.app_id, "kind": kind, "size": rec["size"], "status": "Concluído" if ok else "Falhou",
                        "at": now_ms(), "file": None, "sha256": rec["sha256"], "files": res[0] if res else 0,
                        "duration_ms": elapsed, "detail": res[1] if res else err, "source": rec["id"], "env": rec.get("env", "")})  # fmt: skip
    await ops.admin.audit(
        run.actor, f"{verb} {done_word if ok else 'falhou'}", detail, icon="clock", scope=run.app_id
    )
    ops.log("INFO" if ok else "ERROR", run.app_id, run.actor, f"{verb.lower()}: {detail}")
    ops.jobs.busy.discard(run.app_id)
    msg = f"{verb} de {run.label} {done_word} — {res[1]}." if res else f"{verb} de {run.label} falhou: {err}"
    ops.jobs.update(run.job_id, lambda j: j.update(done=True, ok=ok, progress=100, message=msg,
                                                   result={"duration_ms": elapsed}))  # fmt: skip


async def _run_restore(ops: OpsCtx, run: RestoreRun) -> None:
    phase, t0 = _phase(ops, run.job_id), now_ms()
    res: tuple[int, str] | None = None
    err = ""
    safety = await _enter_maintenance(ops, run, phase)
    try:
        res = await ops.backups.restore_bundle(
            run.app_id, run.k, run.rec["file"], run.rec["sha256"], run.action, phase
        )
    except Exception as e:
        err = _error_text(e)
    elapsed = now_ms() - t0
    await _leave_maintenance(ops, run, res is not None, err)
    await _report_restore(ops, run, res, err, safety, elapsed)
