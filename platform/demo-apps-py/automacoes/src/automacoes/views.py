"""What the screens and the API need from a process: its package's fields as a form spec."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from automacoes import packages, processes
from automacoes.fields import FieldSpec
from automacoes.model import Package, Process


@dataclass(frozen=True)
class FormSpec:
    """A runnable process: ``key`` (process id), name, description and the generated form."""

    key: str
    name: str
    description: str
    fields: tuple[FieldSpec, ...]
    defaults: dict[str, Any] = field(default_factory=dict)
    process: Process | None = None
    package: Package | None = None


def form_of(p: Process, pkg: Package) -> FormSpec:
    return FormSpec(str(p.id), p.name, pkg.description, pkg.specs, processes.field_defaults(pkg, p), p, pkg)


async def runnable(pool: Any, pid: int | None, admin: bool) -> FormSpec | None:
    """The process ``pid`` with its pinned package; ``None`` if missing, disabled (unless admin) or
    its package version is gone."""
    p = await processes.get_process(pool, pid) if pid is not None else None
    if p is None or not (p.enabled or admin):
        return None
    pkg = await packages.get_package(pool, p.package_name, p.package_version)
    return form_of(p, pkg) if pkg is not None else None


async def catalog(pool: Any) -> list[FormSpec]:
    """Enabled processes whose package version exists, by name."""
    pkgs = {(k.name, k.version): k for k in await packages.list_packages(pool)}
    out = []
    for p in await processes.list_processes(pool, only_enabled=True):
        pkg = pkgs.get((p.package_name, p.package_version))
        if pkg is not None:
            out.append(form_of(p, pkg))
    return out
