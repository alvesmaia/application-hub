import json
from pathlib import Path

from hub.config import load_settings
from hub.models import AdminDocument, KeyringFile


def test_toml_and_environment_settings(tmp_path, monkeypatch):
    path = tmp_path / "hub.toml"
    path.write_text('brand = "Toml Hub"\n[feature]\nenabled = true\n', encoding="utf-8")
    monkeypatch.setenv("HUB_CONFIG_FILE", str(path))
    monkeypatch.delenv("HUB_BRAND", raising=False)
    settings = load_settings()
    assert settings.brand == "Toml Hub"
    assert settings.config["feature"]["enabled"] is True
    monkeypatch.setenv("HUB_BRAND", "Env Hub")
    assert load_settings().brand == "Env Hub"


def test_models_validate_without_changing_rust_json_shapes():
    admin = {"settings": {}, "env": [], "roles": [], "health": [], "audit": [], "next_audit_id": 0}
    assert AdminDocument.model_validate(admin).model_dump(exclude_none=True) == admin
    keyring = json.loads((Path(__file__).parent / "vectors/vault-v2.json").read_text())["keyring"]
    assert KeyringFile.model_validate(keyring).version == 2


def test_manifest_requires_section():
    from hub.models import AppManifest, requires_error
    from hub.ops import pipeline as pl

    ok = {"database": True, "storage": True, "variables": ["API_URL"], "secrets": ["API_KEY"]}
    assert requires_error(None) is None and requires_error(ok) is None
    req = AppManifest.model_validate({"id": "x", "requires": ok}).requires
    assert req is not None and req.database
    assert "nome inválido" in (requires_error({"variables": ["hub_x"]}) or "")
    assert "HUB_" in (requires_error({"secrets": ["HUB_TOKEN"]}) or "")
    assert "repetidos" in (requires_error({"variables": ["A", "A"]}) or "")
    assert "variável e segredo" in (requires_error({"variables": ["A"], "secrets": ["A"]}) or "")
    assert "requires.queue" in (requires_error({"queue": True}) or "")
    base = {"id": "estoque", "name": "Estoque", "routes": ["/estoque/*"]}
    assert pl._manifest(json.dumps({**base, "requires": ok}), [])[2]
    m, detail, good = pl._manifest(json.dumps({**base, "requires": {"database": "talvez"}}), [])
    assert not good and m is None and detail.startswith("requires.database")
