"""View rows of the Containers page (`/admin/runtime`): pure functions of the runtime state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from hub.admin.config import injected_name
from hub.ops import runtime as rt
from hub.ops.opsctx import br

MIB = 1048576
Row = dict[str, Any]


Pair = tuple[float, float]  # (CPU vCPU, memory GiB)
UNMEASURED_LABEL = "Em uso (sem detalhe por contêiner)"


@dataclass(frozen=True)
class UsageBars:
    """Segments (flex style, css class, hover) of the CPU and memory bars, and the host usage they add up to."""

    cpu: list[tuple[str, str, str]]
    mem: list[tuple[str, str, str]]
    cpu_used: float
    mem_used: float


def _seg(v: float, total: float, cls: str, lbl: str, unit: str) -> tuple[str, str, str]:
    v = max(v, 0.0) + 0.0  # never "-0,00": float noise (0.1 * 8 - 0.3 - 0.5) and the negative zero
    share = v / total * 100 if total else 0.0
    return (
        f"flex:{max(v / total, 0.0) if total else 0.0:.4f}",
        cls,
        f"{lbl}: {br(v, 2)} / {br(total, 2)} {unit} ({br(share, 1)}%)",
    )


def _bar(
    total: float, host_used: float | None, plat: float | None, apps: float | None, unit: str
) -> tuple[list[Any], float]:
    """System = the host's usage minus what the hub's containers use; without per-container numbers the whole
    usage is one segment (no made-up split)."""
    p, a = plat or 0.0, apps or 0.0
    used = min(max(host_used if host_used is not None else p + a, p + a), total)
    first = "Sistema" if plat is not None else UNMEASURED_LABEL
    segs = [_seg(used - p - a, total, "ops-seg sys", first, unit), _seg(p, total, "ops-seg plat", "Plataforma", unit),
            _seg(a, total, "ops-seg apps", "Aplicações", unit), _seg(total - used, total, "ops-seg free", "Livre", unit)]  # fmt: skip
    return segs, used


def usage_bars(
    host: rt.Host, host_usage: tuple[float | None, float | None], plat: Pair | None, apps: Pair | None
) -> UsageBars:
    """`host_usage`: (CPU fraction 0..1, memory GiB) of the whole host, None when /proc cannot be read."""
    frac, mem = host_usage
    cpu_segs, cpu_used = _bar(host.cpu, frac * host.cpu if frac is not None else None,
                              plat and plat[0], apps and apps[0], "vCPU")  # fmt: skip
    mem_segs, mem_used = _bar(host.mem_gib, mem, plat and plat[1], apps and apps[1], "GiB")
    return UsageBars(cpu_segs, mem_segs, round(cpu_used, 2), round(mem_used, 2))


def measured(rows: list[Row]) -> Pair | None:
    """(vCPU, GiB) used by these containers; None when the runtime could not measure them."""
    if any(r.get("cpu_used") is None for r in rows if r.get("state") == "running"):
        return None
    run = [r for r in rows if r.get("state") == "running"]
    return float(sum(r["cpu_used"] for r in run)), float(sum((r.get("mem_used") or 0) for r in run)) / rt.GIB


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


def _has_db(raw: list[Row], app: str) -> bool:
    """The app already has a database variable (any environment): "Check database" and password rotation."""
    return any(injected_name(v) == "HUB_DB_URL" and any(b["app"] == app for b in v["bindings"]) for v in raw)


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
        "has_db": _has_db(raw, r["id"]),
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
