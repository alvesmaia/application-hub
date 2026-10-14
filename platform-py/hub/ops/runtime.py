"""Containers: host capacity, limits (admission control) and real state/actions via the Docker SDK (`app-hub-app-*`)."""

from __future__ import annotations

import asyncio
import builtins
import os
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from hub.ops.model import OpsError

GIB = 1024.0**3
SEP = "__"
MAX_CPU, MAX_PIDS, MAX_MEMORY = 4.0, 1024, 8 * 1024**3
_MEM = re.compile(r"^(\d+)(Ki|Mi|Gi)$")


def prefix() -> str:
    return os.getenv("HUB_CONTAINER_PREFIX", "app-hub-app-")


def container_name(app: str, env: str) -> str:
    return f"{prefix()}{app}{SEP}staging" if env == "staging" else f"{prefix()}{app}"


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


OWNED = "app-hub-"  # the only slice of the daemon the hub sees (never hub-*, apphub-devtunnel or others)


PLATFORM_DB = "app-hub-app-db"  # apps' database: matches the apps' prefix but belongs to the platform


def is_platform(name: str) -> bool:
    """The platform's own container (web, databases, objstore, graphsim…): visible, never manageable."""
    db = os.getenv("HUB_BACKUP_DB_CONTAINER", PLATFORM_DB)
    return name.startswith(OWNED) and (name in (PLATFORM_DB, db) or not name.startswith(prefix()))


def own(name: str) -> str:
    """Refuses (403) any name outside `app-hub-*`; all Docker operations go through here."""
    if not isinstance(name, str) or not name.startswith(OWNED) or "/" in name or ":" in name:
        raise OpsError(f"Operação recusada: o hub só gerencia containers {OWNED}* ({str(name)[:60]!r}).", 403)
    return name


_PODMAN: dict[str, tuple[bool, bool]] = {}  # per daemon; cgroup delegation is fixed while the hub runs


def tmpfs_podman(opts: str) -> str:
    """`rw,size=256m,uid=10001,gid=10001` → `rw,size=256m,U` (Podman does not accept uid/gid on tmpfs)."""
    parts = [o for o in opts.split(",") if o]
    keep: list[str] = [o for o in parts if not o.startswith(("uid=", "gid="))]
    return ",".join([*keep, "U"] if len(keep) < len(parts) else keep)


class _Containers:
    def __init__(self, real: Any) -> None:
        self._r = real

    def get(self, name: str) -> Any:
        return self._r.get(own(name))

    def list(self, **kw: Any) -> builtins.list[Any]:
        return [c for c in self._r.list(**kw) if str(c.name).startswith(OWNED)]


class Guarded:
    """The only Docker client surface the hub uses: containers only `app-hub-*`, images `app-hub-*` at build time."""

    def __init__(self, real: Any) -> None:
        self._cl, self.containers = real, _Containers(real.containers)

    def podman(self) -> tuple[bool, bool]:
        """(is Podman, has the `cpu` controller). Rootless Podman on RHEL 9 delegates only `memory pids` to the user: with
        a CPU limit, every build `RUN` and container creation fail. Docker: (False, True)."""
        base = str(getattr(getattr(self._cl, "api", None), "base_url", ""))
        if base not in _PODMAN:
            try:
                r = self._cl.api._get(base + "/v4.0.0/libpod/info")
                ctl = r.json()["host"]["cgroupControllers"] if r.status_code == 200 else None
                _PODMAN[base] = (ctl is not None, ctl is None or "cpu" in ctl)
            except Exception:  # no response: assumes Docker (the earlier behavior)
                return (False, True)
        return _PODMAN[base]

    def version(self) -> dict[str, Any]:
        return dict(self._cl.version())

    def info(self) -> dict[str, Any]:
        return dict(self._cl.info())

    def image(self, ref: str) -> Any:
        return self._cl.images.get(ref)

    def build(self, path: str, tag: str, **kw: Any) -> Any:
        if not tag.startswith(OWNED):
            own(tag)
        return self._cl.images.build(path=path, tag=tag, rm=True, **kw)

    def build_stream(self, path: str, tag: str, dockerfile: str, memory: int, cpus: int = 0) -> Any:
        """Builds `tag` (only `app-hub-*`) with a memory/swap cap (and CPUs) and returns the daemon's stream."""
        own(tag.split(":", 1)[0])
        limits: dict[str, Any] = {"memory": memory, "memswap": memory}
        if self.podman()[1]:
            limits["cpushares"] = 512
        if cpus > 0 and self.podman()[1]:
            limits["cpusetcpus"] = f"0-{cpus - 1}" if cpus > 1 else "0"
        return self._cl.api.build(
            path=path, tag=tag, dockerfile=dockerfile, rm=True, forcerm=True, decode=True, container_limits=limits,
        )  # fmt: skip

    def pinned(self, repo: str) -> builtins.list[tuple[str, str]]:
        """(tag, LastTagTime) of the pinned images of `repo` (`app-hub-app-*`)."""
        own(repo)
        out: builtins.list[tuple[str, str]] = []
        for im in self._cl.images.list(name=repo):
            when = str((im.attrs.get("Metadata") or {}).get("LastTagTime", ""))
            out += [(t, when) for t in im.tags if t.startswith(repo + ":")]
        return out

    def untag(self, ref: str) -> None:
        own(ref.split(":", 1)[0])
        self._cl.images.remove(ref, noprune=False)

    def pin(self, image: str, repo: str, tag: str) -> None:
        """Labels the image as `app-hub-app-*:<tag>`: a named image is not swept by `image prune`."""
        own(repo)
        self._cl.images.get(image).tag(repo, tag)

    def create(self, image: str, name: str, **spec: Any) -> Any:
        own(name)
        podman, cpu = self.podman()
        host = spec.get("host_config")
        if isinstance(host, dict) and podman:
            host = dict(host)
            if not cpu:
                for k in ("NanoCpus", "CpuShares", "CpuQuota", "CpuPeriod", "CpusetCpus"):
                    host.pop(k, None)
            if host.get("Tmpfs"):  # Podman refuses `uid=`/`gid=`; `U` gives the tmpfs to the container's user
                host["Tmpfs"] = {path: tmpfs_podman(opts) for path, opts in host["Tmpfs"].items()}
            spec["host_config"] = host
        self._cl.api.create_container(image, name=name, **spec)
        return self.containers.get(name)

    def connect(self, network: str, container: str, **kw: Any) -> None:
        own(container)
        self._cl.api.connect_container_to_network(container, network, **kw)


