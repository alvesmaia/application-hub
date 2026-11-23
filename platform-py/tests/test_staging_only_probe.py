"""A new app deployed only to staging has no production container: probing its production upstream every minute
failed and the catalog marked it `Unstable` while its staging route and API answered."""

import json
from pathlib import Path

import pytest

from hub.app import create_app
from hub.ops import newapp

MANIFEST = {
    "id": "novo",
    "name": "Novo",
    "version": "1.0.0",
    "routes": ["/novo/"],
    "permissions": ["novo.read"],
}


def test_a_new_app_without_production_is_not_probed_until_it_reaches_production(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    app = create_app()
    ins = app.state.insight
    assert newapp.register(tmp_path, ins.registry, [], MANIFEST)
    ids = [a for a, _ in ins.targets()]
    assert "novo" not in ids and "inventory" in ids  # compose apps are always probed
    deps = [{"id": 1, "app": "novo", "env": "Produção", "status": "Implantado", "version": "1.0.0"}]
    (tmp_path / "ops.json").write_text(json.dumps({"deployments": deps}), encoding="utf-8")
    assert "novo" in [a for a, _ in ins.targets()]
