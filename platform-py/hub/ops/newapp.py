"""New apps deployed by the hub (developer kit, F22) and the manifest's `requires`.

- Registry: an app that is not in `apps.json` joins the registry on its first deployment approved by the pipeline and
  is saved in `<data>/apps.dynamic.json` (reloaded on startup).
- Base container: without a compose container, the candidate starts from a hardened template (read-only root,
  `cap_drop ALL`, `no-new-privileges`, `/scratch` on tmpfs) on the apps' network (`HUB_APP_NETWORK`).
- `requires`: database (`provision.provision`), storage (Connections hook) and variables/secrets sent
  by `hub deploy` — stored as managed variables `NAME_<APP>[_STAGING]` injected as `NAME`, write-only.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from hub.admin.config import env_suffix
from hub.gateway.registry import AppEntry, Registry
from hub.models import AppManifest, AppRequires
from hub.ops.model import OpsError
from hub.ops.runtime import container_name

DYNAMIC = "apps.dynamic.json"


def upstream_of(app_id: str) -> str:
    return f"http://{container_name(app_id, 'producao')}:8080"


def _entry(m: AppManifest) -> AppEntry:
    return AppEntry(id=m.id, name=m.name or m.id, upstream=upstream_of(m.id), version=str(m.version or ""),
                    max_upload_mb=int(m.limits.get("max_upload_mb", 16)), manifest=m)  # fmt: skip


def load_dynamic(data_dir: Path, registry: Registry) -> list[str]:
    """Adds the apps deployed by the hub to the registry; returns the ids added."""
    p = data_dir / DYNAMIC
    if not p.is_file():
        return []
    added = []
    for raw in json.loads(p.read_text(encoding="utf-8")):
        m = AppManifest.model_validate(raw)
        if registry.get(m.id) is None:
            registry.add(_entry(m))
            added.append(m.id)
    return added


def register(data_dir: Path, registry: Registry, apps: list[str], manifest: dict[str, Any]) -> bool:
    """Registers the new app (gateway registry, Administration/Connections app list, and the file)."""
    m = AppManifest.model_validate(manifest)
    if registry.get(m.id) is not None:
        return False
    registry.add(_entry(m))
    if m.id not in apps:
        apps.append(m.id)
    p = data_dir / DYNAMIC
    cur = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
    cur = [x for x in cur if x.get("id") != m.id] + [manifest]
    p.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def is_dynamic(data_dir: Path, app_id: str) -> bool:
    p = data_dir / DYNAMIC
    return p.is_file() and any(x.get("id") == app_id for x in json.loads(p.read_text(encoding="utf-8")))


def base_container() -> dict[str, Any]:
    """Template for the first container of a new app (`rollout` applies limits and restart)."""
    net = os.getenv("HUB_APP_NETWORK", "app-hub_default")
    return {
        "Image": "",
        "Config": {"Env": [], "Labels": {"hub.py": "1"}},
        "HostConfig": {
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "Tmpfs": {"/scratch": "rw,size=256m,uid=10001,gid=10001"},
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
        },
        "NetworkSettings": {"Networks": {net: {}}},
    }


def var_name(app: str, env: str, name: str) -> str:
    return f"{name}_{env_suffix(app)}" + ("_STAGING" if env == "staging" else "")


def parse_config(raw: str) -> dict[str, dict[str, str]]:
    """`config` field of the submission (`hub deploy`): {"variables": {NAME: value}, "secrets": {...}}."""
    if not raw.strip():
        return {"variables": {}, "secrets": {}}
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise OpsError("config inválido (JSON).") from e
    if not isinstance(data, dict):
        raise OpsError("config inválido.")
    out: dict[str, dict[str, str]] = {"variables": {}, "secrets": {}}
    for kind in out:
        vals = data.get(kind) or {}
        if not isinstance(vals, dict) or len(vals) > 64:
            raise OpsError(f"config.{kind} inválido.")
        out[kind] = {str(k): str(v) for k, v in vals.items() if v not in ("", None)}  # empty = keep current
    return out


async def apply_requires(
    app: str,
    env: str,
    manifest: dict[str, Any],
    config: dict[str, dict[str, str]],
    actor: str,
    cfg: Any,
    database: Callable[[str, str], Awaitable[dict[str, Any]]],
    storage: Callable[[str, str], Awaitable[dict[str, Any]]] | None,
) -> dict[str, Any]:
    """Provisions what the manifest asks for and stores variables/secrets; returns a summary without values."""
    req = AppRequires.model_validate(manifest.get("requires") or {})
    done: dict[str, Any] = {"database": None, "storage": None, "variables": [], "secrets": []}
    if req.database:
        p = await database(app, env)
        done["database"] = p.get("database")
    if req.storage:
        if storage is None:
            raise OpsError("armazenamento indisponível neste hub", 503)
        s = await storage(app, env)
        done["storage"] = (s.get("storage") or {}).get("bucket")
    for kind, allowed in (("variables", req.variables), ("secrets", req.secrets)):
        extra = sorted(set(config.get(kind, {})) - set(allowed))
        if extra:
            raise OpsError(f"{', '.join(extra)} não declarado(s) em requires.{kind}")
        for name, value in sorted(config.get(kind, {}).items()):
            await cfg.upsert_managed(
                actor,
                {"name": var_name(app, env, name), "value": value, "inject_as": name,
                 "kind": "secreta" if kind == "secrets" else "publica",
                 "bindings": [{"app": app, "env": env}], "managed": True},
                "Segredo definido na implantação" if kind == "secrets" else "Variável definida na implantação",
            )  # fmt: skip
            done[kind].append(name)
    return done
