"""F22: new app registration, container template and `requires` in the deployment."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hub.gateway.registry import Registry
from hub.ops import newapp
from hub.ops.model import OpsError

MF = {"id": "estoque", "name": "Estoque", "version": "0.1.0", "routes": ["/estoque/*"],
      "requires": {"database": True, "storage": True, "variables": ["API_URL"], "secrets": ["API_KEY"]}}  # fmt: skip


def test_register_persist_and_reload(tmp_path: Path) -> None:
    reg, apps = Registry([]), ["ativos"]
    assert newapp.register(tmp_path, reg, apps, MF)
    assert not newapp.register(tmp_path, reg, apps, MF)
    e = reg.get("estoque")
    assert e is not None and e.upstream == "http://app-hub-app-estoque:8080" and apps == ["ativos", "estoque"]
    assert newapp.is_dynamic(tmp_path, "estoque") and not newapp.is_dynamic(tmp_path, "ativos")
    fresh = Registry([])
    assert newapp.load_dynamic(tmp_path, fresh) == ["estoque"] and newapp.load_dynamic(tmp_path, fresh) == []
    assert newapp.load_dynamic(tmp_path / "vazio", fresh) == []
    host = newapp.base_container()["HostConfig"]
    assert host["ReadonlyRootfs"] and host["CapDrop"] == ["ALL"]


def test_parse_config() -> None:
    assert newapp.parse_config("") == {"variables": {}, "secrets": {}}
    assert newapp.parse_config('{"secrets": {"K": "v"}}')["secrets"] == {"K": "v"}
    for bad in ("{", '{"variables": [1]}', "[]"):
        with pytest.raises(OpsError):
            newapp.parse_config(bad)


class Cfg:
    def __init__(self) -> None:
        self.vars: list[dict[str, Any]] = []

    async def upsert_managed(self, actor: str, v: dict[str, Any], title: str) -> bool:
        self.vars.append(v)
        return True


async def test_apply_requires() -> None:
    cfg, calls = Cfg(), []

    async def db(a: str, e: str) -> dict[str, Any]:
        calls.append(("db", a, e))
        return {"database": "app_estoque_staging"}

    async def st(a: str, e: str) -> dict[str, Any]:
        return {"storage": {"bucket": "estoque-staging"}}

    conf = {"variables": {"API_URL": "http://x"}, "secrets": {"API_KEY": "s3cr3t"}}
    out = await newapp.apply_requires("estoque", "staging", MF, conf, "dev", cfg, db, st)
    assert out == {"database": "app_estoque_staging", "storage": "estoque-staging",
                   "variables": ["API_URL"], "secrets": ["API_KEY"]}  # fmt: skip
    assert "s3cr3t" not in str(out)
    sec = next(v for v in cfg.vars if v["inject_as"] == "API_KEY")
    assert sec["name"] == "API_KEY_ESTOQUE_STAGING" and sec["kind"] == "secreta"
    assert sec["bindings"] == [{"app": "estoque", "env": "staging"}]
    with pytest.raises(OpsError, match="armazenamento"):
        await newapp.apply_requires("estoque", "producao", MF, conf, "dev", cfg, db, None)
    with pytest.raises(OpsError, match="não declarado"):
        await newapp.apply_requires("estoque", "producao", {"id": "x"}, conf, "dev", cfg, db, st)
