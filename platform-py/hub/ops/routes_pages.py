"""Operations pages: `/admin/{deploys,runtime,tasks,backups}`, staging cookie and the developer kit."""

from __future__ import annotations

import os
from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from hub.admin.routes import relative
from hub.admin.store import log_stamp
from hub.ops import backups_view as bv
from hub.ops import jobs as jb
from hub.ops import model as m
from hub.ops import pipeline as pl
from hub.ops import runtime as rt
from hub.ops import runtime_view as rv
from hub.ops.devkit import mount_devkit
from hub.ops.model import now_ms
from hub.ops.opsctx import OpsCtx, br, ctx, tone
from hub.ops.routes_runtime import list_tasks, runtime_state
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
  "routes": ["/fornecedores/*"],
  "resources": { "cpu": 1.0, "memory": "1Gi", "pids": 100 },
  "permissions": ["fornecedores.read"],
  "requires": { "database": true, "variables": ["API_BASE_URL"], "secrets": ["SMTP_PASSWORD"] },
  "sdk": "1.0",
  "jobs": [{ "name": "fornecedores.reprocessar", "max_duration_s": 600, "max_attempts": 3 }]
}"""

SDK_CMDS = [
    ("hub dev --dir . --upstream http://127.0.0.1:3000", "Shell local (porta 4173) + proxy para o app nativo, identidade simulada (--role admin)"),
    ("hub pack --dir . --image registro/app@sha256:…", "Valida o manifest, exige digest e gera o .hubapp com checksums.txt"),
    ("hub deploy --env staging", "Empacota, pede os valores de requires.variables/secrets e envia (o mesmo que a aba Importar pacote)"),
    ('curl -H "Authorization: Bearer $HUB_TOKEN" -H "X-Hub-Request: 1" -F package=@app.hubapp …/api/ops/packages', "Publica pelo CI e roda o pipeline de validação; ?wait=true aguarda o resultado"),
    ("POST /api/ops/deployments/{id}/promote · /rollback", "Promove a mesma versão ou volta à anterior (pela tela de Implantações)"),
    ("POST /api/ops/backups/{app}/run", "Backup sob demanda; restore, clone e teste pelo histórico"),
    ("hub dev --limits · --container", "Planejado: limites locais e execução em container"),
]  # fmt: skip

router = APIRouter()


def render(
    ops: OpsCtx,
    request: Request,
    name: str,
    eyebrow: str,
    title: str,
    subtitle: str,
    tab: str = "",
    **ctx: Any,
) -> Response:
    data = ops.page_ctx(request) | {
        "icon": ops.icon,
        "eyebrow": eyebrow,
        "title": title,
        "subtitle": subtitle,
        "tab": tab or title,
    }
    return page_response(ops.templates, request, name, data | ctx)


@router.get("/hub-env")
async def hub_env(to: str = "producao", next: str = "/", ops: OpsCtx = Depends(ctx)) -> Response:
    """Turns the browser's staging mode on or off (cookie without script, SameSite=Strict); the gateway checks the permission."""
    dest = next if next.startswith("/") and not next.startswith("//") and "\\" not in next else "/"
    r = Response(status_code=303, headers={"location": dest, "cache-control": "no-store"})
    if to == "staging":
        r.set_cookie("hub_env", "staging", httponly=True, samesite="strict", path="/")
    else:
        r.delete_cookie("hub_env", path="/")
    return r


