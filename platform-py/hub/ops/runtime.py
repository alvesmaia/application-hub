"""Containers: names, platform guard and real state/actions via the Docker SDK (`app-hub-app-*`)."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from hub.ops import appnet
from hub.ops.capacity import (  # re-exported: callers use rt.Host, rt.admit, …
    GIB as GIB,
)
from hub.ops.capacity import (
    MAX_CPU as MAX_CPU,
)
from hub.ops.capacity import (
    MAX_MEMORY as MAX_MEMORY,
)
from hub.ops.capacity import (
    MAX_PIDS as MAX_PIDS,
)
from hub.ops.capacity import (
    Host as Host,
)
from hub.ops.capacity import (
    admit as admit,
)
from hub.ops.capacity import (
    allocated as allocated,
)
from hub.ops.capacity import (
    memory_bytes as memory_bytes,
)
from hub.ops.capacity import (
    resources_of as resources_of,
)
from hub.ops.capacity import (
    validate_limits as validate_limits,
)
from hub.ops.docker_guard import OWNED as OWNED
from hub.ops.docker_guard import Guarded as Guarded
from hub.ops.docker_guard import own as own
from hub.ops.model import OpsError
from hub.ops.runtime_text import human_duration as human_duration
from hub.ops.runtime_text import level_of as level_of
from hub.ops.runtime_text import uptime_of as uptime_of

SEP = "__"


def prefix() -> str:
    return os.getenv("HUB_CONTAINER_PREFIX", "app-hub-app-")


def container_name(app: str, env: str) -> str:
    return f"{prefix()}{app}{SEP}staging" if env == "staging" else f"{prefix()}{app}"


PLATFORM_DB = "app-hub-app-db"  # apps' database: matches the apps' prefix but belongs to the platform


def is_platform(name: str) -> bool:
    """The platform's own container (web, databases, objstore, graphsim…): visible, never manageable."""
    db = os.getenv("HUB_BACKUP_DB_CONTAINER", PLATFORM_DB)
    return name.startswith(OWNED) and (name in (PLATFORM_DB, db) or not name.startswith(prefix()))


def _consume_build(stream: Any, deadline: float, timeout_s: float, emit: Callable[[str], Any]) -> str:
    """Reads the daemon's build stream: log lines go to `emit`, errors and the deadline raise; returns the image ID."""
    last = ""
    for ch in stream:
        if deadline and time.monotonic() > deadline:
            getattr(stream, "close", lambda: None)()  # closing the stream cancels the build
            raise OpsError(f"Build falhou: tempo limite de {int(timeout_s)} s excedido", 422)
        if ch.get("error"):
            raise OpsError(f"Build falhou: {str(ch['error']).strip()}"[:300], 422)
        for ln in str(ch.get("stream") or "").splitlines():
            if ln.strip():
                emit(ln.rstrip())
        if ch.get("aux", {}).get("ID"):
            last = str(ch["aux"]["ID"])
    return last


def _default_client() -> Any:
    import docker

    return docker.from_env(timeout=30)


