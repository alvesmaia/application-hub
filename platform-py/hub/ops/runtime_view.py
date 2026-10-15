"""View rows of the Containers page (`/admin/runtime`): pure functions of the runtime state."""

from __future__ import annotations

import json
from typing import Any

from hub.admin.config import injected_name
from hub.ops import runtime as rt
from hub.ops.opsctx import br

MIB = 1048576
Row = dict[str, Any]


def _seg(v: float, total: float, cls: str, lbl: str) -> tuple[str, str, str]:
    return f"flex:{max(v / total, 0.0):.4f}", cls, lbl


def host_segments(host: rt.Host, alloc_cpu: float, alloc_mem: float) -> tuple[list[Any], list[Any]]:
    """CPU and memory bars: system, platform, apps and free share of the host."""
    free_cpu, free_mem = host.available_cpu - alloc_cpu, host.available_mem_gib - alloc_mem
    cpu = [_seg(host.system_cpu, host.cpu, "ops-seg sys", "Sistema"), _seg(host.platform_cpu, host.cpu, "ops-seg plat", "Plataforma"),
           _seg(alloc_cpu, host.cpu, "ops-seg apps", "Aplicações"), _seg(free_cpu, host.cpu, "ops-seg free", "Livre")]  # fmt: skip
    mem = [_seg(host.system_mem_gib, host.mem_gib, "ops-seg sys", "Sistema"), _seg(host.platform_mem_gib, host.mem_gib, "ops-seg plat", "Plataforma"),
           _seg(alloc_mem, host.mem_gib, "ops-seg apps", "Aplicações"), _seg(free_mem, host.mem_gib, "ops-seg free", "Livre")]  # fmt: skip
    return cpu, mem


def maintenance_alert(mt: Row | None) -> tuple[str, str, str] | None:
    if not mt:
        return None
    failed = mt["kind"] == "restauracao_falhou"
    return ("Em manutenção (restauração falhou)" if failed else "Em manutenção (restauração)", str(mt.get("detail", "")),
            "Confira os dados, restaure o snapshot \"Pré-restauração\" ou outro backup e libere em Backups." if failed
            else "Implantação, aplicar, iniciar/reiniciar e backup voltam ao fim da restauração.")  # fmt: skip


def container_state(lv: Row | None, alert: tuple[str, str, str] | None, reachable: bool) -> tuple[str, str]:
    """(css state, label) of an app container."""
    if alert:
        return "bad", alert[0]
    if lv and lv["state"] == "running":
        return "ok", "Em execução"
    if lv and lv["state"] in ("exited", "created"):
        return "off", "Parado"
    if lv:
        return "bad", f"Com problema ({lv['state']})"
    return "off", "Não criado" if reachable else "Sem conexão"


def _hardening(hc: Row) -> list[tuple[str, str]]:
    facts: list[tuple[str, str]] = [
        ("Arquivos somente leitura", "Sim" if hc.get("ReadonlyRootfs") else "Não")
    ]
    hard = [f"cap-drop {','.join(hc['CapDrop'])}"] if hc.get("CapDrop") else []
    hard += hc.get("SecurityOpt") or []
    if hard:
        facts.append(("Privilégios mínimos", " · ".join(hard)))
    return facts


def _supervision(r: Row) -> list[tuple[str, str]]:
    facts: list[tuple[str, str]] = [("Reinício automático", "Sim (o hub também religa em caso de queda)")]
    if sv := r["supervision"]:
        loop = " · laço de quedas: parou de religar" if sv["loop"] else ""
        facts.append(("Supervisão", f"{sv['restarts']} religamento(s){loop}"))
    if r["restart_pending"]:
        facts.append(("Reinício pendente", "Variáveis alteradas depois da criação do contêiner"))
    return facts


def _bound_vars(raw: list[Row], r: Row) -> str:
    vars_ = {injected_name(v): ("••••" if v.get("kind", "publica") != "publica" else v["value"])
             for v in raw if any(b["app"] == r["id"] and b["env"] == r["env"] for b in v["bindings"])}  # fmt: skip
    return json.dumps(dict(sorted(vars_.items())), indent=2, ensure_ascii=False)


