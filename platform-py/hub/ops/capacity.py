"""Container capacity: memory sizes, per-app limits, host budget and admission control."""

from __future__ import annotations

import os
import re
from typing import Any

from hub.ops.model import OpsError

GIB = 1024.0**3
MAX_CPU, MAX_PIDS, MAX_MEMORY = 4.0, 1024, 8 * 1024**3
_MEM = re.compile(r"^(\d+)(Ki|Mi|Gi)$")


def memory_bytes(s: str) -> int:
    m = _MEM.match(s)
    if not m:
        raise OpsError(f"Limite inválido: memória {s!r} (use 512Mi ou 1Gi).")
    return int(m.group(1)) * {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}[m.group(2)]


def resources_of(entry: Any, limits: dict[str, Any]) -> dict[str, Any]:
    """Effective limits: edited in Containers or taken from the manifest."""
    if entry.id in limits:
        return dict(limits[entry.id])
    r = (entry.manifest.model_extra or {}).get("resources") if entry.manifest else None
    return {"cpu": 0.5, "memory": "256Mi", "pids": 100, **(r or {})}


def validate_limits(l: dict[str, Any]) -> None:  # noqa: E741
    try:
        cpu, pids = float(l["cpu"]), int(l["pids"])
        mem = memory_bytes(str(l["memory"]))
    except (KeyError, TypeError, ValueError) as e:
        raise OpsError("Limite inválido: informe cpu, memory e pids.") from e
    if not 0 < cpu <= MAX_CPU:
        raise OpsError(f"Limite inválido: cpu {cpu}.")
    if mem > MAX_MEMORY or mem <= 0:
        raise OpsError(f"Limite inválido: memory {l['memory']}.")
    if not 0 < pids <= MAX_PIDS:
        raise OpsError(f"Limite inválido: pids {pids}.")


def _num(name: str) -> float | None:
    try:
        v = float(os.environ[name])
    except (KeyError, ValueError):
        return None
    return v if v >= 0 else None


class Host:
    """Host capacity and system/platform reserves (vCPU, GiB)."""

    def __init__(self, cpu: float | None = None, mem_gib: float | None = None) -> None:
        cc, mc = _num("HUB_HOST_CPU"), _num("HUB_HOST_MEMORY_GIB")
        self.cpu = cc or cpu or float(os.cpu_count() or 4)
        self.mem_gib = mc or mem_gib or 16.0
        self.system_cpu = _num("HUB_RESERVE_SYSTEM_CPU") or 0.5
        self.system_mem_gib = _num("HUB_RESERVE_SYSTEM_MEMORY_GIB") or 1.0
        self.platform_cpu = _num("HUB_RESERVE_PLATFORM_CPU") or 1.0
        self.platform_mem_gib = _num("HUB_RESERVE_PLATFORM_MEMORY_GIB") or 2.0
        self.source = (
            f"{'CPU configurada' if cc else 'CPU do engine' if cpu else 'CPU detectada'} · "
            f"{'memória configurada' if mc else 'memória do engine' if mem_gib else 'memória detectada'}"
        )

    @property
    def available_cpu(self) -> float:
        return self.cpu - self.system_cpu - self.platform_cpu

    @property
    def available_mem_gib(self) -> float:
        return self.mem_gib - self.system_mem_gib - self.platform_mem_gib

    def as_json(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("cpu", "mem_gib", "system_cpu", "system_mem_gib", "platform_cpu",
                                              "platform_mem_gib", "source")}  # fmt: skip


def allocated(entries: list[Any], limits: dict[str, Any]) -> tuple[float, float]:
    cpu = mem = 0.0
    for e in entries:
        r = resources_of(e, limits)
        cpu += float(r["cpu"])
        mem += memory_bytes(str(r["memory"])) / GIB
    return cpu, mem


def admit(host: Host, entries: list[Any], limits: dict[str, Any], app: str, new: dict[str, Any]) -> None:
    cpu, mem = allocated(entries, {**limits, app: new})
    br = lambda v: f"{v:.2f}".replace(".", ",")  # noqa: E731
    if cpu > host.available_cpu + 1e-9:
        raise OpsError(
            f"Capacidade insuficiente: as aplicações somariam {br(cpu)} vCPU, mas o host tem "
            f"{br(host.available_cpu)} vCPU disponíveis após as reservas de sistema e plataforma."
        )
    if mem > host.available_mem_gib + 1e-9:
        raise OpsError(
            f"Capacidade insuficiente: as aplicações somariam {br(mem)} GiB de memória, mas o host tem "
            f"{br(host.available_mem_gib)} GiB disponíveis após as reservas."
        )