@router.get("/admin/deploys", response_class=HTMLResponse)
async def deploys_page(request: Request, ops: OpsCtx = Depends(ctx)) -> Response:
    d, now = ops.store.read(), now_ms()
    vers: dict[str, list[dict[str, Any]]] = {}
    for x in sorted(d["deployments"], key=lambda y: -y["id"]):
        vers.setdefault(x["app"], []).append({
            "id": x["id"], "app": ops.name_of(x["app"]), "version": x["version"], "env": x["env"], "origin": x["origin"],
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
    known = [e.id for e in ops.registry.entries()]
    for aid in known + [a for a in vers if a not in known]:  # one row per application: current version
        if vs := vers.get(aid, [])[:40]:  # (deployed production, else the deployed one, else the most recent)
            cur = next((v for v in vs if v["status"] == m.IMPLANTADO and v["env"] == m.PRODUCAO), None)
            cur = cur or next((v for v in vs if v["status"] == m.IMPLANTADO), vs[0])
            rows.append({"app": aid, "name": ops.name_of(aid), "cur": cur, "vers": vs})
    rows, pager = paginate(request, rows)
    supply = "Assinatura: não configurada · Vulnerabilidades: não configurado"
    return render(ops, request, "ops_deploys.html", "Aplicações", "Implantações",
                  "Publique novas versões das aplicações em staging ou produção. Pacotes enviados aqui ou pelo CI/CD passam pelas mesmas validações.",
                  rows=rows, steps=pl.STEPS, rollout=ROLLOUT, supply=supply, ci_snippet=CI_SNIPPET, pager=pager,
                  example=MANIFEST_EXAMPLE, cmds=SDK_CMDS)  # fmt: skip


@router.get("/admin/runtime", response_class=HTMLResponse)
async def runtime_page(request: Request, ops: OpsCtx = Depends(ctx)) -> Response:
    data, info, rows, host = await runtime_state(ops)
    podman = "runtime=podman" in request.url.query
    alloc_cpu, alloc_mem = rt.allocated(ops.registry.entries(), data["limits"])
    free_cpu, free_mem = host.available_cpu - alloc_cpu, host.available_mem_gib - alloc_mem
    cpu_segs, mem_segs = rv.host_segments(host, alloc_cpu, alloc_mem)
    live = {x["name"]: x for x in info["live"]}
    raw = (await ops.admin.cfg.read())["env"]
    view = [rv.container_view(r, live.get(r["container"]), raw, info["reachable"]) for r in rows]
    platform_view = [rv.platform_view(x) for x in sorted(info.get("platform", []), key=lambda z: z["name"])]
    default_sock = info["socket"] if os.getenv("HUB_CONTAINER_SOCKET") else "/var/run/docker.sock"
    sock = "/run/podman/podman.sock" if podman else default_sock
    page_rows, pager = paginate(request, view + platform_view)
    return render(ops,
        request, "ops_runtime.html", "Aplicações", "Containers", "Cada aplicação roda isolada, com limites próprios de CPU e memória.",
        rows=page_rows, pager=pager, banner=None if info["reachable"] else f"Runtime indisponível: exibindo especificação. ({info['error']})",
        docker_class="filter-chip" if podman else "filter-chip active", podman_class="filter-chip active" if podman else "filter-chip",
        shown_socket=sock, rollout=ROLLOUT, host_source=f"Medido em: {host.source}",
        in_use=f"Conectado a {info['socket']} · {info['version']}" if info["reachable"] else f"Sem conexão com {info['socket']}",
        cpu_title=f"CPU · {br(host.cpu, 1)} vCPU", mem_title=f"Memória · {br(host.mem_gib, 1)} GiB", cpu_segs=cpu_segs,
        mem_segs=mem_segs, plat_label=f"Plataforma (reservado {br(host.platform_cpu, 1)} vCPU · {br(host.platform_mem_gib, 1)} GiB)",
        free_label=f"Livre {br(free_cpu, 1)} vCPU · {br(free_mem, 1)} GiB",
    )  # fmt: skip


@router.get("/admin/sdk")
async def old_sdk(ops: OpsCtx = Depends(ctx)) -> Response:
    return RedirectResponse("/admin/deploys", 308)


def _task_row(t: dict[str, Any]) -> dict[str, Any]:
    lbl = jb.label(t)
    total = t.get("progress_total", 0)
    done = t.get("progress_done", 0)
    progress = (f"{jb.percent(t)}% · {done} de {total}" if total > 0 else
                f"{done} · {t.get('progress_text', '')}" if done > 0 else f"{jb.percent(t)}%")  # fmt: skip
    sg = t.get("saga")
    key = f"{t['app']}|{t['id']}"
    return {
        "id": t["id"], "kind": t["kind"], "app_id": t["app"], "app": f"{t['app_name']} (staging)" if t["env"] == "staging" else t["app_name"],
        "status_key": t["status"], "status": lbl, "status_class": tone(lbl), "user": t.get("user", ""),
        "created": log_stamp(max(int(t.get("created_at", 0)), 0)), "created_ms": str(t.get("created_at", 0)),
        "pct": f"width:{jb.percent(t)}%", "progress": progress, "attempts": f"{t['attempts']} de {t['max_attempts']}",
        "duration": f"{br(float(t.get('elapsed_s', 0)), 0)} s", "error": (t.get("error") or "")[:160],
        "saga": (SAGA.get(sg.get("state"), "desconhecido"), [(p["name"] or "", SAGA_STEP.get(p["state"], "?")) for p in sg["steps"]]) if sg else None,
        "retry": f"{key}|retry|{t['env']}" if jb.can_retry(t) else None,
        "cancel": f"{key}|cancel|{t['env']}" if jb.can_cancel(t) else None,
    }  # fmt: skip


@router.get("/admin/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request, ops: OpsCtx = Depends(ctx)) -> Response:
    q = request.query_params
    app_f = q.get("app") if ops.registry.get(q.get("app", "")) else None
    st_f = q.get("status") if q.get("status") in dict(TASK_STATUSES) else None
    listing = await list_tasks(ops=ops)
    every = listing["tasks"]
    running = sum(t["status"] == "running" for t in every)
    failed = sum(t["status"] in ("failed", "dead") for t in every)
    rows = [
        _task_row(t) for t in every if not ((app_f and t["app"] != app_f) or (st_f and t["status"] != st_f))
    ]
    n = len(every)
    summary = f"{n} {'tarefa' if n == 1 else 'tarefas'} · {running} em execução · {failed} com falha"
    errors = " · ".join(f"{ops.name_of(a)}: {e}" for a, e in listing["errors"])
    rows, pager = paginate(request, rows)
    return render(ops, request, "ops_tasks.html", "Operação", "Tarefas",
                  "Importações, exportações e outros processos longos dos apps: acompanhe, reprocesse ou cancele.",
                  rows=rows, errors=errors, summary=summary, pager=pager)  # fmt: skip


@router.get("/admin/backups", response_class=HTMLResponse)
async def backups_page(request: Request, ops: OpsCtx = Depends(ctx)) -> Response:
    d, now = ops.store.read(), now_ms()
    tz_name = os.getenv("HUB_BACKUP_TZ", "America/Sao_Paulo")
    policies = [
        bv.policy_row(p, d["schedules"], ops.name_of, now)
        for p in d["policies"]
        if ops.registry.get(p["app"])
    ]
    hist, pager = paginate(request, [bv.history_row(b, ops.name_of, now) for b in d["backups"]])
    tv, tc, tcls = bv.restore_test_metric(d["backups"], ops.name_of, now)
    kept = [b for b in d["backups"] if b.get("file") and b["status"] == "Concluído"]
    fails = [b for b in d["backups"] if b["status"] == "Falhou" and now - b["at"] < 86_400_000]
    maint = bv.maintenance_rows(d["maintenance"], ops.name_of, ops.jobs.busy)
    return render(ops,
        request, "ops_backups.html", "Operação", "Backups e snapshots",
        "Cópias de segurança dos dados e arquivos de cada aplicação, com restauração e teste de restauração.", tab="Backups",
        policies=policies, history=hist, pager=pager, maint=maint, test_value=tv, test_change=tc, test_class=tcls,
        stored_value=m.human(sum(b["size"] for b in kept)),
        stored_change={0: "Nenhum arquivo retido", 1: "1 arquivo retido"}.get(len(kept), f"{len(kept)} arquivos retidos"),
        fail_count=str(len(fails)), fail_class="metric-change down" if fails else "metric-change",
        fail_change=f"{ops.name_of(fails[0]['app'])} · {fails[0]['detail']}" if fails else "Nenhuma falha",
        tz_note=f"Agendas em cron de 5 campos (ou \"Diário 02:00\", \"A cada 6 h\", \"Manual\") no fuso {tz_name}, com atraso aleatório e execução perdida configuráveis; um job por aplicação por vez.",
    )  # fmt: skip


def mount(ops: OpsCtx) -> None:
    """Startup wiring of this section (state the rest of the hub calls)."""
    mount_devkit(
        ops.app, partial(render, ops), lambda r: (os.getenv("HUB_PUBLIC_URL") or str(r.base_url)).rstrip("/")
    )
