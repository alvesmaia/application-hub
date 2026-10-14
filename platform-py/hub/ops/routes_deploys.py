"""Operations API: package upload, validation pipeline, deployments (approve, promote, rollback) and build."""

from __future__ import annotations

import json
import os
import shutil
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from hub.ops import model as m
from hub.ops import newapp
from hub.ops import pipeline as pl
from hub.ops import provision as pv
from hub.ops import runtime as rt
from hub.ops.model import Data, OpsError, now_ms
from hub.ops.opsctx import OpsCtx, rollout_env


def mount_deploys(  # noqa: C901, PLR0915 closure mount moved from mount_ops; TASKS.md task 3
    c: OpsCtx,
) -> None:
    (
        app,
        client,
        data_dir,
        admin,
        registry,
        store,
        eng,
        jobs,
        backups,
        rollout,
        api,
        name_of,
        spawn,
        actor_of,
        log,
    ) = (
        c.app,
        c.client,
        c.data_dir,
        c.admin,
        c.registry,
        c.store,
        c.eng,
        c.jobs,
        c.backups,
        c.rollout,
        c.api,
        c.name_of,
        c.spawn,
        c.actor_of,
        c.log,
    )

    # ---- Deployments ------------------------------------------------------------------------------
    async def run_import(  # noqa: C901, PLR0915
        job_id: int,
        data: bytes,
        env: str,
        origin: str,
        commit: str,
        file_name: str,
        actor: str,
        uid: str,
        config: dict[str, dict[str, str]] | None = None,
    ) -> None:
        async def probe(url: str) -> int:
            return (await client.get(url, timeout=5)).status_code

        def on_step(steps: list[dict[str, Any]]) -> None:
            jobs.update(job_id, lambda j: j.update(steps=json.loads(json.dumps(steps))))

        # new app still without a production container: no upstream smoke test (the candidate proves readiness)
        regs = [
            e
            for e in registry.entries()
            if not (
                newapp.is_dynamic(data_dir, e.id)
                and await eng.raw(rt.container_name(e.id, "producao")) is None
            )
        ]
        report = await pl.run_all(data, env, regs, probe, on_step=on_step,
                                  allow_without_api=os.getenv("HUB_ALLOW_WITHOUT_API") == "1")  # fmt: skip
        for st in report["steps"]:
            if st["state"] in ("ok", "warn", "failed"):
                log("ERROR" if st["state"] == "failed" else "INFO", report["app"] or "platform", actor,
                    f"pipeline · {st['name']}: {st['detail']}")  # fmt: skip
        provisioned: dict[str, Any] = {}
        if report["passed"] and report["app"] and report.get("manifest"):
            mf = report["manifest"]
            try:  # F22: new app joins the registry; `requires` provisioned before the deployment
                if newapp.register(data_dir, registry, admin.cfg.apps, mf):
                    log("INFO", report["app"], actor, "app novo registrado pela implantação")

                async def db(a: str, e: str) -> dict[str, Any]:
                    return await pv.provision(backups, admin.cfg, actor, a, e)

                provisioned = await newapp.apply_requires(report["app"], env, mf, config or {}, actor, admin.cfg, db,
                                                          getattr(app.state, "storage_provision", None))  # fmt: skip
            except (OpsError, ValueError) as ex:
                msg_ = ex.message if isinstance(ex, OpsError) else str(ex)[:200]
                report.update(passed=False, reason=f"Provisionamento: {msg_}")
        report["provisioned"] = provisioned
        sha = pl.hashlib.sha256(data).hexdigest()
        package = None
        if report["passed"]:
            package = f"{report['app']}-{report['version']}-{sha[:12]}.hubapp"
            (data_dir / "packages").mkdir(parents=True, exist_ok=True)
            (data_dir / "packages" / package).write_bytes(data)
        auto = report["passed"] and env == "staging"  # staging is approved by the pipeline itself (step 7)
        status = m.REJEITADO if not report["passed"] else m.IMPLANTANDO if auto else m.AGUARDANDO
        label = m.STAGING if env == "staging" else m.PRODUCAO
        note = report["reason"]
        dep = {"id": 0, "app": report["app"] or "desconhecido", "version": report["version"] or "—", "env": label,
               "origin": origin, "commit": commit, "status": status, "at": now_ms(), "image": report["image"],
               "package": package, "sha256": sha, "note": note, "runtime": "", "scripts": report["scripts"],
               "build": bool(report.get("build")),
               "submitted_by": actor, "submitted_by_id": uid}  # fmt: skip
        if report["passed"] and report["app"]:
            await ensure_baseline(
                report["app"], "producao"
            )  # before the `push`: the compose version is the oldest (rollback)
        async with store.locked(True) as d:
            saved = m.push(d, dep)
        title = {m.REJEITADO: "Pacote rejeitado pelo pipeline de validação",
                 m.AGUARDANDO: "Pacote validado — aguardando aprovação",
                 m.IMPLANTANDO: "Pacote validado — implantando em staging"}[status]  # fmt: skip
        detail = (f"{name_of(saved['app'])} {saved['version']} · {label} · {origin} · {file_name}"
                  + (f" · {report['reason']}" if not report["passed"] else ""))  # fmt: skip
        await admin.audit(
            actor,
            title,
            detail,
            icon="file",
            scope=saved["app"] if registry.get(saved["app"]) else "platform",
        )
        msg = (f"Pacote rejeitado — {report['reason']}" if not report["passed"] else
               "Pacote validado — aguardando aprovação para produção.")  # fmt: skip
        good = report["passed"]
        if auto:
            good, msg, saved = await run_deploy(saved["id"], job_id, actor)

        def fin(j: dict[str, Any]) -> None:
            st = (j.get("steps") or [{}] * 6)[5]
            if st.get("state") == "running":  # build/candidate step in staging
                st.update(state="ok" if good else "failed",
                          detail=(f"Imagem {saved['image']} pronta e candidato respondeu à prontidão em staging"
                                  if good else msg))  # fmt: skip
            j.update(done=True, ok=good, message=msg, progress=100,
                     result={"deployment": saved, "report": report}, provisioned=report["provisioned"])  # fmt: skip

        jobs.update(job_id, fin)

    async def ensure_baseline(app_id: str, k: str) -> None:
        """First deployment of an app: records the version already running (compose), so rollback has somewhere to go back to."""
        raw = await eng.raw(rt.container_name(app_id, k))
        e = registry.get(app_id)
        if raw is None or e is None or k != "producao":
            return
        await eng.pin(
            str(raw.get("Image", "")), app_id, f"baseline-{str(raw.get('Image', ''))[7:19]}"
        )  # rollback
        async with store.locked(True) as d:
            if any(
                x["app"] == app_id and x["env"] == m.PRODUCAO and x["status"] == m.IMPLANTADO
                for x in d["deployments"]
            ):
                return
            base = m.push(d, {"id": 0, "app": app_id, "version": e.version or "—", "env": m.PRODUCAO, "origin": "Compose",
                       "commit": "—", "status": m.IMPLANTADO, "at": now_ms(), "package": None, "sha256": "",
                       "image": f"{str(raw['Config'].get('Image', 'imagem')).split(':')[0]}@{raw['Image']}",
                       "note": "versão em execução antes da primeira implantação", "runtime": "", "scripts": [],
                       "submitted_by": "", "submitted_by_id": ""})  # fmt: skip
            d["deployments"].append(
                d["deployments"].pop(d["deployments"].index(base))
            )  # the oldest one stays last

    async def build_image(dep: Data, job_id: int, actor: str) -> None:  # noqa: PLR0915
        """Builds the package's image (`build/**`) on the daemon, with a memory cap; the log goes to the job, to the logs console and
        to the deployment record. The image of an already existing version (rollback, resubmission) is not rebuilt."""
        tag, app_id = dep["image"], dep["app"]
        if await eng.has_image(tag) and dep.get("origin") == "Rollback":
            return
        if not dep.get("package"):
            raise OpsError("Pacote da implantação não está mais no hub: reenvie-o.", 422)
        files = pl.read_zip((data_dir / "packages" / dep["package"]).read_bytes())
        spec = json.loads(files["runtime.json"]).get("build") or {}
        lines: list[str] = []

        def on_line(ln: str) -> None:
            lines.append(ln[:300])
            log("INFO", app_id, actor, f"build · {ln[:300]}")

            def up(j: dict[str, Any]) -> None:
                j.update(build_log=lines[-200:], message=f"Construindo a imagem · {ln[:120]}")
                if len(j.get("steps") or []) > 5:
                    j["steps"][5].update(state="running", detail=f"Construindo a imagem {tag} · {ln[:120]}")

            jobs.update(job_id, up)

        jobs.update(job_id, lambda j: j.update(progress=2, message=f"Construindo a imagem {tag}"))
        tmp = data_dir / "build" / f"{app_id}-{dep['id']}"
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            for n, b in pl.build_context(files, str(spec.get("dockerfile", "Dockerfile"))).items():
                if n.startswith("build/"):
                    f = tmp / n.removeprefix("build/")
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(b)
            mem = rt.memory_bytes(os.getenv("HUB_BUILD_MEMORY", "1Gi"))
            await eng.build(
                str(tmp), tag, str(spec.get("dockerfile", "Dockerfile")), mem, on_line,
                int(os.getenv("HUB_BUILD_CPUS", "2") or 0), float(os.getenv("HUB_BUILD_TIMEOUT_S", "900") or 0),
            )  # fmt: skip
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            async with store.locked(True) as d:
                m._find(d, dep["id"])["build_log"] = lines[-200:]
        log("INFO", app_id, actor, f"build concluído · {tag} ({len(lines)} linhas)")

    async def run_deploy(dep_id: int, job_id: int, actor: str) -> tuple[bool, str, Data]:  # noqa: PLR0915
        """Blue/green of the `Implantando` (Deploying) deployment; returns (ok, message, record). The active one only leaves after the candidate is ready."""
        dep = m._find(store.read(), dep_id)
        app_id, k, label = dep["app"], rollout_env(dep["env"]), dep["env"]
        e = registry.get(app_id)
        err, res = "", {}

        def phase(p: int, msg: str) -> None:
            jobs.update(job_id, lambda j: j.update(progress=p, message=msg))

        if e is None:
            err = "Aplicação não encontrada no registro."
        else:
            try:
                await ensure_baseline(app_id, k)
                lim = rt.resources_of(e, store.read()["limits"])
                if dep.get("build"):
                    await build_image(dep, job_id, actor)
                res = await rollout.apply(app_id, k, lim, dep["version"], dep["image"] or None, phase)
            except OpsError as ex:
                err = ex.message
            except Exception as ex:
                err = f"{type(ex).__name__}: {ex}"[:300]
        async with store.locked(True) as d:
            dep = m._find(d, dep_id)
            if err:
                dep.update(status=m.FALHOU, note=err)
            else:
                for x in d["deployments"]:
                    if (
                        x is not dep
                        and x["app"] == app_id
                        and x["env"] == dep["env"]
                        and x["status"] == m.IMPLANTADO
                    ):
                        x["status"] = m.SUBSTITUIDO
                dep.update(status=m.IMPLANTADO, runtime=f"container {res['container']}", at=now_ms())
            saved = dict(dep)
        what = f"{name_of(app_id)} {saved['version']} · {label}"
        await admin.audit(actor, "Implantação falhou" if err else "Versão implantada", f"{what}" + (f" · {err}" if err else ""),
                          icon="alert" if err else "check", scope=app_id)  # fmt: skip
        log(
            "ERROR" if err else "INFO",
            app_id,
            actor,
            f"implantação {what}: {err or 'concluída (blue/green)'}",
        )
        if not err and k == "producao":
            await admin.cfg.clear_pending(app_id)
        return (
            (not err),
            (
                f"Implantação falhou — {err}"
                if err
                else f"{what} implantada (blue/green, sem indisponibilidade)."
            ),
            saved,
        )

    @api.post("/packages")
    async def upload(request: Request) -> Response:
        form = await request.form()
        up = form.get("package")
        if up is None or isinstance(up, str):
            raise OpsError("Selecione um arquivo .hubapp (campo package).")
        data = await up.read()
        if not data or len(data) > pl.MAX_PACKAGE_BYTES:
            raise OpsError(
                f"O pacote tem {len(data) / 1024 / 1024:.1f} MB; o limite é "
                f"{pl.MAX_PACKAGE_BYTES // 1024 // 1024} MB. Dependências entram no build, não no pacote: "
                "não copie .venv/ nem vendor/ para build/."
                if data
                else "Selecione um arquivo .hubapp (campo package)."
            )
        raw_env = request.query_params.get("env") or str(form.get("env") or "")
        env = {"staging": "staging", "producao": "producao", "produção": "producao"}.get(raw_env.lower())
        if env is None:
            raise OpsError("Ambiente inválido: use staging ou produção.")
        commit = request.query_params.get("commit")
        if commit is not None and not (
            7 <= len(commit) <= 40 and all(c in "0123456789abcdefABCDEF" for c in commit)
        ):
            raise OpsError("Commit inválido: informe o SHA (7 a 40 hex).")
        file_name = "".join(c for c in (up.filename or "pacote.hubapp") if c.isprintable())[:120]
        ci = request.query_params.get("origin") == "ci"
        origin = "GitHub Actions" if ci else "Import manual"
        peek = pl.peek_app_id(data)
        job = await jobs.create("deploy", file_name, [pl.step(i) for i in range(len(pl.STEPS))], peek, env)
        actor = actor_of(request)
        log("INFO", "platform", actor, f"pacote {file_name} recebido ({-(-len(data) // 1024)} KB) · "
            f"{'Staging' if env == 'staging' else 'Produção'} · {origin}")  # fmt: skip
        coro = run_import(job["id"], data, env, origin, (commit or "—")[:12].lower(), file_name, actor,
                          str(request.state.user["id"]), newapp.parse_config(str(form.get("config") or "")))  # fmt: skip
        if request.query_params.get("wait") == "true":
            await coro
            return JSONResponse(job, status_code=201 if job["ok"] else 422)
        spawn(coro)
        return JSONResponse(job, status_code=202)

    @api.post("/manifest/validate")
    async def validate_manifest(request: Request) -> Any:
        raw = await request.body()
        if len(raw) > 64 * 1024:
            raise OpsError("Manifest maior que 64 KB.")
        mf, detail, ok = pl._manifest(raw.decode(errors="replace"), registry.entries())
        if not ok or mf is None:
            return {"valid": False, "error": detail}
        mf["resources"] = mf.get("resources") or {"cpu": 0.5, "memory": "256Mi", "pids": 100}
        mf.setdefault("version", "")
        return {
            "valid": True,
            "error": None,
            "manifest": mf,
            "registered": registry.get(mf["id"]) is not None,
        }

    @api.get("/replicas")
    async def replicas() -> Any:
        """Live replicas, who leads, and how many rounds of each leader task this replica ran."""
        return await app.state.leader.info()

    @api.get("/jobs/{job_id}")
    async def get_job(job_id: int) -> Any:
        j = await jobs.aget(job_id)
        if j is None:
            raise OpsError("Job não encontrado.", 404)
        return j

    @api.get("/deployments")
    async def list_deployments() -> Any:
        return store.read()["deployments"]

    @api.post("/deployments/{dep_id}/scripts/approve")
    async def approve_scripts(dep_id: int, request: Request) -> Any:
        actor, uid = actor_of(request), str(request.state.user["id"])
        allow = os.getenv("HUB_SCRIPTS_ALLOW_SELF_APPROVAL") == "1"
        async with store.locked(True) as d:
            try:
                lst = m.approve_scripts(d, dep_id, actor, uid, bool(request.state.token), now_ms(), allow)
            except OpsError as e:
                dep = next((x for x in d["deployments"] if x["id"] == dep_id), None)
                if dep:
                    await admin.audit(actor, "Aprovação de JavaScript próprio recusada",
                                      f"{dep['app']} {dep['version']} · {e.message}", icon="alert", scope=dep["app"])  # fmt: skip
                raise
            dep = m._find(d, dep_id)
        detail = f"{name_of(dep['app'])} {dep['version']} · {dep['env']} · " + "; ".join(
            f"{r['path']} {r['sha384']} — {r['reason']}" for r in lst
        )
        await admin.audit(
            actor,
            "JavaScript próprio aprovado (revisão de segurança)",
            detail,
            icon="check",
            scope=dep["app"],
        )
        log("WARN", dep["app"], actor, f"JavaScript próprio aprovado · {dep['app']} {dep['version']} · "
            + ", ".join(r["path"] for r in lst))  # fmt: skip
        return {"deployment": dep, "approved": lst}

    @api.post("/deployments/{dep_id}/{action}")
    async def deployment_action(dep_id: int, action: str, request: Request) -> Response:
        if action not in ("approve", "promote", "rollback"):
            raise OpsError("Ação inválida.", 404)
        now = now_ms()
        src: Data = {}
        async with store.locked(True) as d:
            if action == "promote":
                src = dict(m._find(d, dep_id))
                dep = m.promote(d, dep_id, now)
            elif action == "approve":
                dep = m.approve(d, dep_id)
            else:
                dep = m.rollback(d, dep_id, now)
        actor = actor_of(request)
        title = {"promote": "Promoção", "approve": "Aprovação", "rollback": "Rollback"}[action]
        job = await jobs.create(
            "deployment",
            f"{title} · {name_of(dep['app'])} {dep['version']} ({dep['env']})",
            [],
            dep["app"],
            "producao",
        )
        if action == "promote":
            await admin.audit(actor, "Versão promovida para produção",
                              f"{name_of(dep['app'])} {dep['version']} · aguardando aprovação (de #{src['id']})",
                              icon="check", scope=dep["app"])  # fmt: skip
            job.update(done=True, ok=True, progress=100, result={"deployment": dep},
                       message=f"{title} registrada — aguardando aprovação para produção.")  # fmt: skip
            return (
                JSONResponse(dep)
                if request.query_params.get("wait") == "true"
                else JSONResponse(job, status_code=202)
            )
        await admin.audit(actor, "Implantação aprovada" if action == "approve" else "Rollback solicitado",
                          f"{name_of(dep['app'])} {dep['version']} · {dep['env']}", icon="check", scope=dep["app"])  # fmt: skip

        async def go() -> Data:
            ok, msg, saved = await run_deploy(dep["id"], job["id"], actor)
            jobs.update(job["id"], lambda j: j.update(done=True, ok=ok, progress=100, message=msg,
                                                      result={"deployment": saved}))  # fmt: skip
            return saved

        if request.query_params.get("wait") == "true":
            saved = await go()
            return JSONResponse(saved, status_code=200 if saved["status"] == m.IMPLANTADO else 422)
        spawn(go())
        return JSONResponse(job, status_code=202)
