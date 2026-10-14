"""Operations API: backup policies, runs, restore/clone/test, set-aside databases, maintenance and alerts."""

from __future__ import annotations

import os
import zoneinfo
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from hub.admin.store import log_stamp
from hub.ops import model as m
from hub.ops.backups import parse_targets
from hub.ops.model import Data, OpsError, now_ms
from hub.ops.opsctx import OpsCtx, env_key
from hub.ops.schedule import normalize
from hub.ux.inbox import item_of


def mount_backups(  # noqa: C901, PLR0915 closure mount moved from mount_ops; TASKS.md task 3
    c: OpsCtx,
) -> None:
    app, admin, registry, store, jobs, backups, api, name_of, spawn, actor_of, log, need_app, body = (
        c.app,
        c.admin,
        c.registry,
        c.store,
        c.jobs,
        c.backups,
        c.api,
        c.name_of,
        c.spawn,
        c.actor_of,
        c.log,
        c.need_app,
        c.body,
    )

    # ---- Backups -------------------------------------------------------------------------------------
    @api.get("/backups")
    async def list_backups() -> Any:
        d = store.read()
        return {"policies": d["policies"], "history": d["backups"], "schedules": d["schedules"],
                "timezone": os.getenv("HUB_BACKUP_TZ", "America/Sao_Paulo")}  # fmt: skip

    @api.put("/backups/policies/{app_id}")
    async def put_policy(app_id: str, request: Request) -> Any:  # noqa: PLR0915
        need_app(app_id)
        p = await body(request)
        schedule, dest = str(p.get("schedule", "")).strip(), str(p.get("destination", "")).strip()
        if not schedule or len(schedule) > 60:
            raise OpsError("Informe a agenda (até 60 caracteres).")
        normalize(schedule)
        keep = p.get("keep")
        if not isinstance(keep, int) or not 1 <= keep <= 90:
            raise OpsError("Retenção deve manter entre 1 e 90 backups.")
        vols = p.get("volumes")
        if (not isinstance(vols, list) or not 1 <= len(vols) <= 8
                or not all(isinstance(v, str) and 0 < len(v) <= 32 and all(c in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in v) for v in vols)):  # fmt: skip
            raise OpsError("Volumes inválidos: use de 1 a 8 nomes simples (ex.: data, uploads).")
        if not dest or len(dest) > 80:
            raise OpsError("Informe o destino (até 80 caracteres).")
        envs = p.get("envs") or ["producao"]
        if (
            not isinstance(envs, list)
            or not 1 <= len(envs) <= 2
            or not all(x in ("producao", "staging") for x in envs)
        ):
            raise OpsError("Ambientes: producao e/ou staging.")
        missed = p.get("missed") or "executar"
        if missed not in ("executar", "pular"):
            raise OpsError("Execução perdida: executar ou pular.")
        tz = str(p.get("tz") or "").strip()
        if tz:
            try:
                zoneinfo.ZoneInfo(tz)
            except (zoneinfo.ZoneInfoNotFoundError, ValueError) as ex:
                raise OpsError(f"Fuso horário desconhecido: {tz}.") from ex
        pol = {"app": app_id, "schedule": schedule, "keep": keep, "volumes": vols, "hooks": bool(p.get("hooks")),
               "destination": dest, "envs": envs, "missed": missed, "jitter_s": min(int(p.get("jitter_s") or 20), 3600),
               "tz": tz}  # fmt: skip
        async with store.locked(True) as d:
            d["policies"] = [x for x in d["policies"] if x["app"] != app_id] + [pol]
            d["schedules"] = {k: v for k, v in d["schedules"].items() if not k.startswith(f"{app_id}|")}
        await admin.audit(actor_of(request), "Política de backup alterada",
                          f"{name_of(app_id)} · {schedule} · últimos {keep} · volumes {', '.join(vols)}",
                          icon="clock", scope=app_id)  # fmt: skip
        return pol

    def maintenance_error(d: Data, app_id: str, env: str) -> str | None:
        mt = d["maintenance"].get(f"{app_id}|{env}")
        if not mt:
            return None
        lbl = (
            "Em manutenção (restauração falhou)"
            if mt["kind"] == "restauracao_falhou"
            else "Em manutenção (restauração)"
        )
        return f"{lbl}: {name_of(app_id)} bloqueado até a restauração terminar ou ser liberada em Backups."

    async def alert(level: str, title: str, detail: str, app_id: str, link: str = "/admin/backups") -> None:
        """Platform alert for the administrators (Inbox + SSE); at most one identical alert per hour."""
        app.state.inbox.push(item_of("alert", app_id, title, body=detail[:500], level=level, to_perm="editar_configuracoes",
                                     link=link, source=f"alert:{app_id}:{title}:{now_ms() // 3_600_000}"))  # fmt: skip

    app.state.platform_alert = alert

    async def set_maintenance(app_id: str, k: str, kind: str | None, detail: str) -> None:
        """Turns "Em manutenção (restauração)" (Under maintenance, restore) on or off for the app×environment (`None` = releases it)."""
        async with store.locked(True) as dd:
            if kind is None:
                dd["maintenance"].pop(f"{app_id}|{k}", None)
            else:
                dd["maintenance"][f"{app_id}|{k}"] = {"kind": kind, "since": now_ms(), "detail": detail[:300],
                                                      "replica": app.state.leader.replica}  # fmt: skip

    async def safety_snapshot(app_id: str, k: str, phase: Callable[[int, str], None]) -> str:
        """Backup "Pré-restauração" (Pre-restore) of the current state (restorable like any other); a failure becomes a note, not an abort."""
        if os.getenv("HUB_RESTORE_SAFETY_SNAPSHOT", "1").strip() == "0":
            return ""
        pol = next((p for p in store.read()["policies"] if p["app"] == app_id), None)
        phase(5, "Snapshot de segurança (Pré-restauração)")
        t0 = now_ms()
        try:
            name, sha, man = await backups.create_bundle(
                app_id, k, pol["volumes"] if pol else ["data"], False, phase
            )
        except Exception as e:
            return (
                f" · sem snapshot de segurança: {e.message if isinstance(e, OpsError) else type(e).__name__}"
            )
        async with store.locked(True) as dd:
            rid = dd["next_backup_id"]
            dd["next_backup_id"] += 1
            dd["backups"].insert(0, {"id": rid, "app": app_id, "kind": "Pré-restauração", "size": man["size"], "status": "Concluído",
                                     "at": t0, "file": name, "sha256": sha, "files": man["count"], "duration_ms": now_ms() - t0,
                                     "detail": f"{man['summary']} · {man['consistency']}", "source": None, "env": k})  # fmt: skip
        return f" · snapshot Pré-restauração #{rid}"

    async def start_backup(app_id: str, k: str, kind: str, actor: str) -> dict[str, Any]:  # noqa: C901, PLR0915
        """Creates the backup job and runs it in the background (manual or from the scheduler); `OpsError` if it cannot start."""
        need_app(app_id)
        d = store.read()
        pol = next((p for p in d["policies"] if p["app"] == app_id), None)
        if pol is None:
            raise OpsError("Aplicação sem política de backup.", 404)
        if err := maintenance_error(d, app_id, k):
            raise OpsError(err, 409)
        await jobs.claim(app_id)
        suffix = " (staging)" if k == "staging" else ""
        job = await jobs.create("backup", f"Backup de {name_of(app_id)}{suffix}", [], app_id, k)
        started = now_ms()
        async with store.locked(True) as dd:
            rid = dd["next_backup_id"]
            dd["next_backup_id"] += 1
            dd["backups"].insert(0, {"id": rid, "app": app_id, "kind": kind, "size": 0, "status": "Em andamento",
                                     "at": started, "file": None, "sha256": "", "files": 0, "duration_ms": 0,
                                     "detail": "", "source": None, "env": k})  # fmt: skip

        def phase(p: int, msg: str) -> None:
            jobs.update(job["id"], lambda j: j.update(progress=p, message=msg))

        async def go() -> None:  # noqa: PLR0915
            t0 = now_ms()
            res: tuple[str, str, dict[str, Any]] | None = None
            err = ""
            try:
                res = await backups.create_bundle(app_id, k, pol["volumes"], pol["hooks"], phase)
            except OpsError as e:
                err = e.message
            except Exception as e:
                err = f"{type(e).__name__}: {e}"[:300]
            off_note = ""
            if res:
                phase(90, "Cópia externa")
                try:
                    if dest := await backups.offsite(app_id, k, res[0]):
                        off_note = f" · cópia externa em {dest}"
                except OpsError as e:  # the local backup still counts; the failure becomes a note and alert
                    off_note = f" · cópia externa FALHOU: {e.message}"
                    await alert(
                        "error", f"Cópia externa do backup de {name_of(app_id)} falhou", e.message, app_id
                    )
            elapsed = now_ms() - t0
            pruned: list[str] = []
            async with store.locked(True) as dd:
                rec = next(x for x in dd["backups"] if x["id"] == rid)
                rec["duration_ms"] = elapsed
                if res:
                    name, sha, man = res
                    rec.update(status="Concluído", size=man["size"], file=name, sha256=sha, files=man["count"],
                               detail=f"{man['summary']} · {man['consistency']}{off_note}")  # fmt: skip
                    pruned = backups.prune(dd, app_id, k, pol["keep"])
                else:
                    rec.update(status="Falhou", detail=err)
            ok = res is not None
            title = "Backup concluído" if ok else "Backup falhou"
            detail = f"{name_of(app_id)}{suffix} · " + (
                f"{res[2]['summary']} · {m.human(res[2]['size'])} · sha256 {res[1][:12]}…{off_note}"
                if res
                else err
            )
            await admin.audit(actor, title, detail, icon="clock", scope=app_id)
            log("INFO" if ok else "ERROR", app_id, actor, f"backup: {detail}")
            if not ok:
                await alert("error", f"Backup de {name_of(app_id)}{suffix} falhou", err, app_id)
            jobs.busy.discard(app_id)
            msg = (f"Backup de {name_of(app_id)}{suffix} concluído ({m.human(res[2]['size'])})." if res
                   else f"Backup de {name_of(app_id)}{suffix} falhou: {err}")  # fmt: skip
            jobs.update(job["id"], lambda j: j.update(done=True, ok=ok, progress=100, message=msg,
                                                      result={"record": rid, "pruned": pruned}))  # fmt: skip

        spawn(go())
        return job

    app.state.ops_start_backup = start_backup

    @api.post("/backups/{app_id}/run")
    async def run_backup(app_id: str, request: Request, env: str | None = None) -> Response:
        need_app(app_id)
        return JSONResponse(
            await start_backup(app_id, env_key(env), "Sob demanda", actor_of(request)), status_code=202
        )

    async def known_dbs() -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        for a in registry.ids():
            for env in ("producao", "staging"):
                db, _ = parse_targets(await backups.app_env(a, env))
                if db and all(x[2] != db for x in out):
                    out.append((a, env, db))
        return out

    @api.get("/backups/aside")
    async def list_aside() -> Any:
        try:
            return {"available": True, "aside": await backups.list_aside(await known_dbs())}
        except OpsError as e:
            if e.status == 503:
                return {"available": False, "aside": []}
            raise

    @api.post("/backups/aside/{name}/{action}")
    async def aside_action(name: str, action: str, request: Request) -> Any:
        if action not in ("rename-back", "drop"):
            raise OpsError("Ação inválida.", 404)
        found = next((x for x in await backups.list_aside(await known_dbs()) if x["aside"] == name), None)
        if found is None:
            raise OpsError("Banco de lado não encontrado (ou restauração em curso).", 404)
        await jobs.claim(found["app"])
        try:
            a = await backups.aside_action(await known_dbs(), name, action)
        finally:
            jobs.busy.discard(found["app"])
        what = (f"banco de lado {name} apagado" if action == "drop"
                else f"banco de lado {name} devolvido ao nome {a['db']}")  # fmt: skip
        actor = actor_of(request)
        log("WARN", a["app"], actor, f"backups: {what}")
        await admin.audit(actor, "Banco de lado", what, icon="check", scope=a["app"])
        return {"done": True, "detail": what}

    @api.post("/backups/credentials/rotate")
    async def rotate_credentials(request: Request) -> Any:
        if admin.vault is None:
            raise OpsError(
                "Cofre desligado (HUB_VAULT=0): as credenciais de backup já são novas a cada backup.", 409
            )
        lines = await backups.rotate_all(registry.ids())
        failed = sum("falhou" in ln for ln in lines)
        await admin.audit(actor_of(request), "Credenciais de backup rotacionadas",
                          " · ".join(lines) or "nenhum banco/bucket de app com backup", icon="key")  # fmt: skip
        return {"rotated": len(lines) - failed, "failed": failed, "items": lines}

    @api.post("/backups/records/{rec_id}/{action}")
    async def backup_action(rec_id: int, action: str, request: Request) -> Response:  # noqa: C901, PLR0915
        d = store.read()
        rec = next((x for x in d["backups"] if x["id"] == rec_id), None)
        if rec is None:
            raise OpsError("Backup não encontrado.", 404)
        kind, verb, done_word = {"restore": ("Restauração", "Restauração", "concluída"),
                                 "clone": ("Clone p/ staging", "Clone para staging", "concluído"),
                                 "test": ("Teste de restore", "Teste de restore", "concluído")}.get(action, (None, None, None))  # fmt: skip
        if kind is None or verb is None or done_word is None:
            raise OpsError("Ação inválida.", 404)
        if rec["status"] != "Concluído" or not rec.get("file"):
            raise OpsError("Só backups concluídos (com arquivo disponível) podem ser usados.")
        app_id, k = rec["app"], env_key(rec.get("env"))
        if action == "clone" and k == "staging":
            raise OpsError("O clone parte de um backup de produção.")
        await jobs.claim(app_id)
        label = f"{name_of(app_id)}{' (staging)' if k == 'staging' else ''}"
        job = await jobs.create("restore", f"{verb} de {label}", [], app_id, None)
        actor = actor_of(request)

        def phase(p: int, msg: str) -> None:
            jobs.update(job["id"], lambda j: j.update(progress=p, message=msg))

        async def go() -> None:  # noqa: PLR0915
            t0 = now_ms()
            res: tuple[int, str] | None = None
            err, safety = "", ""
            real = action == "restore"
            if action == "clone":
                await set_maintenance(
                    app_id, "staging", "restauracao", f"clonando o backup #{rec['id']} para staging"
                )
            if real:
                await set_maintenance(app_id, k, "restauracao", f"restaurando o backup #{rec['id']}")
                safety = await safety_snapshot(app_id, k, phase)
            try:
                res = await backups.restore_bundle(app_id, k, rec["file"], rec["sha256"], action, phase)
            except OpsError as e:
                err = e.message
            except Exception as e:
                err = f"{type(e).__name__}: {e}"[:300]
            elapsed = now_ms() - t0
            ok = res is not None
            if action == "clone":
                await set_maintenance(app_id, "staging", None, "")
            if real:
                await set_maintenance(app_id, k, None if ok else "restauracao_falhou", err)
            when = log_stamp(rec["at"])
            detail = (
                f"{label} · backup de {when} · "
                + (f"{res[1]} · {elapsed / 1000:.1f} s" if res else err)
                + safety
            )
            if real and not ok:
                await alert("error", f"Restauração de {label} falhou", err, app_id)
            async with store.locked(True) as dd:
                rid = dd["next_backup_id"]
                dd["next_backup_id"] += 1
                dd["backups"].insert(0, {"id": rid, "app": app_id, "kind": kind, "size": rec["size"],
                                         "status": "Concluído" if ok else "Falhou", "at": now_ms(), "file": None,
                                         "sha256": rec["sha256"], "files": res[0] if res else 0, "duration_ms": elapsed,
                                         "detail": res[1] if res else err, "source": rec["id"], "env": rec.get("env", "")})  # fmt: skip
            await admin.audit(
                actor, f"{verb} {done_word if ok else 'falhou'}", detail, icon="clock", scope=app_id
            )
            log("INFO" if ok else "ERROR", app_id, actor, f"{verb.lower()}: {detail}")
            jobs.busy.discard(app_id)
            msg = f"{verb} de {label} {done_word} — {res[1]}." if res else f"{verb} de {label} falhou: {err}"
            jobs.update(
                job["id"],
                lambda j: j.update(
                    done=True, ok=ok, progress=100, message=msg, result={"duration_ms": elapsed}
                ),
            )

        spawn(go())
        return JSONResponse(job, status_code=202)

    @api.post("/maintenance/{app_id}/clear")
    async def clear_maintenance(app_id: str, request: Request, env: str | None = None) -> Any:
        need_app(app_id)
        k = env_key(env)
        async with store.locked(True) as d:
            if d["maintenance"].pop(f"{app_id}|{k}", None) is None:
                raise OpsError("O app não está em manutenção.", 409)
        await admin.audit(
            actor_of(request), "Manutenção liberada", f"{name_of(app_id)} ({k})", icon="check", scope=app_id
        )
        return {"cleared": True}
