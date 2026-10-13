"""Páginas (`/admin/{deploys,runtime,tasks,backups}`) e API `/api/ops/*` de Operações (F3c).

F5b: blue/green (`rollout.py`), staging, supervisão (`supervisor.py`) e provisionamento de banco (`provision.py`)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import zoneinfo
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, cast

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from hub.admin.config import injected_name, resolve
from hub.admin.routes import bound_summary, relative
from hub.admin.store import AdminStore, log_stamp
from hub.connections.provision import ObjectStore
from hub.gateway.csp import set_script_provider
from hub.gateway.registry import Registry
from hub.ops import jobs as jb
from hub.ops import model as m
from hub.ops import newapp
from hub.ops import pipeline as pl
from hub.ops import provision as pv
from hub.ops import runtime as rt
from hub.ops.backups import Backups, parse_targets
from hub.ops.devkit import mount_devkit
from hub.ops.jobs import Jobs
from hub.ops.leader import Leader
from hub.ops.model import Data, OpsError, OpsStore, now_ms
from hub.ops.rollout import Rollout
from hub.ops.schedule import local_label, normalize, timezone_of
from hub.ux.inbox import item_of
from hub.ux.pager import paginate
from hub.ux.texts import page_response, tr

CI_SNIPPET = """- uses: actions/checkout@v4
- id: push
  run: docker build -t ghcr.io/org/minha-app:${{ github.sha }} . && docker push …
- run: hub pack --dir . --image ghcr.io/org/minha-app@${{ steps.push.outputs.digest }} --out app.hubapp
- run: |
    curl -fsS -H "Authorization: Bearer $HUB_TOKEN" -H "X-Hub-Request: 1" \\
      -F package=@app.hubapp \\
      "$HUB_URL/api/ops/packages?env=staging&origin=ci&commit=${{ github.sha }}&wait=true"
  env:
    HUB_URL: ${{ vars.HUB_URL }}
    HUB_TOKEN: ${{ secrets.HUB_TOKEN }}  # Acesso › Credenciais"""
ROLLOUT = "Implantação blue/green ativa: o candidato sobe com a imagem do pacote, só recebe tráfego depois de GET /{app}/_hub/ready (sem a rota, é recusado) e o container anterior sai depois de esgotar as requisições. Rollback volta à versão anterior pelo mesmo caminho."
TASK_STATUSES = [("queued", "Na fila"), ("running", "Em execução"), ("done", "Concluídas"), ("failed", "Falharam"),
                 ("dead", "Tentativas esgotadas"), ("cancelled", "Canceladas")]  # fmt: skip
SAGA = {"running": "em andamento", "compensating": "compensando", "completed": "concluída", "compensated": "compensada",
        "failed": "falhou (intervenção manual)"}  # fmt: skip
SAGA_STEP = {"pending": "pendente", "done": "feito", "attempted": "falhou", "compensating": "desfazendo",
             "compensated": "desfeito", "failed": "erro ao desfazer"}  # fmt: skip


MANIFEST_EXAMPLE = """{
  "id": "fornecedores",
  "name": "Rede de Fornecedores",
  "version": "1.8.0",
  "mode": "fragment",
  "routes": ["/fornecedores/*"],
  "resources": { "cpu": 1.0, "memory": "1Gi", "pids": 100 },
  "env": ["API_BASE_URL", "SMTP_PASSWORD"],
  "permissions": ["fornecedores.read"],
  "sdk": "1.0",
  "jobs": [{ "name": "fornecedores.reprocessar", "max_duration_s": 600, "max_attempts": 3 }]
}"""

SDK_CMDS = [
    ("hub dev --dir . --upstream http://127.0.0.1:3000", "Shell local (porta 4173) + proxy para o app nativo, identidade simulada (--role admin)"),
    ("hub pack --dir . --image registro/app@sha256:…", "Valida o manifest, exige digest e gera o .hubapp com checksums.txt"),
    ('curl -H "Authorization: Bearer $HUB_TOKEN" -H "X-Hub-Request: 1" -F package=@app.hubapp …/api/ops/packages', "Publica (UI ou CI) e roda o pipeline de validação; ?wait=true aguarda o resultado"),
    ("POST /api/ops/deployments/{id}/promote · /rollback", "Promove a mesma versão ou volta à anterior (pela tela de Implantações)"),
    ("POST /api/ops/backups/{app}/run", "Backup sob demanda; restore, clone e teste pelo histórico"),
    ("hub dev --limits · --container · hub publish", "Planejado: limites locais, execução em container e publicação pela CLI"),
]  # fmt: skip


def tone(s: str) -> str:
    def has(xs: list[str]) -> bool:
        return any(x in s for x in xs)

    if has(["Implantado", "Concluído", "Aprovado", "Em execução", "Válido", "Concluída"]):
        return "status-pill ops-pill-ok"
    if has(["Validando", "Implantando", "Em andamento", "Aguardando", "Na fila", "Cancelando"]):
        return "status-pill ops-pill-warn"
    if has(["Rejeitado", "Falhou", "Parado", "Tentativas esgotadas"]):
        return "status-pill ops-pill-err"
    return "status-pill ops-pill-muted"


def br(v: float, places: int) -> str:
    return f"{v:.{places}f}".replace(".", ",")


def env_key(v: str | None) -> str:
    return "staging" if v == "staging" else "producao"


def rollout_env(label: str) -> str:
    return "staging" if label in (m.STAGING, "staging") else "producao"