class Engine:
    """Thin layer over the Docker SDK; `factory` is swappable (tests). Never raises in `probe`."""

    def __init__(self, factory: Callable[[], Any] | None = None) -> None:
        self._cache: tuple[float, dict[str, Any]] | None = None
        self._gen = 0
        self._flight: asyncio.Task[dict[str, Any]] | None = None
        self.factory = factory or _default_client

    @property
    def factory(self) -> Callable[[], Any]:
        return self._factory

    @factory.setter
    def factory(self, f: Callable[[], Any]) -> None:
        self._factory, self._cache, self._flight = f, None, None  # trocar o cliente descarta o cache

    async def _run(self, fn: Callable[[Any], Any], mutates: bool = False) -> Any:
        try:
            return await asyncio.to_thread(lambda: fn(Guarded(self.factory())))
        finally:
            if mutates:
                self.invalidate()

    @staticmethod
    def _live(c: Any, stats: dict[str, Any] | None) -> dict[str, Any]:
        cpu = mem = pids = limit = None
        if stats:
            cs, pc = stats.get("cpu_stats", {}), stats.get("precpu_stats", {})
            cd = cs.get("cpu_usage", {}).get("total_usage", 0) - pc.get("cpu_usage", {}).get("total_usage", 0)
            sd = cs.get("system_cpu_usage", 0) - pc.get("system_cpu_usage", 0)
            cpu = cd / sd * (cs.get("online_cpus") or 1) if sd > 0 else 0.0
            mem, limit = (
                stats.get("memory_stats", {}).get("usage"),
                stats.get("memory_stats", {}).get("limit"),
            )
            pids = stats.get("pids_stats", {}).get("current")
        try:  # the image may have been removed while the container runs: fall back to Config.Image
            tags = c.image.tags if getattr(c, "image", None) else []
        except Exception:
            tags = []
        return {"name": c.name, "state": c.status, "status": _status_text(c), "image": (tags[0] if tags else
                str(c.attrs.get("Config", {}).get("Image", ""))), "cpu_used": cpu, "mem_used": mem,
                "mem_limit": limit, "pids": pids, "host": c.attrs.get("HostConfig", {}),
                "networks": list(c.attrs.get("NetworkSettings", {}).get("Networks") or {})}  # fmt: skip

    async def probe(self, max_age: float | None = None) -> dict[str, Any]:
        """Cached daemon state (TTL `HUB_RUNTIME_TTL_S`, 8 s); when expired, returns the old one and refreshes in the background.
        `max_age=0` forces a fresh read (supervisor). Simultaneous reads share a single call."""
        ttl = float(os.getenv("HUB_RUNTIME_TTL_S", "8") or 8) if max_age is None else max_age
        c = self._cache
        if c and time.monotonic() - c[0] < ttl:
            return c[1]
        if not self._flight or self._flight.done():
            self._flight = asyncio.create_task(self._refresh())
        if c and max_age is None and c[1]["reachable"]:
            return c[1]  # serves the old one; the refresh finishes in the background
        return await asyncio.shield(self._flight)

    async def _refresh(self) -> dict[str, Any]:
        f, gen = self._factory, self._gen
        out = await self._probe()
        if f is self._factory and gen == self._gen:  # a mutation during the read makes this result stale
            self._cache = (time.monotonic(), out)
        return out

    def invalidate(self) -> None:
        """After a mutation: drops the cache and any read already in flight (it saw the old state)."""
        self._cache, self._flight = None, None
        self._gen += 1

    async def _probe(self) -> dict[str, Any]:
        def go(cl: Any) -> dict[str, Any]:
            v = cl.version()
            info = cl.info()
            cs = [
                c for c in cl.containers.list(all=True, filters={"name": OWNED}) if c.name.startswith(OWNED)
            ]

            def one(c: Any) -> dict[str, Any]:
                st = None
                if c.status == "running":
                    try:
                        st = c.stats(stream=False)
                    except Exception:
                        st = None
                return self._live(c, st)

            with ThreadPoolExecutor(max_workers=min(16, len(cs) or 1)) as ex:
                out = list(ex.map(one, cs))
            plat = (v.get("Platform") or {}).get("Name", "Engine")
            return {"reachable": True, "socket": os.getenv("HUB_CONTAINER_SOCKET", "/var/run/docker.sock"),
                    "version": f"{plat} {v.get('Version', '')}", "error": "",
                    "live": [x for x in out if not is_platform(x["name"])],
                    "platform": [x for x in out if is_platform(x["name"])],
                    "ncpu": info.get("NCPU"), "mem_total": info.get("MemTotal")}  # fmt: skip

        try:
            return dict(await self._run(go))
        except Exception as e:
            return {"reachable": False, "socket": os.getenv("HUB_CONTAINER_SOCKET", "/var/run/docker.sock"),
                    "version": "", "error": str(e)[:200], "live": [], "platform": [], "ncpu": None, "mem_total": None}  # fmt: skip

    async def action(self, name: str, action: str) -> None:
        if action not in ("start", "stop", "restart"):
            raise OpsError("Ação inválida.", 404)

        def go(cl: Any) -> None:
            getattr(cl.containers.get(name), action)()

        try:
            await self._run(go, True)
        except Exception as e:
            raise OpsError(f"Falha no runtime: {e}"[:300], 503) from e

    async def update_limits(self, name: str, cpu: float, memory: str, pids: int) -> bool:
        def go(cl: Any) -> bool:
            try:
                c = cl.containers.get(name)
            except OpsError:
                raise
            except Exception:
                return False
            mem = memory_bytes(memory)
            lim: dict[str, Any] = {"mem_limit": mem, "memswap_limit": mem, "pids_limit": pids}
            if cl.podman()[1]:
                lim["nano_cpus"] = int(cpu * 1e9)
            c.update(**lim)
            return True

        return bool(await self._run(go))

    async def tail(self, name: str, n: int) -> list[tuple[str, str]]:
        def go(cl: Any) -> list[tuple[str, str]]:
            raw = (
                cl.containers.get(name)
                .logs(tail=max(1, min(n, 2000)), timestamps=False)
                .decode(errors="replace")
            )
            return [("stdout", ln) for ln in raw.splitlines()]

        try:
            return list(await self._run(go))
        except Exception as e:
            raise OpsError(f"Logs indisponíveis: {e}"[:300], 503) from e

    # ---- lifecycle (blue/green, staging) ---------------------------------------------------------
    async def raw(self, name: str) -> dict[str, Any] | None:
        """Raw container attributes (or `None` if it does not exist)."""

        def go(cl: Any) -> dict[str, Any] | None:
            try:
                return dict(cl.containers.get(name).attrs)
            except OpsError:
                raise
            except Exception:
                return None

        return await self._run(go)  # type: ignore[no-any-return]

    async def image_ref(self, ref: str) -> tuple[str, dict[str, Any]]:
        """Resolves the package image (local only): `name@sha256:<hex>` is valid by the repository digest or by the ID."""

        def go(cl: Any) -> tuple[str, dict[str, Any]]:
            tries = [ref]
            if "@sha256:" in ref:
                tries.append("sha256:" + ref.rsplit("@sha256:", 1)[1])
            for t in tries:
                try:
                    im = cl.image(t)
                except Exception:  # noqa: S112 - tenta a próxima forma
                    continue
                return str(im.id), dict(im.attrs)
            raise OpsError(
                f"Imagem não encontrada no daemon (o runtime só usa imagens locais): {ref}"[:300], 422
            )

        return await self._run(go)  # type: ignore[no-any-return]

    async def prune_pins(self, app: str, keep: int) -> list[str]:
        """Keeps only the `keep` most recent `keep-*` pins per app/environment (`keep-stg-*` = staging); never raises.
        An image in use by a container is not deleted by the daemon (it only loses the tag, if it has another one)."""
        repo = f"{prefix()}{app}"
        try:
            tags = await self._run(lambda cl: cl.pinned(repo))
        except Exception:
            return []
        gone: list[str] = []
        for pre in ("keep-stg-", "keep-"):
            mine = sorted(
                (t for t in tags if t[0].split(":", 1)[1].startswith(pre)
                 and (pre == "keep-stg-" or not t[0].split(":", 1)[1].startswith("keep-stg-"))),
                key=lambda t: t[1], reverse=True,
            )  # fmt: skip
            for ref, _ in mine[max(keep, 1) :]:
                try:
                    await self._run(lambda cl, r=ref: cl.untag(r))
                    gone.append(ref)
                except Exception:  # noqa: S112 — in use or already removed
                    continue
        return gone

    async def pin(self, image_id: str, app: str, label: str) -> str:
        """Pins `image_id` as `app-hub-app-<app>:<label>` (returns the reference); never raises."""
        repo = f"{prefix()}{app}"
        try:
            await self._run(lambda cl: cl.pin(image_id, repo, label))
        except Exception:  # pinning is a precaution; the deployment continues
            return ""
        return f"{repo}:{label}"

    async def build(
        self, path: str, tag: str, dockerfile: str, memory: int, on_line: Callable[[str], None],
        cpus: int = 0, timeout_s: float = 0,
    ) -> str:  # fmt: skip
        """Builds the package image with a memory/CPU cap and a deadline, passing the log through line by line. Returns the ID."""
        loop = asyncio.get_running_loop()
        deadline = time.monotonic() + timeout_s if timeout_s > 0 else 0.0

        def go(cl: Any) -> str:
            stream = cl.build_stream(path, tag, dockerfile, memory, cpus, appnet.build_args())
            return _consume_build(
                stream, deadline, timeout_s, lambda ln: loop.call_soon_threadsafe(on_line, ln)
            )

        try:
            return str(await self._run(go))
        except OpsError:
            raise
        except Exception as e:
            raise OpsError(f"Build falhou: {e}"[:300], 422) from e

    async def has_image(self, ref: str) -> bool:
        try:
            await self.image_ref(ref)
        except OpsError:
            return False
        return True

    async def launch(
        self, name: str, image: str, env: dict[str, str], labels: dict[str, str], host: dict[str, Any],
        networks: list[str], start: bool = True, aliases: dict[str, list[str]] | None = None,
    ) -> None:  # fmt: skip
        al = aliases or {}
        first = (
            {"EndpointsConfig": {networks[0]: {"Aliases": al[networks[0]]}}} if al.get(networks[0]) else None
        )

        def go(cl: Any) -> None:
            full = appnet.container_env() | env  # corporate proxy/CA (install.sh); the app's values win
            c = cl.create(image, name, environment=[f"{k}={v}" for k, v in full.items()], labels=labels,
                          host_config={**host, "NetworkMode": networks[0]}, networking_config=first)  # fmt: skip
            for n in networks[1:]:
                cl.connect(n, name, aliases=al.get(n) or None)
            if start:
                c.start()

        try:
            await self._run(go, True)
        except OpsError:
            raise
        except Exception as e:
            raise OpsError(f"Falha ao criar {name}: {e}"[:300], 503) from e

    async def rename(self, old: str, new: str) -> None:
        own(new)
        await self._run(lambda cl: cl.containers.get(old).rename(new), True)

    async def remove(self, name: str, stop_timeout: int = 10) -> None:
        def go(cl: Any) -> None:
            try:
                c = cl.containers.get(name)
            except OpsError:
                raise
            except Exception:
                return
            if c.status == "running":
                c.stop(timeout=stop_timeout)
            c.remove(force=True)

        await self._run(go, True)

    async def tail_since(self, name: str, since: int) -> list[tuple[int | None, str]]:
        """New lines (stdout+stderr) since `since` (epoch s), with the instant in ms."""

        def go(cl: Any) -> list[tuple[int | None, str]]:
            raw = cl.containers.get(name).logs(since=since, timestamps=True).decode(errors="replace")
            out: list[tuple[int | None, str]] = []
            for ln in raw.splitlines():
                ts, _, text = ln.partition(" ")
                try:
                    ms: int | None = int(
                        datetime.fromisoformat(ts[:26].rstrip("Z") + "+00:00").timestamp() * 1000
                    )
                except ValueError:
                    ms, text = None, ln
                out.append((ms, text))
            return out

        return list(await self._run(go))

    async def inspect(self, name: str) -> dict[str, Any]:
        def go(cl: Any) -> dict[str, Any]:
            return dict(cl.containers.get(name).attrs)

        try:
            return _redact(await self._run(go))
        except Exception as e:
            raise OpsError(f"Inspect indisponível: {e}"[:300], 503) from e