def _default_client() -> Any:
    import docker

    return docker.from_env(timeout=30)


class Engine:
    """Thin layer over the Docker SDK; `factory` is swappable (tests). Never raises in `probe`."""

    def __init__(self, factory: Callable[[], Any] | None = None) -> None:
        self._cache: tuple[float, dict[str, Any]] | None = None
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
        f = self._factory
        out = await self._probe()
        if f is self._factory:
            self._cache = (time.monotonic(), out)
        return out

    def invalidate(self) -> None:
        self._cache = None

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

    async def build(  # noqa: C901
        self, path: str, tag: str, dockerfile: str, memory: int, on_line: Callable[[str], None],
        cpus: int = 0, timeout_s: float = 0,
    ) -> str:  # fmt: skip
        """Builds the package image with a memory/CPU cap and a deadline, passing the log through line by line. Returns the ID."""
        loop = asyncio.get_running_loop()
        deadline = time.monotonic() + timeout_s if timeout_s > 0 else 0.0

        def go(cl: Any) -> str:
            last = ""
            stream = cl.build_stream(path, tag, dockerfile, memory, cpus)
            for ch in stream:
                if deadline and time.monotonic() > deadline:
                    getattr(stream, "close", lambda: None)()  # closing the stream cancels the build
                    raise OpsError(f"Build falhou: tempo limite de {int(timeout_s)} s excedido", 422)
                if ch.get("error"):
                    raise OpsError(f"Build falhou: {str(ch['error']).strip()}"[:300], 422)
                if ch.get("stream"):
                    for ln in str(ch["stream"]).splitlines():
                        if ln.strip():
                            loop.call_soon_threadsafe(on_line, ln.rstrip())
                if ch.get("aux", {}).get("ID"):
                    last = str(ch["aux"]["ID"])
            return last

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
        networks: list[str], start: bool = True,
    ) -> None:  # fmt: skip
        def go(cl: Any) -> None:
            c = cl.create(image, name, environment=[f"{k}={v}" for k, v in env.items()], labels=labels,
                          host_config={**host, "NetworkMode": networks[0]})  # fmt: skip
            for n in networks[1:]:
                cl.connect(n, name)
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


def human_duration(sec: float) -> str:
    """Like `docker ps` (HumanDuration)."""
    s = int(sec)
    if s < 1:
        return "Less than a second"
    if s < 60:
        return f"{s} seconds"
    mi = s // 60
    if mi == 1:
        return "About a minute"
    if mi < 60:
        return f"{mi} minutes"
    h = round(s / 3600)
    if h == 1:
        return "About an hour"
    if h < 48:
        return f"{h} hours"
    d = h // 24
    if d < 14:
        return f"{d} days"
    if d < 60:
        return f"{d // 7} weeks"
    return f"{d // 30} months"


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


def level_of(kind: str, text: str) -> str:
    t = text.lower()
    return "ERROR" if kind == "stderr" or "error" in t else "WARN" if "warn" in t else "INFO"


def uptime_of(status: str) -> str:
    if not status.startswith("Up "):
        return "—"
    s = status[3:].split(" (")[0]
    for a, b in (("Less than a second", "< 1 s"), ("About a minute", "~1 min"), ("About an hour", "~1 h"),
                 (" seconds", " s"), (" second", " s"), (" minutes", " min"), (" minute", " min"),
                 (" hours", " h"), (" hour", " h"), (" days", " dias"), (" day", " dia"), (" weeks", " semanas"),
                 (" week", " semana"), (" months", " meses"), (" month", " mês")):  # fmt: skip
        s = s.replace(a, b)
    return s


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
