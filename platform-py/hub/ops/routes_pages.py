"""Operations pages: `/admin/{deploys,runtime,tasks,backups}`, staging cookie and the developer kit."""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from hub.admin.config import injected_name
from hub.admin.routes import relative
from hub.admin.store import log_stamp
from hub.ops import jobs as jb
from hub.ops import model as m
from hub.ops import pipeline as pl
from hub.ops import runtime as rt
from hub.ops.devkit import mount_devkit
from hub.ops.model import Data, OpsError, now_ms
from hub.ops.opsctx import OpsCtx, br, tone
from hub.ops.schedule import local_label, normalize, timezone_of
from hub.ux.pager import paginate
from hub.ux.texts import page_response

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


def mount_pages(  # noqa: C901, PLR0915 closure mount moved from mount_ops; TASKS.md task 3
    c: OpsCtx,
    runtime_state: Callable[[], Awaitable[tuple[Data, dict[str, Any], list[dict[str, Any]], rt.Host]]],
    list_tasks: Callable[[], Awaitable[Any]],
) -> None:
    app, templates, page_ctx, icon, admin, registry, store, jobs, name_of = (
        c.app,
        c.templates,
        c.page_ctx,
        c.icon,
        c.admin,
        c.registry,
        c.store,
        c.jobs,
        c.name_of,
    )

    # ---- Pages ------------------------------------------------------------------------------------
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
        """Turns the browser's staging mode on or off (cookie without script, SameSite=Strict); the gateway checks the permission."""
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
        for aid in known + [a for a in vers if a not in known]:  # one row per application: current version
            if vs := vers.get(aid, [])[
                :40
            ]:  # (deployed production, else the deployed one, else the most recent)
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
    async def runtime_page(request: Request) -> Response:  # noqa: C901, PLR0915
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
    async def backups_page(request: Request) -> Response:  # noqa: PLR0915
        d, now = store.read(), now_ms()
        tz_name = os.getenv("HUB_BACKUP_TZ", "America/Sao_Paulo")
        policies = []
        for p in d["policies"]:
            if registry.get(p["app"]) is None:
                continue
            try:
                manual = normalize(p["schedule"]) is None
            except OpsError:
                manual = False  # invalid schedule: the error appears on the environment's row
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