def _status_text(c: Any) -> str:
    """`Up 3 hours (healthy)` (`docker ps` text), rebuilt from the inspect."""
    st = c.attrs.get("State", {})
    if c.status != "running":
        return str(c.status)
    try:
        t0 = datetime.fromisoformat(str(st.get("StartedAt", ""))[:19] + "+00:00")
        up = human_duration((datetime.now(UTC) - t0).total_seconds())
    except ValueError:
        up = "running"
    health = (st.get("Health") or {}).get("Status")
    return f"Up {up}" + (f" ({health})" if health else "")


def _redact(attrs: dict[str, Any]) -> dict[str, Any]:
    cfg = attrs.get("Config")
    if isinstance(cfg, dict) and isinstance(cfg.get("Env"), list):
        cfg["Env"] = [f"{e.split('=', 1)[0]}=••••" for e in cfg["Env"]]
    return attrs


def _tar(files: dict[str, bytes]) -> bytes:
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, data in files.items():
            ti = tarfile.TarInfo(name.lstrip("/"))
            ti.size, ti.mode = len(data), 0o600
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


async def exec_in(
    engine: Engine, container: str, cmd: list[str], env: dict[str, str] | None = None,
    files: dict[str, bytes] | None = None, user: str | None = None,
) -> tuple[int, bytes]:  # fmt: skip
    """Runs `cmd` in a container (`docker exec`); `files` (paths in /tmp) go in before and come out after."""

    def go(cl: Any) -> tuple[int, bytes]:
        c = cl.containers.get(container)
        if files:
            c.put_archive("/", _tar(files))
        try:
            r = c.exec_run(cmd, environment=env or {}, user=user or "", demux=False)
            return int(r.exit_code or 0), bytes(r.output or b"")
        finally:
            if files:
                c.exec_run(["rm", "-f", *files], user="root")

    try:
        rc, out = await engine._run(go)
        return int(rc), bytes(out)
    except Exception as e:
        raise OpsError(f"Falha no runtime: {e}"[:300], 503) from e
