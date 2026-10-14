"""The only Docker client surface the hub uses (`Guarded`): every container and image name must be `app-hub-*`."""

from __future__ import annotations

import builtins
from typing import Any

from hub.ops.model import OpsError

OWNED = "app-hub-"  # the only slice of the daemon the hub sees (never hub-*, apphub-devtunnel or others)


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
