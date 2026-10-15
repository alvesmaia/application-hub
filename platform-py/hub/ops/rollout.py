"""Blue/green of the apps (port of `hub-ops::rollout`): candidate `<container>__new` with the new image, readiness
`GET /{app}/_hub/ready` (a candidate without the route is refused), upstream switch in the gateway, drain of the old one,
removal and renaming of the candidate to the canonical name. Failure at any step: the candidate disappears and the active one stays."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import Callable
from typing import Any

import httpx

from hub.ops import runtime as rt
from hub.ops.model import OpsError

Progress = Callable[[int, str], None]
_DROP_HOST = ("PortBindings", "Links", "VolumesFrom", "PublishAllPorts", "ExtraHosts")


def _num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def env_dict(items: list[str]) -> dict[str, str]:
    return {k: v for k, _, v in (i.partition("=") for i in items)}


def ready_path(app: str) -> str:
    return f"/{app}/_hub/ready"


def ready_ok(status: int, headers: Any) -> bool:
    """200 and without the old `x-hub-ready: 0|false` header."""
    return status == 200 and str(headers.get("x-hub-ready", "")).strip() not in ("0", "false")


def staging_env_check(env_vars: list[dict[str, Any]], app: str) -> None:
    """Staging never receives the production database credential and needs its own database (like `staging_env_check`)."""
    has = lambda v, e: any(b["app"] == app and b["env"] == e for b in v["bindings"])  # noqa: E731
    shared = [
        v["name"]
        for v in env_vars
        if v.get("kind") == "connection_string" and has(v, "staging") and has(v, "producao")
    ]
    if shared:
        raise OpsError(
            f"Contrato de variáveis de staging: {', '.join(shared)} vale em produção e em staging. Staging nunca "
            "recebe credenciais de banco de produção — tire o vínculo de staging e provisione o banco de staging.", 422)  # fmt: skip
    db = lambda e: any((v.get("inject_as") or v["name"]) == "HUB_DB_URL" and has(v, e) for v in env_vars)  # noqa: E731
    if db("producao") and not db("staging"):
        raise OpsError("Staging sem banco próprio: provisione o banco de staging (Containers › Banco próprio de "
                       "staging) antes de implantar em staging.", 422)  # fmt: skip


def host_for(base: dict[str, Any], limits: dict[str, Any], staging: bool) -> dict[str, Any]:
    """HostConfig of the candidate: the current container's (hardening, restart, volumes) without published ports,
    with the effective limits; named volumes get the environment suffix in staging."""
    h = {k: v for k, v in base.items() if k not in _DROP_HOST and v not in (None, [], {})}
    mem = rt.memory_bytes(str(limits["memory"]))
    h.update(
        NanoCpus=int(float(limits["cpu"]) * 1e9), Memory=mem, MemorySwap=mem, PidsLimit=int(limits["pids"])
    )
    h.setdefault("RestartPolicy", {"Name": "unless-stopped", "MaximumRetryCount": 0})
    if staging and h.get("Binds"):
        h["Binds"] = [(f"{b.split(':', 1)[0]}__staging:{b.split(':', 1)[1]}" if not b.startswith("/") else b)
                      for b in h["Binds"]]  # fmt: skip
    return h


class Rollout:
    def __init__(self, engine: rt.Engine, registry: Any, client: httpx.AsyncClient,
                 app_env: Callable[[str, str], Any], env_vars: Callable[[], Any]) -> None:  # fmt: skip
        self.engine, self.registry, self.client = engine, registry, client
        self.app_env, self.env_vars = app_env, env_vars
        # F22: template for the first container of a new app (no compose) — `hub.ops.newapp.base_container`.
        self.fresh: Callable[[str], dict[str, Any] | None] = lambda _app: None
        self.locks: dict[str, asyncio.Lock] = {}

    def url_of(self, container: str) -> str:
        return os.getenv("HUB_RUNTIME_UPSTREAM", "http://{name}:8080").format(name=container)

    async def probe(self, url: str, app: str, name: str, timeout: float) -> None:
        """Waits for readiness; a 404 (missing route) is refused right away; so is a container that goes down first."""
        end, last = time.monotonic() + timeout, "sem resposta"
        while time.monotonic() < end:
            raw = await self.engine.raw(name)
            if raw is None or raw.get("State", {}).get("Running") is False:
                tail = ""
                with contextlib.suppress(OpsError):
                    tail = " · " + " | ".join(t for _, t in (await self.engine.tail(name, 3)))
                raise OpsError(f"O candidato encerrou antes de ficar pronto{tail}"[:300], 422)
            try:
                r = await self.client.get(url + ready_path(app), timeout=3)
                if ready_ok(r.status_code, r.headers):
                    return
                last = f"HTTP {r.status_code}" + (
                    f": {r.text[:100]}" if r.status_code != 200 and r.text else ""
                )
                if r.status_code == 404:
                    raise OpsError(f"O candidato não implementa a prontidão GET {ready_path(app)} (obrigatória "
                                   "pelo contrato): implantação recusada.", 422)  # fmt: skip
            except httpx.HTTPError as e:
                last = type(e).__name__
            await asyncio.sleep(_num("HUB_ROLLOUT_INTERVAL_S", 1))
        raise OpsError(
            f"O candidato não ficou pronto em {timeout:.0f} s ({last}): implantação recusada.", 422
        )

    async def apply(self, app: str, k: str, limits: dict[str, Any], version: str, image: str | None = None,
                    progress: Progress | None = None) -> dict[str, Any]:  # fmt: skip
        """Blue/green of `app` in `k` (`producao`/`staging`). Empty `image` = the current container's (applies variables)."""
        lock = self.locks.setdefault(f"{app}|{k}", asyncio.Lock())
        if lock.locked():
            raise OpsError("Já há uma implantação em andamento neste app e ambiente.", 409)
        async with lock:
            return await self._apply(app, k, limits, version, image, progress or (lambda *_: None))

    async def _base(self, app: str, k: str, old: dict[str, Any] | None) -> dict[str, Any]:
        """Container the candidate copies its settings from: the current one, else production's (staging), else a fresh spec."""
        prod = rt.container_name(app, "producao")
        base = old or (await self.engine.raw(prod) if k == "staging" else None) or self.fresh(app)
        if base is None:
            raise OpsError("Não há container de base para este app: suba-o uma vez pelo compose.", 409)
        return base

    async def _launch(self, app: str, k: str, cand: str, base: dict[str, Any], img_id: str,
                      limits: dict[str, Any], version: str, say: Progress) -> None:  # fmt: skip
        staging = k == "staging"
        carried = {} if staging or not base.get("Image") else await self._carried(base)
        env = {**carried, "HUB_APP_ID": app, "PORT": "8080", **await self.app_env(app, k)}
        await self.engine.remove(cand, 1)
        host = host_for(base.get("HostConfig") or {}, limits, staging)
        nets = list(
            (base.get("NetworkSettings", {}).get("Networks") or {}) or [host.get("NetworkMode", "default")]
        )
        say(15, f"Criando o candidato {cand}")
        await self.engine.launch(
            cand, img_id, env, _labels(base, app, k, version), host, nets, aliases=network_aliases(base)
        )

    async def _apply(self, app: str, k: str, limits: dict[str, Any], version: str, image: str | None,
                     say: Progress) -> dict[str, Any]:  # fmt: skip
        name = rt.container_name(app, k)
        old = await self.engine.raw(name)
        base = await self._base(app, k, old)
        say(5, "Conferindo a imagem")
        ref = image or str(base.get("Image", ""))
        img_id, _ = await self.engine.image_ref(ref)
        if k == "staging":
            staging_env_check(await self.env_vars(), app)
        cand = f"{name}__new"
        await self._launch(app, k, cand, base, img_id, limits, version, say)
        await self._switch(app, k, cand, version, say)
        if old is not None:
            await self._pin(old, app, k)
        say(75, "Esgotando requisições do container anterior")
        await asyncio.sleep(_num("HUB_ROLLOUT_DRAIN_S", 5))
        if old is not None:
            await self.engine.remove(name, 30)
        await self.engine.rename(cand, name)
        self.route(app, k, self.url_of(name), version)
        say(100, "Concluído")
        return {"container": name, "image": ref, "upstream": self.url_of(name)}

    async def _carried(self, base: dict[str, Any]) -> dict[str, str]:
        """What compose/the operator injected into the current container (beyond the image)."""
        try:
            _, old_img = await self.engine.image_ref(str(base.get("Image", "")))
        except OpsError:
            old_img = {}
        had = env_dict(old_img.get("Config", {}).get("Env") or [])
        return {a: b for a, b in env_dict(base.get("Config", {}).get("Env") or []).items() if had.get(a) != b}

    async def _pin(self, old: dict[str, Any], app: str, k: str) -> None:
        """Previous image pinned by name: rollback never depends on it escaping a prune."""
        pre = "keep-stg-" if k == "staging" else "keep-"
        await self.engine.pin(str(old.get("Image", "")), app, f"{pre}{str(old.get('Image', ''))[7:19]}")
        await self.engine.prune_pins(app, int(_num("HUB_KEEP_IMAGES", 3)))

    async def _switch(self, app: str, k: str, cand: str, version: str, say: Progress) -> None:
        """Waits for the candidate's readiness and routes traffic to it; a failure removes the candidate."""
        try:
            say(35, "Aguardando a prontidão do candidato")
            await self.probe(self.url_of(cand), app, cand, _num("HUB_ROLLOUT_TIMEOUT_S", 90))
            say(65, "Trocando o tráfego para o candidato")
            self.route(app, k, self.url_of(cand), version)
        except BaseException:
            await self.engine.remove(cand, 1)
            raise

    def route(self, app: str, k: str, url: str, version: str) -> None:
        if k == "staging":
            self.registry.staging[app] = url
        else:
            self.registry.set_upstream(app, url, version)


def network_aliases(base: dict[str, Any]) -> dict[str, list[str]]:
    """Aliases the replaced container had on each network (set by compose/the installer, e.g. the upstream name
    in apps.json), without the ones Docker derives from its own name and id: the candidate must answer to them too."""
    name, cid = str(base.get("Name", "")).lstrip("/"), str(base.get("Id", ""))
    nets = (base.get("NetworkSettings") or {}).get("Networks") or {}
    own = {name, cid[:12]}
    return {n: [a for a in (v or {}).get("Aliases") or [] if a not in own] for n, v in nets.items()}


def _labels(base: dict[str, Any], app: str, k: str, version: str) -> dict[str, str]:
    staging = k == "staging"
    labels: dict[str, str] = {a: b for a, b in (base.get("Config", {}).get("Labels") or {}).items()
              if not staging or not a.startswith("com.docker.compose")}  # fmt: skip
    labels |= {"hub.py": "1", "hub.app": app, "hub.env": k, "hub.version": version, "hub.role": "app"}
    if staging:  # else it inherits the image's project label; Docker Desktop groups it as a ghost project
        labels["com.docker.compose.project"] = "app-hub"
    return labels
