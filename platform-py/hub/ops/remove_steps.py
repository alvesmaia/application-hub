"""Remove an application: the steps after the optional backup, in order (each returns the lines for the job log).

The app leaves the registry first, so the supervisor neither heals nor revives it while the rest goes."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hub.connections import provision as prov
from hub.ops import newapp
from hub.ops import remove_db as rdb
from hub.ops import remove_state as rs
from hub.ops import runtime as rt
from hub.ops.model import now_ms
from hub.ops.opsctx import OpsCtx

ENVS = ("producao", "staging")


@dataclass
class RemoveRun:
    app: str
    name: str
    actor: str
    backup: bool
    keep_data: bool
    job_id: int
    kept_backup: str = ""  # the pre-removal backup file, never deleted
    lines: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


async def catalog(ops: OpsCtx, run: RemoveRun) -> list[str]:
    newapp.unregister(ops.data_dir, ops.registry, run.app)
    if run.app in ops.store.apps:
        ops.store.apps.remove(run.app)
    return ["tirado do catálogo e do gateway"]


async def containers(ops: OpsCtx, run: RemoveRun) -> list[str]:
    live = {x["name"] for x in (await ops.eng.probe(0))["live"]}
    names = [
        n for e in ENVS for n in (rt.container_name(run.app, e), rt.container_name(run.app, e) + "__new")
    ]
    gone = [n for n in names if n in live]
    for n in gone:
        await ops.eng.remove(n)
    return [f"container {n} removido" for n in gone] or ["nenhum container"]


async def images(ops: OpsCtx, run: RemoveRun) -> list[str]:
    """Every tag of `app-hub-app-<id>` (keep-*, keep-stg-*, baseline-*): an image in use only loses the tag."""
    repo = f"{rt.prefix()}{run.app}"
    tags = await ops.eng._run(lambda cl: cl.pinned(repo))
    left = []
    for ref, _ in tags:
        try:
            await ops.eng._run(lambda cl, r=ref: cl.untag(r), True)
        except Exception:  # already gone or refused by the daemon: reported, the others still go
            left.append(ref)
    kept = f" (mantidas: {', '.join(left)})" if left else ""
    return [f"imagens removidas: {len(tags) - len(left)}{kept}"]


async def _drop_storage(ops: OpsCtx, run: RemoveRun, rec: dict[str, Any]) -> str:
    """Connection, MinIO user and bucket (with its objects: the removal was confirmed with the password)."""
    conns, store = ops.app.state.conns, ops.backups.objstore
    if conns.get(rec["connection"]) is not None:
        await conns.delete(rec["connection"])
    if why := store.available():
        return f"bucket {rec['bucket']} mantido: {why}"
    await prov._remove(conns, store, rec, False, True, run.actor, "")
    return f"bucket {rec['bucket']} apagado com o usuário {rec['user']}"


async def connections(ops: OpsCtx, run: RemoveRun) -> list[str]:
    conns = ops.app.state.conns
    async with conns.mutating() as items:
        unbound = rs.strip_connections(items, run.app)
    lines = [f"conexão {n} desvinculada" for n in unbound]
    recs = [p for p in conns.storage if p["app"] == run.app]
    if run.keep_data:
        return lines + [f"bucket {p['bucket']} mantido" for p in recs]
    lines += [await _drop_storage(ops, run, p) for p in recs]
    await conns.storage_update(lambda s: [p for p in s if p["app"] != run.app])
    return lines


async def database(ops: OpsCtx, run: RemoveRun) -> list[str]:
    if run.keep_data:
        return ["banco mantido"]
    return [await rdb.drop(ops.backups, run.app, e) for e in ENVS]


async def config(ops: OpsCtx, run: RemoveRun) -> list[str]:
    """admin.json in one write (no audit per variable: the removal has its own entry)."""
    async with ops.admin.locked(True) as d:
        gone = rs.strip_admin(d, run.app)
    if run.app in ops.admin.apps:
        ops.admin.apps.remove(run.app)
    return [f"variáveis apagadas: {', '.join(gone) or 'nenhuma'}"]


def _rm(p: Path, keep: str = "") -> bool:
    """Deletes a file or a folder (all but `keep`); False when there was nothing."""
    if not p.exists():
        return False
    if p.is_file():
        p.unlink()
        return True
    for f in p.iterdir():
        if f.name != keep:
            _rm(f)
    if not any(p.iterdir()):
        shutil.rmtree(p)
    return True


async def operations(ops: OpsCtx, run: RemoveRun) -> list[str]:
    """ops.json and the files named after the app (packages, outbox cursor; volumes and backups unless kept)."""
    async with ops.store.locked(True) as d:
        pkgs = {str(x["package"]) for x in d["deployments"] if x["app"] == run.app and x.get("package")}
        rs.strip_ops(d, run.app, run.keep_data, run.kept_backup)
    root = ops.data_dir
    files = [root / "packages" / Path(p).name for p in pkgs] + [root / "outbox" / f"{run.app}.json"]
    if not run.keep_data:
        files += [
            root / "apps" / run.app,
            root / "apps" / f"{run.app}__staging",
            root / "backups" / f"{run.app}__staging",
        ]
        _rm(root / "backups" / run.app, run.kept_backup)
    gone = sum(_rm(p) for p in files)
    return [f"implantações e políticas apagadas; arquivos removidos: {gone}"]


async def people(ops: OpsCtx, run: RemoveRun) -> list[str]:
    async with ops.app.state.auth.locked(True) as d:
        revoked = rs.strip_tokens(d, run.app, now_ms())
    ops.app.state.ux.forget_app(run.app)
    ops.app.state.inbox.forget_app(run.app)
    return [f"tokens revogados: {len(revoked)}; favoritos e acessos limpos"]


STEPS = (
    ("Catálogo", catalog), ("Containers", containers), ("Imagens", images), ("Conexões", connections),
    ("Banco de dados", database), ("Configuração", config), ("Operação", operations), ("Pessoas", people),
)  # fmt: skip
