"""«Provisionar armazenamento»: bucket + usuário do MinIO com política restrita a ele + conexão S3 do broker.

Fala com o object store da plataforma pelo `mc` (administração: `HUB_S3_ADMIN_URL=http://usuario:senha@host:9000`;
binário em `HUB_MC_BIN`/PATH). Idempotente; desfaz em ordem inversa se um passo falhar."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from hub.connections.store import ConnError, ConnStore, valid_name
from hub.storage import StorageError

Runner = Callable[[list[str], dict[str, str]], Awaitable[tuple[int, str]]]


async def run_mc(args: list[str], env: dict[str, str]) -> tuple[int, str]:
    exe = os.getenv("HUB_MC_BIN") or shutil.which("mc") or "mc"
    proc = await asyncio.create_subprocess_exec(
        exe,
        *args,
        env={**os.environ, **env},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await asyncio.wait_for(proc.communicate(), 60)
    return proc.returncode or 0, out.decode(errors="replace")


def names(app: str, env: str) -> str:
    """Nome do bucket (= usuário do MinIO = conexão) de um app × ambiente."""
    n = f"stg-{app}" if env == "staging" else f"app-{app}"
    if (
        len(n) > 40
        or not all((c.islower() and c.isascii()) or c.isdigit() or c == "-" for c in app)
        or not valid_name(n)
    ):
        raise ConnError(f"Id de app sem nome de bucket válido (máximo 32 caracteres, a-z, 0-9 e -): {app}")
    return n


def policy(bucket: str) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetBucketLocation", "s3:ListBucket"],
                    "Resource": [f"arn:aws:s3:::{bucket}"],
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:DeleteObject",
                        "s3:AbortMultipartUpload",
                        "s3:ListMultipartUploadParts",
                    ],
                    "Resource": [f"arn:aws:s3:::{bucket}/*"],
                },
            ],
        }
    )


class ObjectStore:
    """Administração do MinIO: bucket, usuário e política (um por bucket)."""

    def __init__(self, admin_url: str | None = None, runner: Runner = run_mc) -> None:
        self.url = admin_url if admin_url is not None else os.getenv("HUB_S3_ADMIN_URL", "").strip()
        self.runner = runner

    def available(self) -> str | None:
        """Motivo da indisponibilidade ou `None`."""
        if not self.url:
            return "Object store da plataforma não configurado (HUB_S3_ADMIN_URL)."
        if self.runner is run_mc and not (os.getenv("HUB_MC_BIN") or shutil.which("mc")):
            return "Provisionamento indisponível: o cliente mc não está instalado no hub (HUB_MC_BIN)."
        return None

    @property
    def endpoint(self) -> str:
        u = urlsplit(self.url)
        return f"{u.scheme}://{u.hostname}{f':{u.port}' if u.port else ''}"

    async def mc(self, *args: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory(prefix="hub-mc-") as cfg:
            return await self.runner(["--config-dir", cfg, "--quiet", *args], {"MC_HOST_hub": self.url})

    async def bucket_exists(self, bucket: str) -> bool:
        return (await self.mc("stat", f"hub/{bucket}"))[0] == 0

    async def user_exists(self, user: str) -> bool:
        return (await self.mc("admin", "user", "info", "hub", user))[0] == 0

    async def ensure(self, bucket: str, user: str, secret: str) -> None:
        with tempfile.TemporaryDirectory(prefix="hub-pol-") as d:
            f = Path(d) / "policy.json"
            f.write_text(policy(bucket), encoding="utf-8")
            steps = [
                ("mb", "--ignore-existing", f"hub/{bucket}"),
                ("admin", "policy", "create", "hub", f"hub-{user}", str(f)),
                ("admin", "user", "add", "hub", user, secret),
                ("admin", "policy", "attach", "hub", f"hub-{user}", "--user", user),
            ]
            for step in steps:
                rc, out = await self.mc(*step)
                if rc != 0:
                    raise ConnError(f"Object store: {_scrub(out, secret)}"[:300], 502)

    async def remove_user(self, bucket: str, user: str) -> None:
        for step in (
            ("admin", "user", "remove", "hub", user),
            ("admin", "policy", "remove", "hub", f"hub-{user}"),
        ):
            await self.mc(*step)

    async def remove_bucket(self, bucket: str, force: bool) -> None:
        rc, out = await self.mc("rb", *(["--force"] if force else []), f"hub/{bucket}")
        if rc != 0:
            raise ConnError(f"Object store: {out.strip()[:200]}", 502)


def _scrub(text: str, *secrets_: str) -> str:
    for s in secrets_:
        if len(s) >= 4:
            text = text.replace(s, "••••")
    return text.strip()


async def provision(
    conns: ConnStore, store: ObjectStore, app: str, env: str, who: str, broker_url: str
) -> tuple[int, dict[str, Any]]:
    if app not in conns.apps:
        raise ConnError("Aplicação não encontrada.", 404)
    name = names(app, env)
    if reason := store.available():
        raise ConnError(reason, 503)
    existing = conns.get(name)
    rec = next((p for p in conns.storage if p["app"] == app and p["env"] == env), None)
    if rec:
        if existing is None:
            raise ConnError(
                f"A conexão {name} do armazenamento provisionado sumiu: desprovisione e provisione de novo.",
                409,
            )
        await store.ensure(rec["bucket"], rec["user"], existing["secret"])
        b = {"app": app, "env": env}
        if b not in existing["bindings"]:
            await conns.set_bindings(name, [*existing["bindings"], b])
            await conns.sync_env(who, broker_url)
        return 200, {
            "changed": False,
            "storage": rec,
            "detail": "já provisionado: bucket, usuário, política e conexão conferidos",
        }
    if existing is not None:
        raise ConnError(
            f"Já existe uma conexão {name} que não foi provisionada pelo hub: renomeie ou exclua-a antes.",
            409,
        )
    if await store.bucket_exists(name):
        raise ConnError(f"O bucket {name} já existe e não foi provisionado pelo hub: recusado.", 409)
    secret = secrets.token_hex(24)
    # O roteiro pode criar o bucket e o usuário antes de falhar: ambos entram no desfazer (o bucket é inexistente aqui;
    # um usuário que já existia nunca é tocado).
    done = ["bucket"] + ([] if await store.user_exists(name) else ["user"])
    try:
        await store.ensure(name, name, secret)
        await conns.create(
            {
                "name": name,
                "kind": "s3",
                "description": f"Provisionado pelo hub para {app} ({env}) — bucket e usuário próprios",
                "endpoint": store.endpoint,
                "bucket": name,
                "region": "us-east-1",
                "access_key": name,
                "secret": secret,
                "bindings": [{"app": app, "env": env}],
            }
        )
        done.append("connection")
        await conns.sync_env(who, broker_url)
        rec = {
            "app": app,
            "env": env,
            "bucket": name,
            "user": name,
            "connection": name,
            "created_at": int(time.time() * 1000),
            "created_by": who,
        }
        await conns.storage_update(
            lambda s: [*(p for p in s if not (p["app"] == app and p["env"] == env)), rec]
        )
    except (ConnError, StorageError) as e:
        notes = await _undo(conns, store, name, done, who, broker_url)
        raise ConnError(f"{e} · {' · '.join(notes)}" if notes else str(e), getattr(e, "status", 502)) from e
    return 201, {
        "changed": True,
        "storage": rec,
        "detail": "bucket, usuário com política restrita ao bucket e conexão criados; o app usa o broker (reinício pendente para receber HUB_CONNECTIONS)",
    }


async def _undo(
    conns: ConnStore, store: ObjectStore, name: str, done: list[str], who: str, url: str
) -> list[str]:
    notes = []
    for step in reversed(done):
        try:
            if step == "connection":
                await conns.delete(name)
                await conns.sync_env(who, url)
            elif step == "user":
                await store.remove_user(name, name)
            else:
                await store.remove_bucket(name, False)
            notes.append(f"{step} desfeito")
        except (ConnError, StorageError) as e:
            notes.append(f"{step}: não desfeito ({e})")
    return notes


async def deprovision(
    conns: ConnStore,
    store: ObjectStore,
    app: str,
    env: str,
    who: str,
    body: dict[str, Any],
    step_up: Callable[[str], Awaitable[str | None]],
    broker_url: str,
) -> dict[str, Any]:
    rec = next((p for p in conns.storage if p["app"] == app and p["env"] == env), None)
    if rec is None:
        raise ConnError("Nenhum armazenamento provisionado para este app e ambiente.", 404)
    if str(body.get("confirm", "")).strip() != rec["bucket"]:
        raise ConnError(f"Confirme digitando o nome do bucket ({rec['bucket']}).")
    if reason := store.available():
        raise ConnError(reason, 503)
    conn = conns.get(rec["connection"])
    has_objects = False
    if conn is not None:
        try:
            has_objects = bool(await cast(Any, conns.backend(conn)).ls(""))  # type: ignore[arg-type]
        except StorageError as e:
            raise ConnError(f"Object store: {e.message}", 502) from e
    if has_objects:
        if not body.get("force"):
            raise ConnError(
                f"O bucket {rec['bucket']} tem objetos: esvazie-o ou confirme a remoção com force e sua senha.",
                409,
            )
        if err := await step_up(str(body.get("password", ""))):
            raise ConnError(err, 403)
    steps: list[str] = []
    if conn is not None:
        await conns.delete(rec["connection"])
        await conns.sync_env(who, broker_url)
        steps.append(f"conexão {rec['connection']} excluída")
    await store.remove_user(rec["bucket"], rec["user"])
    steps.append(f"usuário {rec['user']} removido")
    await store.remove_bucket(rec["bucket"], has_objects)
    steps.append(
        f"bucket {rec['bucket']} apagado{' com os objetos (force + senha)' if has_objects else ' (vazio)'}"
    )
    await conns.storage_update(lambda s: [p for p in s if not (p["app"] == app and p["env"] == env)])
    return {"removed": True, "steps": steps}
