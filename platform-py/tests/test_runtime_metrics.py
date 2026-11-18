"""Containers page on an engine that cannot measure each container (rootless Podman with cgroupfs, no cgroup
delegation): no made-up numbers, the real socket, local image names and the capacity overcommit."""

from __future__ import annotations

from typing import Any, override

import pytest
from fastapi.testclient import TestClient
from test_ops import FakeContainer, FakeDocker, hub  # noqa: F401 (fixture)

from hub.ops import runtime as rt

MIB = 1 << 20


class SharedCgroupContainer(FakeContainer):
    """What Podman reports there: the user's whole cgroup for every container, above each one's own limits."""

    def __init__(self, cl: FakeDocker, name: str, status: str = "running") -> None:
        super().__init__(cl, name, status)
        self.attrs["HostConfig"] |= {"Memory": 256 * MIB, "PidsLimit": 100}

    @override
    def stats(self, stream: bool = False) -> dict[str, Any]:
        st = super().stats()
        st["memory_stats"] = {"usage": 2035 * MIB, "limit": 16 << 30}
        st["pids_stats"] = {"current": 198}
        return st


class SharedCgroupDocker(FakeDocker):
    @override
    def _list(self, all: bool = True, filters: Any = None) -> list[FakeContainer]:
        return [SharedCgroupContainer(self, n) for n in self.names]

    @override
    def info(self) -> dict[str, Any]:
        return {"NCPU": 8, "MemTotal": 16 << 30, "CpuCfsQuota": False}


def use(hub: TestClient, fake: FakeDocker) -> None:  # noqa: F811
    hub.app.state.ops_engine.factory = lambda: fake  # type: ignore[attr-defined]


def test_usage_above_the_containers_own_limit_means_the_engine_does_not_measure_it() -> None:
    hc = {"Memory": 256 * MIB, "PidsLimit": 100}
    assert rt.unmeasured([{"state": "running", "mem_used": 2035 * MIB, "pids": 50, "host": hc}])
    assert rt.unmeasured([{"state": "running", "mem_used": 5 * MIB, "pids": 198, "host": hc}])
    assert not rt.unmeasured([{"state": "running", "mem_used": 5 * MIB, "pids": 4, "host": hc}])
    assert not rt.unmeasured([{"state": "running", "mem_used": 5 * MIB, "pids": 4, "host": {}}])  # no limit


def test_metrics_the_engine_cannot_measure_show_as_unavailable(hub: TestClient) -> None:  # noqa: F811
    use(hub, SharedCgroupDocker())
    j = hub.get("/api/ops/runtime").json()
    live = j["engine"]["live"]
    assert live and all(x["mem_used"] is None and x["cpu_used"] is None and x["pids"] is None for x in live)
    assert "indisponível" in j["engine"]["metrics_note"]
    page = hub.get("/admin/runtime").text
    assert "2035" not in page and "198 /" not in page
    assert "Uso por contêiner indisponível" in page
    assert "limite de CPU não é aplicado" in page  # CpuCfsQuota false: the cpu controller is not delegated


def test_the_real_socket_of_the_host_is_shown(hub: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    use(hub, FakeDocker())
    sock = "/data/app-hub-engine/run/podman/podman.sock"
    monkeypatch.setenv("HUB_CONTAINER_SOCKET_HOST", sock)
    page = hub.get("/admin/runtime").text
    assert f'value="{sock}"' in page and f"Conectado a {sock}" in page
    assert "/var/run/docker.sock" not in page


def test_local_images_are_not_labelled_as_docker_hub() -> None:
    assert rt.image_label("docker.io/library/app-hub-web:latest") == "app-hub-web:latest"
    assert rt.image_label("localhost/app-hub-app-ativos:3") == "app-hub-app-ativos:3"
    assert rt.image_label("ghcr.io/empresa/app:1") == "ghcr.io/empresa/app:1"


def test_allocations_above_the_host_say_overcommit_instead_of_negative_free(
    hub: TestClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use(hub, FakeDocker())
    monkeypatch.setenv("HUB_HOST_CPU", "1")
    page = hub.get("/admin/runtime").text
    assert "Livre -" not in page and "acima da capacidade" in page
