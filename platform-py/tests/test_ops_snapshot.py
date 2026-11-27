"""`ops.json` on the request path: every app request asked for the approved scripts and parsed the whole file
(≈16 KB per deployment, logs included). `OpsStore.snapshot()` parses it once per change (mtime + size)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hub.ops.model import OpsStore


def test_snapshot_is_parsed_once_per_change(tmp_path: Path) -> None:
    store = OpsStore(tmp_path, ["a"])
    store.write({**store.read(), "next_deploy_id": 7})
    first = store.snapshot()
    assert first["next_deploy_id"] == 7 and store.snapshot() is first
    store.write({**store.read(), "next_deploy_id": 8})  # same size: the write itself drops the cache
    again = store.snapshot()
    assert again is not first and again["next_deploy_id"] == 8


def test_snapshot_without_a_file_has_the_defaults(tmp_path: Path) -> None:
    snap = OpsStore(tmp_path, ["a"]).snapshot()
    assert snap["deployments"] == [] and [p["app"] for p in snap["policies"]] == ["a"]


def test_push_keeps_the_last_deployments_per_app_and_env() -> None:
    from hub.ops import model as m

    d: dict[str, Any] = {"deployments": [], "next_deploy_id": 1}
    m.push(d, {"app": "a", "env": m.PRODUCAO, "status": m.IMPLANTADO, "image": "img:1"})  # the active one
    m.push(d, {"app": "a", "env": m.PRODUCAO, "status": m.SUBSTITUIDO, "image": "img:2"})  # rollback target
    for _ in range(m.KEEP_DEPLOYMENTS + 5):
        m.push(d, {"app": "a", "env": m.PRODUCAO, "status": m.FALHOU})
        m.push(d, {"app": "b", "env": m.STAGING, "status": m.FALHOU})
    a = [x for x in d["deployments"] if x["app"] == "a"]
    assert len([x for x in a if x["status"] == m.FALHOU]) == m.KEEP_DEPLOYMENTS
    assert {m.IMPLANTADO, m.SUBSTITUIDO} <= {x["status"] for x in a}
    assert len([x for x in d["deployments"] if x["app"] == "b"]) == m.KEEP_DEPLOYMENTS
    assert d["deployments"][0]["id"] == d["next_deploy_id"] - 1  # newest first, ids keep growing
