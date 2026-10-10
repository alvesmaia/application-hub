"""Apps novos implantados pelo hub (kit do desenvolvedor, F22) e o `requires` do manifesto.

- Registro: um app que não está no `apps.json` entra no registro na primeira implantação aprovada pelo pipeline e
  fica salvo em `<dados>/apps.dynamic.json` (recarregado na subida).
- Container de base: sem container do compose, o candidato nasce de um molde endurecido (raiz só leitura,
  `cap_drop ALL`, `no-new-privileges`, `/scratch` em tmpfs) na rede dos apps (`HUB_APP_NETWORK`).
- `requires`: banco (`provision.provision`), armazenamento (gancho das Conexões) e variáveis/segredos enviados
  pelo `hub deploy` — gravados como variáveis geridas `NOME_<APP>[_STAGING]` injetadas como `NOME`, só escrita.
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
    """Junta ao registro os apps implantados pelo hub; devolve os ids acrescentados."""
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
    """Registra o app novo (registro do gateway, lista de apps da Administração/Conexões e o arquivo)."""
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
    """Molde do primeiro container de um app novo (o `rollout` aplica limites e reinício)."""
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
    """Campo `config` do envio (`hub deploy`): {"variables": {NOME: valor}, "secrets": {...}}."""
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
        out[kind] = {str(k): str(v) for k, v in vals.items()}
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
    """Provisiona o que o manifesto pede e grava variáveis/segredos; devolve um resumo sem valores."""
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