def _usage(lv: Row | None, r: Row) -> tuple[str, str, float | None, float]:
    mem_mib = r["memory_bytes"] / MIB
    cpu = f"{br(lv['cpu_used'], 2) if lv and lv['cpu_used'] is not None else '—'} / {br(r['cpu'], 2)} vCPU"
    used: float | None = float(lv["mem_used"]) / MIB if lv and lv["mem_used"] is not None else None
    mem = f"{br(used, 0) if used is not None else '—'} / {br(mem_mib, 0)} MiB"
    return cpu, mem, used, float(mem_mib)


def container_view(r: Row, lv: Row | None, raw: list[Row], reachable: bool) -> Row:
    """One app container row (production or staging) with its detail facts."""
    alert = maintenance_alert(r["maintenance"])
    state, status = container_state(lv, alert, reachable)
    cpu, mem, used, mem_mib = _usage(lv, r)
    image = lv["image"] if lv else r["image"]
    uptime = rt.uptime_of(lv["status"]) if lv else "—"
    port = r["upstream"].rstrip("/").rsplit(":", 1)[-1]
    port = port if port.isdigit() else "—"
    hc = (lv or {}).get("host") or {}
    pids = (lv or {}).get("pids") if lv and lv.get("pids") is not None else "—"
    facts = [("Contêiner", r["container"]), ("Aplicação", r["name"]), ("Ambiente", "Staging" if r["env"] == "staging" else "Produção"), ("Estado", status),
             ("Imagem", image), ("Tempo ativo", uptime), ("Porta interna", port), ("CPU", cpu), ("Memória", mem),
             ("Processos", f"{pids} / {r['pids']}"), ("Rede isolada", hc.get("NetworkMode", ""))]  # fmt: skip
    facts += _hardening(hc) + _supervision(r)
    return {
        "id": r["id"], "name": r["name"], "env": r["env"], "staging": r["env"] == "staging", "container": r["container"], "image": image,
        "state": state, "status": status, "cpu": cpu, "mem": mem, "mem_style": f"width:{min(max((used or 0) / mem_mib * 100, 0), 100):.0f}%",
        "uptime": uptime, "port": port, "controls": lv is not None, "running": bool(lv and lv["state"] == "running"),
        "limits": json.dumps({"app": r["id"], "name": r["name"], "cpu": r["cpu"], "memory": r["memory"], "pids": r["pids"]}),
        "pending": r["restart_pending"], "alert": alert, "facts": facts,
        "vars_json": _bound_vars(raw, r), "staging_href": f"/hub-env?to=staging&next=/apps/{r['id']}",
    }  # fmt: skip


def platform_view(x: Row) -> Row:
    """One platform container row (read-only)."""
    running = x["state"] == "running"
    mem_l = (x["mem_limit"] or 0) / MIB
    used = x["mem_used"] / MIB if x["mem_used"] is not None else None
    cpu = f"{br(x['cpu_used'], 2)} vCPU" if x["cpu_used"] is not None else "—"
    mem = f"{br(used, 0)} MiB" if used is not None else "—"
    status = "Em execução" if running else ("Parado" if x["state"] in ("exited", "created") else f"Com problema ({x['state']})")  # fmt: skip
    up = rt.uptime_of(x["status"])
    return {
        "id": x["name"], "name": "Plataforma", "env": "platform", "plat": True, "container": x["name"], "image": x["image"],
        "state": "ok" if running else "off", "status": status, "cpu": cpu, "mem": mem,
        "mem_style": f"width:{min(max((used or 0) / mem_l * 100, 0), 100) if mem_l else 0:.0f}%",
        "uptime": up, "port": "—", "vars_json": "{}",
        "facts": [("Contêiner", x["name"]), ("Grupo", "Plataforma (somente leitura)"), ("Estado", status),
                  ("Imagem", x["image"]), ("Tempo ativo", up), ("CPU", cpu), ("Memória", mem),
                  ("Rede", (x.get("host") or {}).get("NetworkMode", ""))],
    }  # fmt: skip
