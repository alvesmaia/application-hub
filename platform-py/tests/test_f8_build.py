"""F8: package with a build context -> image built on the hub (simulated Docker) -> blue/green -> rollback."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_f5b import FD, IMG_NEW, JSON, READY, fake, hub  # noqa: F401

from hub.ops import model as m
from hub.ops import pipeline as pl
from hub.ops import runtime as rt
from hub.ops.model import OpsError


def bpack(version: str = "9.1.0", dockerfile: str = "Dockerfile", extra: dict[str, bytes] | None = None,
          with_df: bool = True) -> bytes:  # fmt: skip
    man = {
        "id": "ativos",
        "name": "Ativos",
        "version": version,
        "routes": ["/ativos/"],
        "permissions": ["ativos.read"],
    }
    api = {"openapi": "3.0.0", "info": {"title": "t", "version": "1"},
           "paths": {"/ativos/api/x": {"get": {"responses": {"200": {"description": "ok"}}}}}}  # fmt: skip
    body = {"manifest.json": json.dumps(man).encode(), "openapi.json": json.dumps(api).encode(),
            "runtime.json": json.dumps({"build": {"dockerfile": dockerfile}}).encode(), "ui/index.html": b"<h1>x</h1>",
            "build/app.js": b"console.log(1)", **(extra or {})}  # fmt: skip
    if with_df:
        body["build/Dockerfile"] = b"FROM scratch\n"
    sums = "".join(f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in body.items())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("checksums.txt", sums)
        for n, b in body.items():
            z.writestr(n, b)
    return buf.getvalue()


def up(c: TestClient, data: bytes) -> Any:
    return c.post("/api/ops/packages?env=producao&wait=true", files={"package": ("p.hubapp", data)},
                  headers={"x-hub-request": "1"}).json()  # fmt: skip


class Builder:
    """`api.build` and `images.get(...).tag` simulated; the built image now exists on the daemon."""

    def __init__(self, fd: FD, fail: bool = False) -> None:
        self.fd, self.fail, self.calls, self.tags = fd, fail, [], []
        self.have: set[str] = set()
        fd.api.build = self.build
        old = fd.images.get

        def get(ref: str) -> Any:
            if ref in self.have:
                return SimpleNamespace(id="sha256:" + "cc" * 32, attrs={"Config": {"Env": []}},
                                       tag=lambda r, t: self.tags.append(f"{r}:{t}"))  # fmt: skip
            im = old(ref)
            im.tag = lambda r, t: self.tags.append(f"{r}:{t}")
            return im

        fd.images.get = get

    def build(self, **kw: Any) -> Any:
        self.calls.append(kw)
        yield {"stream": "Step 1/2 : FROM scratch\n"}
        if self.fail:
            yield {"error": "pip: no matching distribution"}
            return
        yield {"stream": "Successfully built\n"}
        self.have.add(kw["tag"])
        yield {"aux": {"ID": "sha256:" + "cc" * 32}}


def test_pipeline_accepts_build_package_and_rejects_bad_ones() -> None:
    r = asyncio.run(pl.run_all(bpack(), "producao", []))
    assert r["passed"] and r["build"] and r["image"] == "app-hub-app-ativos:9.1.0"
    assert "construída no hub" in r["steps"][3]["detail"]
    m_ = {"id": "ativos", "version": "9.1.0"}
    files = {"build/Dockerfile": b"x"}
    assert pl._build_spec(m_, {}, files) == ("", "app-hub-app-ativos:9.1.0")
    assert "ausente" in pl._build_spec(m_, {"dockerfile": "X"}, files)[0]
    assert "inválido" in pl._build_spec(m_, {"dockerfile": "../x"}, files)[0]
    assert "tag" in pl._build_spec({"id": "a", "version": "1 0"}, {}, files)[0]
    assert pl.is_build("app-hub-app-a:1") and not pl.is_build("x@sha256:" + "a" * 64) and not pl.is_build("")


def test_build_deploy_pins_previous_and_rollback(hub: TestClient, fake: FD) -> None:  # noqa: F811
    b = Builder(fake)
    j = up(hub, bpack())
    rep = j["result"]["report"]
    assert rep["passed"] and rep["build"] and rep["image"] == "app-hub-app-ativos:9.1.0", rep
    dep = j["result"]["deployment"]
    assert dep["status"] == m.AGUARDANDO and dep["build"]
    r = hub.post(f"/api/ops/deployments/{dep['id']}/approve?wait=true", headers=JSON)
    assert r.status_code == 200 and r.json()["status"] == m.IMPLANTADO, r.text
    assert len(b.calls) == 1
    c = b.calls[0]
    assert c["tag"] == "app-hub-app-ativos:9.1.0" and c["dockerfile"] == "Dockerfile"
    assert c["container_limits"]["memory"] == c["container_limits"]["memswap"] == 1 << 30
    assert c["rm"] and c["forcerm"]
    # new image in the container; the previous one (the compose baseline) was pinned with the name app-hub-app-*
    assert fake.cs["app-hub-app-ativos"].attrs["Image"] == "sha256:" + "cc" * 32
    assert any(t.startswith("app-hub-app-ativos:baseline-") for t in b.tags)
    assert any(t.startswith("app-hub-app-ativos:keep-") for t in b.tags)
    full = next(d for d in hub.get("/api/ops/deployments").json() if d["version"] == "9.1.0")
    assert "Successfully built" in full["build_log"]
    # rollback: baseline image (still on the daemon, because pinned) without rebuilding
    cur = next(d for d in hub.get("/api/ops/deployments").json() if d["status"] == m.IMPLANTADO)
    fake.images.get = lambda ref: SimpleNamespace(id="sha256:" + "aa" * 32, attrs={"Config": {"Env": []}})  # type: ignore[method-assign]
    rb = hub.post(f"/api/ops/deployments/{cur['id']}/rollback?wait=true", headers=JSON)
    assert rb.status_code == 200 and rb.json()["status"] == m.IMPLANTADO, rb.text
    assert fake.cs["app-hub-app-ativos"].attrs["Image"] == "sha256:" + "aa" * 32
    assert len(b.calls) == 1


def test_build_failure_marks_deployment_and_keeps_active(hub: TestClient, fake: FD) -> None:  # noqa: F811
    Builder(fake, fail=True)
    dep = up(hub, bpack())["result"]["deployment"]
    r = hub.post(f"/api/ops/deployments/{dep['id']}/approve?wait=true", headers=JSON)
    assert r.status_code == 422 and r.json()["status"] == m.FALHOU and "Build falhou" in r.json()["note"]
    assert "app-hub-app-ativos__new" not in fake.cs
    assert fake.cs["app-hub-app-ativos"].attrs["Image"] == "sha256:" + "aa" * 32


def test_build_package_validation(hub: TestClient, fake: FD) -> None:  # noqa: F811
    for data, msg in ((bpack(with_df=False), "ausente"), (bpack(version="1 0"), "tag"),
                      (bpack(extra={"build/k.pem": b"-----BEGIN RSA PRIVATE KEY-----"}), "segredo")):  # fmt: skip
        rep = up(hub, data)["result"]["report"]
        assert not rep["passed"] and msg in rep["reason"], rep["reason"]


def test_engine_build_guard_and_pin_swallow(fake: FD) -> None:  # noqa: F811
    with pytest.raises(OpsError):
        rt.Guarded(fake).build_stream(".", "hub-evil:1", "Dockerfile", 1)
    with pytest.raises(OpsError):
        rt.Guarded(fake).pin("sha256:x", "hub-evil", "t")
    assert READY is not None


@pytest.mark.parametrize("ctl", [["cpuset", "cpu", "memory", "pids"], ["memory", "pids"], None])
def test_podman_rootless_tmpfs_and_cpu(ctl: list[str] | None) -> None:
    """Podman refuses `uid=` on tmpfs; without the `cpu` controller delegated (RHEL 9 rootless default) the CPU limit breaks
    the build and the creation. Docker (`/libpod/info` 404) keeps working the same."""
    seen: dict[str, Any] = {}
    info = SimpleNamespace(status_code=200 if ctl else 404, json=lambda: {"host": {"cgroupControllers": ctl}})
    api = SimpleNamespace(
        base_url=f"http+docker://t{ctl}", _get=lambda url: info,
        create_container=lambda image, name, **kw: seen.update(create=kw),
        build=lambda **kw: seen.update(build=kw["container_limits"]) or iter(()),
    )  # fmt: skip
    g = rt.Guarded(SimpleNamespace(api=api, containers=SimpleNamespace(get=lambda n: n)))
    host = {"NanoCpus": 10**9, "Memory": 1, "Tmpfs": {"/scratch": "rw,size=256m,uid=10001,gid=10001"}}
    g.create("img", "app-hub-app-x", host_config=host)
    g.build_stream(".", "app-hub-app-x:1", "Dockerfile", 1, cpus=2)
    made = seen["create"]["host_config"]
    podman, cpu = ctl is not None, ctl is None or "cpu" in ctl
    assert made["Tmpfs"]["/scratch"] == ("rw,size=256m,U" if podman else "rw,size=256m,uid=10001,gid=10001")
    assert ("NanoCpus" in made) is cpu and ("cpushares" in seen["build"]) is cpu and made["Memory"] == 1
    assert host["Tmpfs"]["/scratch"].endswith("gid=10001")  # the caller's template does not change
