"""A fresh install has no "restart pending": the boot syncs (initial variables, rotated infrastructure passwords,
internal tokens, broker) only mirror what compose already gave the containers. An admin's change still counts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from admin_helper import make_hub

ENV_FILE = [
    {
        "name": "BOOT_ONLY",
        "value": "1",
        "kind": "publica",
        "bindings": [{"app": "inventory", "env": "producao"}],
    }
]


def test_boot_syncs_do_not_mark_a_restart_but_an_admin_change_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boot = tmp_path / "app-env.json"
    boot.write_text(json.dumps(ENV_FILE), encoding="utf-8")
    hub = make_hub(tmp_path / "data", monkeypatch)
    monkeypatch.setenv("HUB_APP_ENV_FILE", str(boot))
    monkeypatch.setenv("HUB_INTERNAL_TOKEN_INVENTORY", "t" * 32)
    with hub:  # runs the lifespan (boot syncs)
        assert hub.get("/api/admin/env").status_code == 200
        assert (
            json.loads((tmp_path / "data" / "admin.json").read_text("utf-8")).get("pending_restart", {}) == {}
        )
        body = {
            "name": "MINHA_VAR",
            "value": "x",
            "kind": "publica",
            "bindings": [{"app": "inventory", "env": "producao"}],
        }
        assert hub.post("/api/admin/env", json=body).status_code == 201
        assert (
            "inventory"
            in json.loads((tmp_path / "data" / "admin.json").read_text("utf-8"))["pending_restart"]
        )


def test_without_the_sample_apps_no_example_variable_is_left_unbound() -> None:
    from hub.admin.config import seed

    d: dict[str, Any] = {}
    seed(d, ["my-app"])
    assert d.get("env", []) == []
    d = {}
    seed(d, ["suppliers"])
    assert {v["name"] for v in d["env"]} == {
        "API_BASE_URL",
        "AUTH_CLIENT_ID",
        "LOG_LEVEL",
        "FEATURE_FLAG_NEW_DASHBOARD",
    }
    assert all(v["bindings"] for v in d["env"])
