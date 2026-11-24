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
_DOWN_CAUSES = (  # (fragment of the engine error, cause in plain words): first match wins
    (
        "No such file",
        "o socket do runtime não existe no contêiner do hub (confira HUB_CONTAINER_SOCKET e o volume do socket)",
    ),
    ("Permission denied", "o hub não tem permissão no socket do runtime (confira o grupo do socket)"),
    ("Connection refused", "o runtime recusou a conexão (o serviço do Docker ou do Podman está parado?)"),
    ("timed out", "o runtime não respondeu a tempo (servidor sobrecarregado?)"),
)


def runtime_banner(error: str) -> str:
    """Banner of admin/runtime when the engine is unreachable: the likely cause in plain words instead of the raw
    exception; an unknown error keeps a short detail so the admin still has something to search for."""
    cause = next((c for frag, c in _DOWN_CAUSES if frag.lower() in error.lower()), None)
    if cause:
        return f"Runtime indisponível: {cause}. Sem métricas ao vivo; a tela mostra a configuração salva."
    return f"Runtime indisponível ({error[:120]}). Sem métricas ao vivo; a tela mostra a configuração salva."


@dataclass(frozen=True)
class UsageBars:
    """Segments (flex style, css class, hover) of the CPU and memory bars, and the host usage they add up to."""

    cpu: list[tuple[str, str, str]]
    mem: list[tuple[str, str, str]]
    cpu_used: float
    mem_used: float
    cpu_parts: tuple[float, float, float] = (0.0, 0.0, 0.0)  # system, platform, apps (the 5-minute chart)
    mem_parts: tuple[float, float, float] = (0.0, 0.0, 0.0)


def _seg(v: float, total: float, cls: str, lbl: str, unit: str) -> tuple[str, str, str]:
    v = max(v, 0.0) + 0.0  # never "-0,00": float noise (0.1 * 8 - 0.3 - 0.5) and the negative zero
    share = v / total * 100 if total else 0.0
    return (
        f"flex:{max(v / total, 0.0) if total else 0.0:.4f}",
        cls,
        f"{lbl}: " * bool(lbl) + f"{br(v, 2)} / {br(total, 2)} {unit} ({br(share, 1)}%)",
    )


def _bar(
    total: float, host_used: float | None, plat: float | None, apps: float | None, unit: str
) -> tuple[list[Any], float, tuple[float, float, float]]:
    """System = the host's usage minus what the hub's containers use; without per-container numbers the whole
    usage is one segment (no made-up split)."""
    p, a = plat or 0.0, apps or 0.0
    used = min(max(host_used if host_used is not None else p + a, p + a), total)
    first = "Sistema" if plat is not None else ""  # not split: the hover keeps only the numbers
    segs = [_seg(used - p - a, total, "ops-seg sys", first, unit), _seg(p, total, "ops-seg plat", "Plataforma", unit),
            _seg(a, total, "ops-seg apps", "Aplicações", unit), _seg(total - used, total, "ops-seg free", "Livre", unit)]  # fmt: skip
    return segs, used, (max(used - p - a, 0.0), p, a)


def usage_bars(
    host: rt.Host, host_usage: tuple[float | None, float | None], plat: Pair | None, apps: Pair | None
) -> UsageBars:
    """`host_usage`: (CPU fraction 0..1, memory GiB) of the whole host, None when /proc cannot be read."""
    frac, mem = host_usage
    cpu_segs, cpu_used, cpu_parts = _bar(host.cpu, frac * host.cpu if frac is not None else None,
                              plat and plat[0], apps and apps[0], "vCPU")  # fmt: skip
    mem_segs, mem_used, mem_parts = _bar(host.mem_gib, mem, plat and plat[1], apps and apps[1], "GiB")
    return UsageBars(cpu_segs, mem_segs, round(cpu_used, 2), round(mem_used, 2), cpu_parts, mem_parts)


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
    """x / y of CPU and memory; a container that is not running uses 0 (only an unmeasurable running one shows —)."""
    mem_mib = r["memory_bytes"] / MIB
    idle = not lv or lv.get("state") != "running"
    cpu_v = 0.0 if idle else lv["cpu_used"] if lv else None
    used: float | None = (
        0.0 if idle else float(lv["mem_used"]) / MIB if lv and lv["mem_used"] is not None else None
    )
    num = lambda v, d: "0" if idle else "—" if v is None else br(v, d)  # noqa: E731
    cpu = f"{num(cpu_v, 2)} / {br(r['cpu'], 2)} vCPU"
    mem = f"{num(used, 0)} / {br(mem_mib, 0)} MiB"
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
    pids = 0 if not lv or lv.get("state") != "running" else lv["pids"] if lv.get("pids") is not None else "—"
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
    hc = x.get("host") or {}
    cpu_l, mem_cap = (hc.get("NanoCpus") or 0) / 1e9, (hc.get("Memory") or 0) / MIB
    mem_l = mem_cap or (x["mem_limit"] or 0) / MIB
    used = 0.0 if not running else x["mem_used"] / MIB if x["mem_used"] is not None else None
    cpu_v = 0.0 if not running else x["cpu_used"]
    num = lambda v, d: "—" if v is None else "0" if v == 0 and not running else br(v, d)  # noqa: E731
    cpu = f"{num(cpu_v, 2)} / {br(cpu_l, 2)} vCPU" if cpu_l else f"{num(cpu_v, 2)} vCPU"
    mem = f"{num(used, 0)} / {br(mem_cap, 0)} MiB" if mem_cap else f"{num(used, 0)} MiB"
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


def legend(bars: UsageBars, cpu_total: float, mem_total: float) -> list[tuple[str, str, str]]:
    """(css class, name, "x vCPU · y GiB") of each part of the capacity bars, plus what is free."""
    free = (max(cpu_total - bars.cpu_used, 0.0), max(mem_total - bars.mem_used, 0.0))
    parts = [*zip(bars.cpu_parts, bars.mem_parts, strict=True), free]
    names = (("sys", "Sistema"), ("plat", "Plataforma"), ("apps", "Aplicações"), ("free", "Livre"))
    return [
        (c, n, f"{br(cpu, 2)} vCPU · {br(mem, 1)} GiB")
        for (c, n), (cpu, mem) in zip(names, parts, strict=True)
    ]
