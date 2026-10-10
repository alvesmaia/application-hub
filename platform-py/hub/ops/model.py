"""Estado de Operações (`ops.json`, mesmo desenho do `hub-ops`) e transições de implantação como funções puras."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

IMPLANTADO, AGUARDANDO, REJEITADO, SUBSTITUIDO = (
    "Implantado",
    "Aguardando aprovação",
    "Rejeitado",
    "Substituído",
)
REVERTIDO, FALHOU, IMPLANTANDO = "Revertido", "Falhou", "Implantando"
STAGING, PRODUCAO = "Staging", "Produção"
Data = dict[str, Any]


class OpsError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message, self.status = message, status


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def default_policy(app: str) -> Data:
    return {
        "app": app, "schedule": "Diário 02:00", "keep": 7, "volumes": ["data"], "hooks": False,
        "destination": "Local · backups/", "envs": ["producao"], "missed": "executar", "jitter_s": 20, "tz": "",
    }  # fmt: skip


class OpsStore:
    """`ops.json` com escrita atômica; segredos de backup selados pelo cofre."""

    def __init__(self, data_dir: Path, apps: list[str], vault: Any = None) -> None:
        self.path, self.vault, self.lock = data_dir / "ops.json", vault, asyncio.Lock()
        self.db: Any = None  # StateDb: trava `ops.json` entre réplicas (pg_advisory_lock)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.apps = apps

    def read(self) -> Data:
        try:
            d: Data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        defaults: list[tuple[str, Any]] = [
            ("deployments", []), ("policies", []), ("backups", []), ("limits", {}), ("schedules", {}),
            ("maintenance", {}), ("script_approvals", []), ("secrets", {}), ("next_deploy_id", 1),
            ("next_backup_id", 1),
        ]  # fmt: skip
        for k, v in defaults:
            d.setdefault(k, v)
        have = {p["app"] for p in d["policies"]}
        d["policies"] += [default_policy(a) for a in self.apps if a not in have]
        return d

    def write(self, d: Data) -> None:
        tmp = self.path.with_suffix(f".json.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        with suppress(OSError):
            tmp.chmod(0o600)
        os.replace(tmp, self.path)

    @asynccontextmanager
    async def locked(self, write: bool = False) -> AsyncIterator[Data]:
        async with self.lock:
            if self.db is None:
                d = self.read()
                yield d
                if write:
                    self.write(d)
                return
            async with self.db.file_lock("ops.json", write=write):
                d = self.read()
                yield d
                if write:
                    self.write(d)

    def secret(self, key: str, rotate: bool = False) -> str:
        """Segredo no cofre (`ops.json` guarda só o selado); sem cofre, um novo a cada uso."""
        if not self.vault:
            return secrets.token_hex(12)
        d = self.read()
        sealed = d["secrets"].get(key)
        if sealed and not rotate:
            with suppress(Exception):
                v = str(self.vault.open_value(sealed, f"ops.secret:{key}"))
                if len(v) >= 16:
                    return v
        v = secrets.token_hex(12)
        d["secrets"][key] = self.vault.seal(v, f"ops.secret:{key}")
        self.write(d)
        return v


def _find(d: Data, i: int) -> Data:
    for x in d["deployments"]:
        if x["id"] == i:
            return x
    raise OpsError("Implantação não encontrada.", 404)


def pending_scripts(d: Data, dep: Data) -> list[Data]:
    return [
        s for s in dep.get("scripts", [])
        if not any(a["app"] == dep["app"] and a["path"] == s["path"] and a["sha384"] == s["sha384"]
                   for a in d["script_approvals"])
    ]  # fmt: skip


def push(d: Data, dep: Data) -> Data:
    dep["id"] = d["next_deploy_id"]
    d["next_deploy_id"] += 1
    d["deployments"].insert(0, dep)
    return dep


def in_progress(d: Data, app: str, env: str) -> bool:
    return any(x["app"] == app and x["env"] == env and x["status"] == IMPLANTANDO for x in d["deployments"])


def approve(d: Data, i: int) -> Data:
    dep = _find(d, i)
    if dep["status"] != AGUARDANDO:
        raise OpsError("Só implantações aguardando aprovação podem ser aprovadas.")
    if p := pending_scripts(d, dep):
        raise OpsError(
            f"O pacote traz JavaScript próprio ainda não aprovado ({', '.join(s['path'] for s in p)}): faça a "
            "revisão de segurança (Aprovar JavaScript) antes de aprovar a implantação."
        )
    if in_progress(d, dep["app"], dep["env"]):
        raise OpsError("Já há uma implantação em andamento neste app e ambiente.")
    dep["status"] = IMPLANTANDO
    return dep


def promote(d: Data, i: int, now: int) -> Data:
    src = _find(d, i)
    if src["env"] != STAGING or src["status"] != IMPLANTADO:
        raise OpsError("Só versões implantadas em staging podem ser promovidas.")
    if any(
        x["app"] == src["app"]
        and x["env"] == PRODUCAO
        and x["version"] == src["version"]
        and x["status"] == AGUARDANDO
        for x in d["deployments"]
    ):
        raise OpsError(f"A versão {src['version']} já aguarda aprovação em produção.")
    new = {**src, "env": PRODUCAO, "origin": "Promoção", "status": AGUARDANDO, "at": now,
           "note": f"promovida de staging (#{src['id']})"}  # fmt: skip
    return push(d, new)


def rollback(d: Data, i: int, now: int) -> Data:
    cur = _find(d, i)
    if cur["env"] == PRODUCAO and cur["status"] == SUBSTITUIDO:  # volta a esta versão (a escolhida na gaveta)
        if in_progress(d, cur["app"], cur["env"]):
            raise OpsError("Já há uma implantação em andamento neste app e ambiente.")
        act = next((x for x in d["deployments"] if x["app"] == cur["app"] and x["env"] == PRODUCAO
                    and x["status"] == IMPLANTADO), None)  # fmt: skip
        if act is not None and act["version"] == cur["version"]:
            raise OpsError("Esta versão já está ativa em produção.")
        new = {**cur, "origin": "Rollback", "status": IMPLANTANDO, "at": now, "runtime": "",
               "note": f"rollback de {act['version'] if act else 'produção'} para {cur['version']}"}  # fmt: skip
        return push(d, new)
    if cur["env"] != PRODUCAO or cur["status"] != IMPLANTADO:
        raise OpsError("Só a versão ativa em produção pode ser revertida.")
    if in_progress(d, cur["app"], cur["env"]):
        raise OpsError("Já há uma implantação em andamento neste app e ambiente.")
    prev = [
        x for x in d["deployments"]
        if x["app"] == cur["app"] and x["env"] == PRODUCAO and x["status"] == SUBSTITUIDO
        and x["version"] != cur["version"]
    ]  # fmt: skip
    if not prev:
        raise OpsError("Não há versão anterior implantada em produção para este app.")
    p = max(prev, key=lambda x: x["id"])
    new = {**p, "origin": "Rollback", "status": IMPLANTANDO, "at": now, "runtime": "",
           "note": f"rollback de {cur['version']} para {p['version']}"}  # fmt: skip
    return push(d, new)


def actor_person(name: str) -> str:
    i = name.rfind(" (token:")
    return name[:i] if i >= 0 and name.endswith(")") else name


def approve_scripts(
    d: Data, i: int, by: str, by_id: str, by_token: bool, now: int, allow_self: bool
) -> list[Data]:
    dep = _find(d, i)
    if dep["status"] in (REJEITADO, FALHOU):
        raise OpsError("Implantação rejeitada ou com falha: nada a aprovar.")
    if by_token or not by_id:
        raise OpsError(
            "Separação de funções: JavaScript próprio só é aprovado por uma pessoa com sessão (nunca por token)."
        )
    sub = dep.get("submitted_by_id", "")
    if not allow_self and (not sub or sub == by_id):
        who = actor_person(dep.get("submitted_by", "")) or "remetente desconhecido"
        raise OpsError(
            "Separação de funções: o JavaScript próprio precisa ser aprovado por outra pessoa, "
            f"não por quem enviou o pacote ({who})."
        )
    pend = pending_scripts(d, dep)
    if not pend:
        raise OpsError("Esta implantação não tem JavaScript próprio pendente de aprovação.")
    for s in pend:
        d["script_approvals"].append(
            {"app": dep["app"], "path": s["path"], "sha384": s["sha384"], "reason": s["reason"],
             "by": by, "at": now, "deployment": i}
        )  # fmt: skip
    return pend


def active_scripts(d: Data, app: str, env: str) -> list[Data]:
    dep = next(
        (x for x in d["deployments"] if x["app"] == app and x["env"] == env and x["status"] == IMPLANTADO),
        None,
    )
    if not dep:
        return []
    pend = pending_scripts(d, dep)
    return [s for s in dep.get("scripts", []) if s not in pend]


def human(n: int) -> str:
    b = float(n)
    for lim, u in ((1 << 30, "GB"), (1 << 20, "MB"), (1 << 10, "KB")):
        if b >= lim:
            return f"{b / lim:.1f} {u}".replace(".", ",")
    return f"{n} B"