def mount_ops(
    app: FastAPI,
    templates: Jinja2Templates,
    page_ctx: Callable[[Request], dict[str, Any]],
    icon: Callable[[str], str],
    client: httpx.AsyncClient,
    data_dir: Path,
    *,
    engine: rt.Engine | None = None,
    objstore: ObjectStore | None = None,
) -> OpsStore:
    admin = cast(AdminStore, app.state.admin)
    registry = cast(Registry, app.state.gateway.registry)
    store = OpsStore(data_dir, registry.ids(), admin.vault)
    eng, jobs = engine or rt.Engine(), Jobs()
    tasks: set[asyncio.Task[Any]] = set()
    app.state.ops, app.state.ops_jobs, app.state.ops_engine = store, jobs, eng

    def active_scripts(app_id: str, staging: bool) -> list[tuple[str, str]]:
        """Scripts próprios aprovados da versão ATIVA do app no ambiente: só esses entram na CSP e no `hub.js`."""
        d, env = store.read(), m.STAGING if staging else m.PRODUCAO
        dep = next(
            (
                x
                for x in d["deployments"]
                if x["app"] == app_id and x["env"] == env and x["status"] == m.IMPLANTADO
            ),
            None,
        )
        if dep is None:
            return []
        pending = {s["path"] for s in m.pending_scripts(d, dep)}
        return [
            (s["path"].removeprefix("ui/assets/"), s["sha384"])
            for s in dep.get("scripts", [])
            if s["path"] not in pending
        ]

    set_script_provider(active_scripts)
    app.state.leader = Leader(None)  # troca pela do `lifespan` (Background.start)

    async def app_env(app_id: str, env: str) -> dict[str, str]:
        data = await admin.cfg.read()
        return {r["name"]: r["value"] for r in resolve(data["env"], app_id, env)}

    async def hook(app_id: str, which: str) -> None:
        e = registry.get(app_id)
        path = (((e.manifest.model_extra or {}).get("backup") or {}) if e and e.manifest else {}).get(which)
        token = admin.tokens.get(app_id)
        if e and path and token:
            r = await client.post(
                f"{e.upstream.rstrip('/')}{path}", headers={jb.TOKEN_HEADER: token}, timeout=30
            )
            if r.status_code >= 300:
                raise OpsError(f"hook {which} do app respondeu {r.status_code}", 502)

    backups = Backups(store, eng, objstore or ObjectStore(), data_dir, app_env, hook)
    app.state.ops_backups = backups

    async def env_vars() -> list[dict[str, Any]]:
        return list((await admin.cfg.read())["env"])

    rollout = Rollout(eng, registry, client, app_env, env_vars)
    rollout.fresh = lambda a: newapp.base_container() if newapp.is_dynamic(data_dir, a) else None
    app.state.ops_rollout = rollout

    async def reconcile() -> None:
        """Ao subir: implantação deixada em andamento (réplica morta há >10 min) vira falha."""
        async with store.locked(True) as d:
            for x in d["deployments"]:
                if x["status"] == m.IMPLANTANDO and now_ms() - x["at"] > 600_000:
                    x.update(status=m.FALHOU, note="interrompida: o hub reiniciou durante a implantação")

    app.state.ops_reconcile = reconcile

    def name_of(a: str) -> str:
        e = registry.get(a)
        return e.name if e else a

    def spawn(coro: Any) -> None:
        t = asyncio.ensure_future(coro)
        tasks.add(t)
        t.add_done_callback(
            lambda f: (
                f.cancelled()
                or f.exception() is None
                or print("ops: tarefa falhou:", repr(f.exception()), flush=True)
            )
        )
        t.add_done_callback(tasks.discard)

    @app.exception_handler(OpsError)
    async def ops_error(request: Request, e: OpsError) -> JSONResponse:
        msg = tr(e.message, getattr(request.state, "lang", "pt-BR"))
        return JSONResponse({"error": msg}, status_code=e.status, headers={"cache-control": "no-store"})

    def actor_of(r: Request) -> str:
        return str(r.state.user["name"])

    def log(level: str, app_id: str, actor: str, msg: str) -> None:
        admin.logs.add(level, app_id, actor, msg, "ops")

    def need_app(a: str) -> Any:
        e = registry.get(a)
        if e is None:
            raise OpsError("Aplicação não encontrada.", 404)
        return e

    async def guard(request: Request) -> None:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return
        ct = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if request.url.path == "/api/ops/packages":
            if ct != "multipart/form-data":
                raise OpsError("Envie o pacote como multipart/form-data (campo package).", 415)
            if request.headers.get("x-hub-request") != "1":
                raise OpsError("Header X-Hub-Request: 1 obrigatório no envio de pacotes.")
        elif ct != "application/json":
            raise OpsError("Envie os dados como application/json.", 415)

    api = APIRouter(prefix="/api/ops", dependencies=[Depends(guard)])

    @app.middleware("http")
    async def no_store(request: Request, call_next: Callable[[Request], Any]) -> Response:
        resp = cast(Response, await call_next(request))
        if request.url.path.startswith("/api/ops/"):
            resp.headers["cache-control"] = "no-store"
        return resp

    async def body(request: Request) -> dict[str, Any]:
        try:
            v = await request.json()
        except ValueError as e:
            raise OpsError("Dados inválidos: verifique os campos enviados.") from e
        if not isinstance(v, dict):
            raise OpsError("Dados inválidos: verifique os campos enviados.")
        return v

    # ---- Implantações --------------------------------------------------------------------------------
    async def run_import(
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

        # app novo ainda sem container de produção: sem smoke test de upstream (o candidato prova a prontidão)
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
            try:  # F22: app novo entra no registro; `requires` provisionado antes da implantação
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
        auto = report["passed"] and env == "staging"  # staging é aprovado pelo próprio pipeline (passo 7)
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
            )  # antes do `push`: a versão do compose é a mais antiga (rollback)
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
            if st.get("state") == "running":  # passo do build/candidato em staging
                st.update(state="ok" if good else "failed",
                          detail=(f"Imagem {saved['image']} pronta e candidato respondeu à prontidão em staging"
                                  if good else msg))  # fmt: skip
            j.update(done=True, ok=good, message=msg, progress=100,
                     result={"deployment": saved, "report": report}, provisioned=report["provisioned"])  # fmt: skip

        jobs.update(job_id, fin)

    async def ensure_baseline(app_id: str, k: str) -> None:
        """Primeira implantação de um app: registra a versão que já roda (compose), para o rollback ter a quem voltar."""
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
            )  # a mais antiga fica por último

    async def build_image(dep: Data, job_id: int, actor: str) -> None:
        """Constrói a imagem do pacote (`build/**`) no daemon, com teto de memória; o log vai ao job, ao console de
        logs e ao registro da implantação. Imagem da versão já existente (rollback, reenvio) não é refeita."""
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

    async def run_deploy(dep_id: int, job_id: int, actor: str) -> tuple[bool, str, Data]:
        """Blue/green da implantação `Implantando`; devolve (ok, mensagem, registro). O ativo só sai depois do candidato pronto."""
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
        """Réplicas vivas, quem lidera e quantas rodadas de cada tarefa do líder esta réplica executou."""
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

    # ---- Containers ----------------------------------------------------------------------------------
    def runtime_rows(
        data: Data, live: dict[str, Any], pending: dict[str, Any], bound: dict[str, list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        rows = []
        names = {x["name"] for x in live["live"]}
        for e, env in [(e, v) for e in registry.entries() for v in ("producao", "staging")
                       if v == "producao" or rt.container_name(e.id, v) in names]:  # fmt: skip
            r = rt.resources_of(e, data["limits"])
            lab = m.STAGING if env == "staging" else m.PRODUCAO
            dep = next((x for x in data["deployments"] if x["app"] == e.id and x["env"] == lab
                        and x["status"] == m.IMPLANTADO and x["image"]), None)  # fmt: skip
            sup = getattr(app.state, "ops_supervisor", None)
            rows.append({
                "id": e.id, "name": e.name, "env": env, "container": rt.container_name(e.id, env),
                "image": dep["image"] if dep else "sem imagem implantada", "cpu": float(r["cpu"]),
                "memory": r["memory"], "memory_bytes": rt.memory_bytes(str(r["memory"])), "pids": int(r["pids"]),
                "edited": e.id in data["limits"], "network": f"hub-app-{e.id}", "extra_networks": [],
                "env_vars": [bound_summary(v, e.id) for v in bound.get(e.id, []) if any(
                    b["app"] == e.id and b["env"] == env for b in v["bindings"])],
                "restart_pending": e.id in pending, "supervision": sup.status(e.id, env) if sup else None,
                "maintenance": data["maintenance"].get(f"{e.id}|{env}"), "upstream": e.upstream,
            })  # fmt: skip
        return rows

    async def runtime_state() -> tuple[Data, dict[str, Any], list[dict[str, Any]], rt.Host]:
        data = store.read()
        engine_info = await eng.probe()
        pending = await admin.cfg.pending_restart()
        raw = (await admin.cfg.read())["env"]
        bound = {
            e.id: [v for v in raw if any(b["app"] == e.id for b in v["bindings"])] for e in registry.entries()
        }
        host = rt.Host(engine_info.get("ncpu"), (engine_info["mem_total"] or 0) / rt.GIB or None)
        return data, engine_info, runtime_rows(data, engine_info, pending, bound), host

    @api.get("/runtime")
    async def get_runtime() -> Any:
        data, info, rows, host = await runtime_state()
        cpu, mem = rt.allocated(registry.entries(), data["limits"])
        eng_out = {k: info[k] for k in ("reachable", "socket", "version", "error")}
        eng_out["live"] = [
            {k: v for k, v in x.items() if k not in ("host", "networks")} for x in info["live"]
        ]
        apps = [{k: v for k, v in r.items() if k != "upstream"} for r in rows]
        return {
            "host": host.as_json(),
            "allocated": {"cpu": cpu, "memory_gib": mem},
            "engine": eng_out,
            "apps": apps,
        }

    @api.put("/runtime/{app_id}/limits")
    async def put_limits(app_id: str, request: Request) -> Any:
        need_app(app_id)
        lim = await body(request)
        rt.validate_limits(lim)
        lim = {"cpu": float(lim["cpu"]), "memory": str(lim["memory"]), "pids": int(lim["pids"])}
        data = store.read()
        host = rt.Host()
        try:
            rt.admit(host, registry.entries(), data["limits"], app_id, lim)
        except OpsError as e:
            log("WARN", app_id, actor_of(request), f"admission control recusou limites: {e.message}")
            raise
        detail = f"{name_of(app_id)} · {lim['cpu']} vCPU · {lim['memory']} · {lim['pids']} processos"
        async with store.locked(True) as d:
            d["limits"][app_id] = lim
        await admin.audit(
            actor_of(request), "Limites de container alterados", detail, icon="settings", scope=app_id
        )
        parts = []
        for env in ("producao", "staging"):
            name = rt.container_name(app_id, env)
            try:
                if await eng.update_limits(name, lim["cpu"], lim["memory"], lim["pids"]):
                    parts.append(f"aplicado ao container {name}")
            except Exception as e:
                parts.append(f"falha ao aplicar a {name}: {e}"[:200])
        applied = (
            "salvo; " + "; ".join(parts) if parts else "salvo; será aplicado na próxima criação do container"
        )
        log("INFO", app_id, actor_of(request), f"limites {detail} · {applied}")
        return {"limits": lim, "applied": applied}

    def container_of(app_id: str, env: str | None) -> str:
        if rt.is_platform(app_id):  # contêiner da plataforma: só leitura (logs/inspect)
            return rt.own(app_id)
        need_app(app_id)
        return rt.container_name(app_id, env_key(env))

    @api.get("/runtime/{app_id}/logs")
    async def runtime_logs(app_id: str, env: str | None = None, tail: int = 200) -> Any:
        name = container_of(app_id, env)
        lines = await eng.tail(name, tail)
        return {
            "container": name,
            "items": [{"stream": k, "level": rt.level_of(k, t), "text": t} for k, t in lines],
        }

    @api.get("/runtime/{app_id}/logs/stream")
    async def runtime_logs_stream(app_id: str, request: Request, env: str | None = None) -> Response:
        name = container_of(app_id, env)

        async def events() -> AsyncIterator[str]:
            yield "event: ready\ndata: conectado\n\n"
            seen = 0
            while not await request.is_disconnected():
                try:
                    lines = await eng.tail(name, 200)
                except OpsError:
                    return
                for k, t in lines[seen:]:
                    yield f"event: line\ndata: {json.dumps({'stream': k, 'level': rt.level_of(k, t), 'text': t})}\n\n"
                seen = len(lines)
                await asyncio.sleep(1.5)

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"cache-control": "no-store"}
        )

    @api.get("/runtime/{app_id}/inspect")
    async def runtime_inspect(app_id: str, env: str | None = None) -> Any:
        return await eng.inspect(container_of(app_id, env))

    @api.post("/provision/{app_id}/database")
    async def provision_db(app_id: str, request: Request, env: str | None = None) -> Any:
        e = need_app(app_id)
        k, actor = env_key(env), actor_of(request)
        try:
            p = await pv.provision(backups, admin.cfg, actor, app_id, k)
        except OpsError as ex:
            log("ERROR", app_id, actor, f"provisionamento: {ex.message}")
            raise OpsError(ex.message, 503 if ex.status == 400 else ex.status) from ex
        if not p["changed"]:
            await admin.audit(actor, "Banco do app verificado", f"{e.name} · banco {p['database']} já provisionado e acessível",
                              icon="check", scope=app_id)  # fmt: skip
        log("INFO", app_id, actor, f"provisionamento: banco {p['database']} {'provisionado' if p['changed'] else 'já provisionado'} · variável {p['variable']}")  # fmt: skip
        return {"provisioned": p, "restart_pending": app_id in await admin.cfg.pending_restart()}

    async def rotate_db(app_id: str, actor: str, env: str = "producao") -> str:
        var = await pv.rotate(backups, admin.cfg, actor, app_id, env_key(env))
        log("INFO", app_id, actor, f"senha do banco rotacionada · variável {var}")
        return var

    app.state.ops_rotate_db = rotate_db

    @api.post("/runtime/{app_id}/{action}")
    async def runtime_action(app_id: str, action: str, request: Request, env: str | None = None) -> Any:
        e = need_app(app_id)
        if action not in ("apply", "start", "restart", "stop"):
            raise OpsError("Ação inválida.", 404)
        k = env_key(env)
        data = store.read()
        if f"{app_id}|{k}" in data["maintenance"] and action != "apply":
            raise OpsError(f"{name_of(app_id)} em manutenção: ação bloqueada até liberar em Backups.", 409)
        name = rt.container_name(app_id, k)
        sup = getattr(app.state, "ops_supervisor", None)
        if action == "apply":
            lim = rt.resources_of(e, data["limits"])
            await rollout.apply(app_id, k, lim, e.version or "—", None)
            if k == "producao":
                await admin.cfg.clear_pending(app_id)
            await admin.audit(actor_of(request), "Container recriado com as variáveis atuais", f"{e.name} · {name}",
                              icon="box", scope=app_id)  # fmt: skip
            log(
                "INFO",
                app_id,
                actor_of(request),
                f"container {name} recriado com as variáveis atuais (blue/green)",
            )
            return {"container": name, "action": "apply",
                    "detail": f"{e.name} · {name} recriado(s) com a mesma imagem e as variáveis atuais, sem indisponibilidade (blue/green)."}  # fmt: skip
        await eng.action(name, action)
        if sup:
            await sup.mark_stopped(app_id, k, action == "stop")
        label = {"start": "iniciado", "restart": "reiniciado", "stop": "parado"}[action]
        await admin.audit(
            actor_of(request), f"Container {label}", f"{e.name} · {name}", icon="box", scope=app_id
        )
        log("INFO", app_id, actor_of(request), f"container {name} {label}")
        return {"container": name, "action": action}

    # ---- Tarefas -------------------------------------------------------------------------------------
    @api.get("/tasks")
    async def list_tasks() -> Any:
        targets = [(e.id, "producao", e.upstream, admin.tokens.get(e.id)) for e in registry.entries()]
        return await jb.list_all(client, targets, name_of)

    @api.post("/tasks/{app_id}/{task_id}/{action}")
    async def task_action(
        app_id: str, task_id: str, action: str, request: Request, env: str | None = None
    ) -> Any:
        e = need_app(app_id)
        title = {"retry": "Tarefa reprocessada", "cancel": "Cancelamento de tarefa solicitado"}.get(action)
        if title is None:
            raise OpsError("Ação inválida.", 409)
        if not task_id or len(task_id) > 80 or not all(c.isalnum() or c == "-" for c in task_id):
            raise OpsError("Tarefa inválida.", 409)
        if env_key(env) == "staging":
            raise OpsError("Staging não encontrado para esta aplicação.", 404)
        token = admin.tokens.get(app_id)
        if not token:
            raise OpsError("App sem token interno.", 409)
        try:
            r = await client.post(f"{e.upstream.rstrip('/')}/{app_id}/_hub/jobs/{task_id}/{action}",
                                  headers={jb.TOKEN_HEADER: token}, timeout=10)  # fmt: skip
        except httpx.HTTPError as ex:
            raise OpsError(f"app sem resposta ({ex})", 409) from ex
        try:
            out = r.json()
        except ValueError:
            out = {}
        if r.status_code >= 300:
            raise OpsError(
                out.get("error") or f"o app respondeu {r.status_code}",
                404 if "não encontrada" in str(out) else 409,
            )
        job = out.get("job") or {}
        await admin.audit(actor_of(request), title, f"{e.name} · {job.get('kind', '')} · {job.get('id', task_id)}",
                          icon="clock", scope=app_id)  # fmt: skip
        log(
            "INFO",
            app_id,
            actor_of(request),
            f"tarefas: {title} ({job.get('kind', '')} {job.get('id', task_id)})",
        )
        return {"job": job}

    # ---- Backups -------------------------------------------------------------------------------------
    @api.get("/backups")
    async def list_backups() -> Any:
        d = store.read()
        return {"policies": d["policies"], "history": d["backups"], "schedules": d["schedules"],
                "timezone": os.getenv("HUB_BACKUP_TZ", "America/Sao_Paulo")}  # fmt: skip

    @api.put("/backups/policies/{app_id}")
    async def put_policy(app_id: str, request: Request) -> Any:
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
        """Alerta da plataforma para quem administra (Inbox + SSE); no máximo um igual por hora."""
        app.state.inbox.push(item_of("alert", app_id, title, body=detail[:500], level=level, to_perm="editar_configuracoes",
                                     link=link, source=f"alert:{app_id}:{title}:{now_ms() // 3_600_000}"))  # fmt: skip

    app.state.platform_alert = alert

    async def set_maintenance(app_id: str, k: str, kind: str | None, detail: str) -> None:
        """Liga/desliga "Em manutenção (restauração)" do app×ambiente (`None` = libera)."""
        async with store.locked(True) as dd:
            if kind is None:
                dd["maintenance"].pop(f"{app_id}|{k}", None)
            else:
                dd["maintenance"][f"{app_id}|{k}"] = {"kind": kind, "since": now_ms(), "detail": detail[:300],
                                                      "replica": app.state.leader.replica}  # fmt: skip

    async def safety_snapshot(app_id: str, k: str, phase: Callable[[int, str], None]) -> str:
        """Backup "Pré-restauração" do estado atual (restaurável como qualquer outro); falha vira nota, não aborta."""
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

    async def start_backup(app_id: str, k: str, kind: str, actor: str) -> dict[str, Any]:
        """Cria o job de backup e o executa em segundo plano (manual ou do agendador); `OpsError` se não puder começar."""
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

        async def go() -> None:
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
                except OpsError as e:  # o backup local vale; a falha vira nota e alerta
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
    async def backup_action(rec_id: int, action: str, request: Request) -> Response:
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

        async def go() -> None:
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

    app.include_router(api)

    # ---- Páginas -------------------------------------------------------------------------------------
    def render(
        request: Request, name: str, eyebrow: str, title: str, subtitle: str, tab: str = "", **ctx: Any
    ) -> Response:
        data = page_ctx(request) | {
            "icon": icon,
            "eyebrow": eyebrow,
            "title": title,
            "subtitle": subtitle,
            "tab": tab or title,
        }
        return page_response(templates, request, name, data | ctx)

    mount_devkit(app, render, lambda r: (os.getenv("HUB_PUBLIC_URL") or str(r.base_url)).rstrip("/"))

    @app.get("/hub-env")
    async def hub_env(to: str = "producao", next: str = "/") -> Response:
        """Liga/desliga o modo staging do navegador (cookie sem script, SameSite=Strict); o gateway confere a permissão."""
        dest = next if next.startswith("/") and not next.startswith("//") and "\\" not in next else "/"
        r = Response(status_code=303, headers={"location": dest, "cache-control": "no-store"})
        if to == "staging":
            r.set_cookie("hub_env", "staging", httponly=True, samesite="strict", path="/")
        else:
            r.delete_cookie("hub_env", path="/")
        return r

    @app.get("/admin/deploys", response_class=HTMLResponse)
    async def deploys_page(request: Request) -> Response:
        d, now = store.read(), now_ms()
        vers: dict[str, list[dict[str, Any]]] = {}
        for x in sorted(d["deployments"], key=lambda y: -y["id"]):
            vers.setdefault(x["app"], []).append({
                "id": x["id"], "app": name_of(x["app"]), "version": x["version"], "env": x["env"], "origin": x["origin"],
                "origin_class": "status-pill ops-pill-info" if x["origin"] == "GitHub Actions" else "status-pill ops-pill-muted",
                "commit": x["commit"], "status": x["status"], "status_class": tone(x["status"]), "note": x.get("note", ""),
                "runtime": x.get("runtime", ""), "when": relative(x["at"], now), "approve": x["status"] == m.AGUARDANDO,
                "promote": x["status"] == m.IMPLANTADO and x["env"] == m.STAGING,
                "rollback": x["env"] == m.PRODUCAO and (x["status"] == m.IMPLANTADO or (x["status"] == m.SUBSTITUIDO and bool(x.get("image")))),
                "active": x["status"] == m.IMPLANTADO,
                "staging_link": (f"/hub-env?to=staging&next=/apps/{x['app']}" if x["status"] == m.IMPLANTADO and x["env"] == m.STAGING else None),
                "scripts_pending": [] if x["status"] in (m.REJEITADO, m.FALHOU) else
                    [(s["path"], s["sha384"], s["reason"]) for s in m.pending_scripts(d, x)],
            })  # fmt: skip
        rows = []
        known = [e.id for e in registry.entries()]
        for aid in known + [a for a in vers if a not in known]:  # uma linha por aplicação: versão atual
            if vs := vers.get(aid, [])[
                :40
            ]:  # (produção implantada, senão a implantada, senão a mais recente)
                cur = next((v for v in vs if v["status"] == m.IMPLANTADO and v["env"] == m.PRODUCAO), None)
                cur = cur or next((v for v in vs if v["status"] == m.IMPLANTADO), vs[0])
                rows.append({"app": aid, "name": name_of(aid), "cur": cur, "vers": vs})
        rows, pager = paginate(request, rows)
        supply = "Assinatura: não configurada · Vulnerabilidades: não configurado"
        return render(request, "ops_deploys.html", "Aplicações", "Implantações",
                      "Publique novas versões das aplicações em staging ou produção. Pacotes enviados aqui ou pelo CI/CD passam pelas mesmas validações.",
                      rows=rows, steps=pl.STEPS, rollout=ROLLOUT, supply=supply, ci_snippet=CI_SNIPPET, pager=pager,
                      example=MANIFEST_EXAMPLE, cmds=SDK_CMDS)  # fmt: skip

    @app.get("/admin/runtime", response_class=HTMLResponse)
    async def runtime_page(request: Request) -> Response:
        data, info, rows, host = await runtime_state()
        podman = "runtime=podman" in request.url.query
        alloc_cpu, alloc_mem = rt.allocated(registry.entries(), data["limits"])
        free_cpu, free_mem = host.available_cpu - alloc_cpu, host.available_mem_gib - alloc_mem

        def seg(v: float, total: float, cls: str, lbl: str) -> tuple[str, str, str]:
            return f"flex:{max(v / total, 0.0):.4f}", cls, lbl

        cpu_segs = [seg(host.system_cpu, host.cpu, "ops-seg sys", "Sistema"), seg(host.platform_cpu, host.cpu, "ops-seg plat", "Plataforma"),
                    seg(alloc_cpu, host.cpu, "ops-seg apps", "Aplicações"), seg(free_cpu, host.cpu, "ops-seg free", "Livre")]  # fmt: skip
        mem_segs = [seg(host.system_mem_gib, host.mem_gib, "ops-seg sys", "Sistema"), seg(host.platform_mem_gib, host.mem_gib, "ops-seg plat", "Plataforma"),
                    seg(alloc_mem, host.mem_gib, "ops-seg apps", "Aplicações"), seg(free_mem, host.mem_gib, "ops-seg free", "Livre")]  # fmt: skip
        live = {x["name"]: x for x in info["live"]}
        raw = (await admin.cfg.read())["env"]
        view: list[dict[str, Any]] = []
        platform_view: list[dict[str, Any]] = []
        for r in rows:
            lv = live.get(r["container"])
            alert = None
            if r["maintenance"]:
                mt = r["maintenance"]
                failed = mt["kind"] == "restauracao_falhou"
                alert = ("Em manutenção (restauração falhou)" if failed else "Em manutenção (restauração)", mt.get("detail", ""),
                         "Confira os dados, restaure o snapshot \"Pré-restauração\" ou outro backup e libere em Backups." if failed
                         else "Implantação, aplicar, iniciar/reiniciar e backup voltam ao fim da restauração.")  # fmt: skip
            running = bool(lv and lv["state"] == "running")
            if alert:
                state, status = "bad", alert[0]
            elif lv and running:
                state, status = "ok", "Em execução"
            elif lv and lv["state"] in ("exited", "created"):
                state, status = "off", "Parado"
            elif lv:
                state, status = "bad", f"Com problema ({lv['state']})"
            else:
                state, status = "off", "Não criado" if info["reachable"] else "Sem conexão"
            mem_mib = r["memory_bytes"] / 1048576
            cpu = f"{br(lv['cpu_used'], 2) if lv and lv['cpu_used'] is not None else '—'} / {br(r['cpu'], 2)} vCPU"
            used = lv["mem_used"] / 1048576 if lv and lv["mem_used"] is not None else None
            mem = f"{br(used, 0) if used is not None else '—'} / {br(mem_mib, 0)} MiB"
            image = lv["image"] if lv else r["image"]
            uptime = rt.uptime_of(lv["status"]) if lv else "—"
            port = r["upstream"].rstrip("/").rsplit(":", 1)[-1]
            port = port if port.isdigit() else "—"
            hc = (lv or {}).get("host") or {}
            facts = [("Contêiner", r["container"]), ("Aplicação", r["name"]), ("Ambiente", "Staging" if r["env"] == "staging" else "Produção"), ("Estado", status),
                     ("Imagem", image), ("Tempo ativo", uptime), ("Porta interna", port), ("CPU", cpu), ("Memória", mem),
                     ("Processos", f"{(lv or {}).get('pids') if lv and lv.get('pids') is not None else '—'} / {r['pids']}"),
                     ("Rede isolada", hc.get("NetworkMode", ""))]  # fmt: skip
            facts.append(("Arquivos somente leitura", "Sim" if hc.get("ReadonlyRootfs") else "Não"))
            hard = [f"cap-drop {','.join(hc['CapDrop'])}"] if hc.get("CapDrop") else []
            hard += hc.get("SecurityOpt") or []
            if hard:
                facts.append(("Privilégios mínimos", " · ".join(hard)))
            facts.append(("Reinício automático", "Sim (o hub também religa em caso de queda)"))
            if sv := r["supervision"]:
                facts.append(
                    (
                        "Supervisão",
                        f"{sv['restarts']} religamento(s)"
                        + (" · laço de quedas: parou de religar" if sv["loop"] else ""),
                    )
                )
            if r["restart_pending"]:
                facts.append(("Reinício pendente", "Variáveis alteradas depois da criação do contêiner"))
            vars_ = {injected_name(v): ("••••" if v.get("kind", "publica") != "publica" else v["value"])
                     for v in raw if any(b["app"] == r["id"] and b["env"] == r["env"] for b in v["bindings"])}  # fmt: skip
            view.append({
                "id": r["id"], "name": r["name"], "env": r["env"], "staging": r["env"] == "staging", "container": r["container"], "image": image,
                "state": state, "status": status, "cpu": cpu, "mem": mem, "mem_style": f"width:{min(max((used or 0) / mem_mib * 100, 0), 100):.0f}%",
                "uptime": uptime, "port": port, "controls": lv is not None, "running": running,
                "limits": json.dumps({"app": r["id"], "name": r["name"], "cpu": r["cpu"], "memory": r["memory"], "pids": r["pids"]}),
                "pending": r["restart_pending"], "alert": alert, "facts": facts,
                "vars_json": json.dumps(dict(sorted(vars_.items())), indent=2, ensure_ascii=False), "staging_href": f"/hub-env?to=staging&next=/apps/{r['id']}",
            })  # fmt: skip
        for x in sorted(info.get("platform", []), key=lambda z: z["name"]):
            running = x["state"] == "running"
            mem_l = (x["mem_limit"] or 0) / 1048576
            used = x["mem_used"] / 1048576 if x["mem_used"] is not None else None
            cpu = f"{br(x['cpu_used'], 2)} vCPU" if x["cpu_used"] is not None else "—"
            mem = f"{br(used, 0)} MiB" if used is not None else "—"
            status = "Em execução" if running else ("Parado" if x["state"] in ("exited", "created") else f"Com problema ({x['state']})")  # fmt: skip
            up = rt.uptime_of(x["status"])
            platform_view.append({
                "id": x["name"], "name": "Plataforma", "env": "platform", "container": x["name"], "image": x["image"],
                "state": "ok" if running else "off", "status": status, "cpu": cpu, "mem": mem,
                "mem_style": f"width:{min(max((used or 0) / mem_l * 100, 0), 100) if mem_l else 0:.0f}%",
                "uptime": up, "port": "—", "vars_json": "{}",
                "facts": [("Contêiner", x["name"]), ("Grupo", "Plataforma (somente leitura)"), ("Estado", status),
                          ("Imagem", x["image"]), ("Tempo ativo", up), ("CPU", cpu), ("Memória", mem),
                          ("Rede", (x.get("host") or {}).get("NetworkMode", ""))],
            })  # fmt: skip
        sock = (
            "/run/podman/podman.sock"
            if podman
            else (info["socket"] if os.getenv("HUB_CONTAINER_SOCKET") else "/var/run/docker.sock")
        )
        return render(
            request, "ops_runtime.html", "Aplicações", "Containers", "Cada aplicação roda isolada, com limites próprios de CPU e memória.",
            rows=view, platform_rows=platform_view, banner=None if info["reachable"] else f"Runtime indisponível — exibindo especificação. ({info['error']})",
            docker_class="filter-chip" if podman else "filter-chip active", podman_class="filter-chip active" if podman else "filter-chip",
            shown_socket=sock, rollout=ROLLOUT, host_source=f"Medido em: {host.source}",
            in_use=f"Conectado a {info['socket']} · {info['version']}" if info["reachable"] else f"Sem conexão com {info['socket']}",
            cpu_title=f"CPU · {br(host.cpu, 1)} vCPU", mem_title=f"Memória · {br(host.mem_gib, 1)} GiB", cpu_segs=cpu_segs,
            mem_segs=mem_segs, plat_label=f"Plataforma (reservado {br(host.platform_cpu, 1)} vCPU · {br(host.platform_mem_gib, 1)} GiB)",
            free_label=f"Livre {br(free_cpu, 1)} vCPU · {br(free_mem, 1)} GiB",
        )  # fmt: skip

    @app.get("/admin/sdk")
    async def old_sdk() -> Response:
        return RedirectResponse("/admin/deploys", 308)

    @app.get("/admin/tasks", response_class=HTMLResponse)
    async def tasks_page(request: Request) -> Response:
        q = request.query_params
        app_f = q.get("app") if registry.get(q.get("app", "")) else None
        st_f = q.get("status") if q.get("status") in dict(TASK_STATUSES) else None
        listing = await list_tasks()
        every = listing["tasks"]
        running = sum(t["status"] == "running" for t in every)
        failed = sum(t["status"] in ("failed", "dead") for t in every)
        rows = []
        for t in every:
            if (app_f and t["app"] != app_f) or (st_f and t["status"] != st_f):
                continue
            lbl = jb.label(t)
            total = t.get("progress_total", 0)
            done = t.get("progress_done", 0)
            progress = (f"{jb.percent(t)}% · {done} de {total}" if total > 0 else
                        f"{done} · {t.get('progress_text', '')}" if done > 0 else f"{jb.percent(t)}%")  # fmt: skip
            sg = t.get("saga")
            key = f"{t['app']}|{t['id']}"
            rows.append({
                "id": t["id"], "kind": t["kind"], "app_id": t["app"], "app": f"{t['app_name']} (staging)" if t["env"] == "staging" else t["app_name"],
                "status_key": t["status"], "status": lbl, "status_class": tone(lbl), "user": t.get("user", ""),
                "created": log_stamp(max(int(t.get("created_at", 0)), 0)), "created_ms": str(t.get("created_at", 0)),
                "pct": f"width:{jb.percent(t)}%", "progress": progress, "attempts": f"{t['attempts']} de {t['max_attempts']}",
                "duration": f"{br(float(t.get('elapsed_s', 0)), 0)} s", "error": (t.get("error") or "")[:160],
                "saga": (SAGA.get(sg.get("state"), "desconhecido"), [(p["name"] or "", SAGA_STEP.get(p["state"], "?")) for p in sg["steps"]]) if sg else None,
                "retry": f"{key}|retry|{t['env']}" if jb.can_retry(t) else None,
                "cancel": f"{key}|cancel|{t['env']}" if jb.can_cancel(t) else None,
            })  # fmt: skip
        n = len(every)
        summary = f"{n} {'tarefa' if n == 1 else 'tarefas'} · {running} em execução · {failed} com falha"
        errors = " · ".join(f"{name_of(a)}: {e}" for a, e in listing["errors"])
        rows, pager = paginate(request, rows)
        return render(request, "ops_tasks.html", "Operação", "Tarefas",
                      "Importações, exportações e outros processos longos dos apps: acompanhe, reprocesse ou cancele.",
                      rows=rows, errors=errors, summary=summary, pager=pager)  # fmt: skip

    @app.get("/admin/backups", response_class=HTMLResponse)
    async def backups_page(request: Request) -> Response:
        d, now = store.read(), now_ms()
        tz_name = os.getenv("HUB_BACKUP_TZ", "America/Sao_Paulo")
        policies = []
        for p in d["policies"]:
            if registry.get(p["app"]) is None:
                continue
            try:
                manual = normalize(p["schedule"]) is None
            except OpsError:
                manual = False  # agenda inválida: o erro aparece na linha do ambiente
            runs = []
            for env in p["envs"]:
                lbl = "staging" if env == "staging" else "produção"
                st = d["schedules"].get(f"{p['app']}|{env}")
                last = (
                    f" · último: {st['last_status']} ({relative(st['last_at'], now)})"
                    if st and st.get("last_status")
                    else ""
                )
                runs.append(f"{lbl}: manual" if manual else
                            f"{lbl}: agenda inválida — {st['error']}" if st and st.get("error") else
                            f"{lbl}: próximo {local_label(st['next_due'], timezone_of(p.get('tz', '')))}{last}" if st and st.get("next_due") else
                            f"{lbl}: aguardando o agendador")  # fmt: skip
            policies.append({"app": p["app"], "name": name_of(p["app"]), "schedule": p["schedule"], "retention": f"últimos {p['keep']}",
                             "volumes": p["volumes"], "hooks": p["hooks"], "destination": p["destination"], "item": json.dumps(p, ensure_ascii=False),
                             "staging": False, "runs": runs})  # fmt: skip
        hist = []
        for b in d["backups"]:
            sfx = " (staging)" if (b.get("env") or "producao") == "staging" else ""
            detail = b.get("detail") or (
                f"sha256 {b['sha256']} · {b['files']} arquivos" if b.get("sha256") else ""
            )
            hist.append({"id": b["id"], "app": name_of(b["app"]) + sfx, "kind": b["kind"], "size": m.human(b["size"]) if b["size"] else "—",
                         "status": b["status"], "status_class": tone(b["status"]), "when": relative(b["at"], now), "detail": detail,
                         "usable": b["status"] == "Concluído" and bool(b.get("file"))})  # fmt: skip
        last = next((b for b in d["backups"] if b["kind"] == "Teste de restore"), None)
        if last:
            ok = last["status"] == "Concluído"
            tv, tc = (
                relative(last["at"], now),
                f"{name_of(last['app'])} · {'OK' if ok else 'falhou'} em {br(last['duration_ms'] / 1000, 1)} s",
            )
            tcls = "metric-change" if ok else "metric-change down"
        else:
            tv, tc, tcls = "Nunca", "Faça um teste de restauração a partir do histórico", "metric-change"
        kept = [b for b in d["backups"] if b.get("file") and b["status"] == "Concluído"]
        fails = [b for b in d["backups"] if b["status"] == "Falhou" and now - b["at"] < 86_400_000]
        maint = []
        for k, mt in d["maintenance"].items():
            a, env = k.split("|", 1)
            lbl = (
                "Em manutenção (restauração falhou)"
                if mt["kind"] == "restauracao_falhou"
                else "Em manutenção (restauração)"
            )
            maint.append({"app": a, "env": env, "name": name_of(a) + (" (staging)" if env == "staging" else ""),
                          "detail": f"{lbl} — {mt.get('detail', '')}", "failed": mt["kind"] == "restauracao_falhou" or a not in jobs.busy})  # fmt: skip
        return render(
            request, "ops_backups.html", "Operação", "Backups e snapshots",
            "Cópias de segurança dos dados e arquivos de cada aplicação, com restauração e teste de restauração.", tab="Backups",
            policies=policies, history=hist, maint=maint, test_value=tv, test_change=tc, test_class=tcls,
            stored_value=m.human(sum(b["size"] for b in kept)),
            stored_change={0: "Nenhum arquivo retido", 1: "1 arquivo retido"}.get(len(kept), f"{len(kept)} arquivos retidos"),
            fail_count=str(len(fails)), fail_class="metric-change down" if fails else "metric-change",
            fail_change=f"{name_of(fails[0]['app'])} · {fails[0]['detail']}" if fails else "Nenhuma falha",
            tz_note=f"Agendas em cron de 5 campos (ou \"Diário 02:00\", \"A cada 6 h\", \"Manual\") no fuso {tz_name}, com atraso aleatório e execução perdida configuráveis; um job por aplicação por vez.",
        )  # fmt: skip

    return store
