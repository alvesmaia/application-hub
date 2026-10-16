"""Read-only preview of a `.hubapp` before deploying: what the manifest declares and what it replaces.

Only reads `manifest.json` from the zip (same defensive reader and manifest check as the pipeline);
nothing in the package runs and nothing is recorded."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from hub.models import AppRequires
from hub.ops import model as m
from hub.ops import pipeline as pl

ENVS = (("producao", m.PRODUCAO), ("staging", m.STAGING))


class PackagePreview(BaseModel):
    """Answer of `POST /api/ops/packages/preview`: only names, never values."""

    valid: bool
    error: str | None = None
    id: str = ""
    name: str = ""
    version: str = ""
    new: bool = True
    current: dict[str, str] = Field(default_factory=lambda: {k: "" for k, _ in ENVS})
    requires: AppRequires = Field(default_factory=AppRequires)


def current_versions(deployments: list[dict[str, Any]], app_id: str, registry_version: str) -> dict[str, str]:
    """Deployed version per environment (newest first in the store); production falls back to the registry."""
    out: dict[str, str] = {}
    for key, label in ENVS:
        dep = next((d for d in deployments if d.get("app") == app_id and d.get("env") == label
                    and d.get("status") == m.IMPLANTADO), None)  # fmt: skip
        out[key] = str(dep["version"]) if dep else ""
    out["producao"] = out["producao"] or registry_version
    return out


def preview(data: bytes, registered: list[Any], deployments: list[dict[str, Any]]) -> PackagePreview:
    run = pl._Run("staging", registered, None, False)
    try:
        run.files = pl.read_zip(data)
    except ValueError as e:
        run.open_err = str(e)
    state, detail = pl._step_manifest(run)
    mf = run.manifest
    if state != "ok" or mf is None:
        return PackagePreview(valid=False, error=detail)
    app_id = str(mf["id"])
    entry = next((a for a in registered if a.id == app_id), None)
    return PackagePreview(
        valid=True, id=app_id, name=str(mf.get("name") or app_id), version=str(mf.get("version") or ""),
        new=entry is None, current=current_versions(deployments, app_id, str(getattr(entry, "version", "") or "")),
        requires=AppRequires.model_validate(mf.get("requires") or {}),
    )  # fmt: skip
