"""0.4.0 migration of the hub's /data: sample app ids in English. Only id keys and id fields change - free text and
the audit chain stay; idempotent; reversible."""

from __future__ import annotations

import json
from pathlib import Path

from hub.admin import rename_apps as ra

REN = ra.pairs()


def test_token_substitution_respects_word_edges() -> None:
    assert ra.sub("/apps/ativos/itens", REN) == "/apps/inventory/itens"
    assert ra.sub("HUB_INTERNAL_TOKEN_COLETOR", REN) == "HUB_INTERNAL_TOKEN_COLLECTOR"
    assert (
        ra.sub("postgresql://ativos:pw@db:5432/ativos", REN) == "postgresql://inventory:pw@db:5432/inventory"
    )
    assert (
        ra.sub("ativos|producao", REN) == "inventory|producao"
        and ra.sub("servicos__staging", REN) == "servicedesk__staging"
    )
    assert ra.sub("inativos", REN) == "inativos" and ra.sub("ativos2", REN) == "ativos2"


def test_admin_store_renames_ids_but_not_free_text() -> None:
    doc = {
        "env": [{"name": "HUB_INTERNAL_TOKEN_ATIVOS", "kind": "secreta", "value": "ativos-token-1",
                 "bindings": [{"app": "ativos", "env": "producao"}]},
                {"name": "DATABASE_URL_SERVICOS", "kind": "connection_string",
                 "value": "postgresql://servicos:x@h:5432/servicos", "bindings": [{"app": "servicos", "env": "producao"}]}],
        "roles": [{"name": "User", "apps": ["coletor", "outro"], "description": "acesso aos ativos"}],
        "settings": {"catalog_hidden_apps": ["automacoes"]},
        "flags": [{"app": "fornecedores", "key": "x", "description": "fornecedores novos"}],
    }  # fmt: skip
    out = ra.admin(doc, REN)
    assert (
        out["env"][0]["name"] == "HUB_INTERNAL_TOKEN_INVENTORY" and out["env"][0]["value"] == "ativos-token-1"
    )
    assert out["env"][0]["bindings"] == [{"app": "inventory", "env": "producao"}]
    assert out["env"][1]["value"] == "postgresql://servicedesk:x@h:5432/servicedesk"
    assert (
        out["roles"][0]["apps"] == ["collector", "outro"]
        and out["roles"][0]["description"] == "acesso aos ativos"
    )
    assert out["settings"]["catalog_hidden_apps"] == ["automations"]
    assert out["flags"][0] == {"app": "suppliers", "key": "x", "description": "fornecedores novos"}


def test_ops_store_renames_maps_and_records() -> None:
    doc = {"deployments": [{"app": "ativos", "version": "1"}], "backups": [{"app": "ativos", "file": "ativos-1.zip"}],
           "limits": {"coletor": {"cpu": 0.5}}, "schedules": {"servicos|producao": {}}, "secrets": {"backup.pg:ativos": "x"}}  # fmt: skip
    out = ra.ops(doc, REN)
    assert out["deployments"][0]["app"] == "inventory" and out["backups"][0]["file"] == "inventory-1.zip"
    assert set(out["limits"]) == {"collector"} and set(out["schedules"]) == {"servicedesk|producao"}
    assert set(out["secrets"]) == {"backup.pg:inventory"}


def test_migrate_files_and_folders_is_idempotent_and_reversible(tmp_path: Path) -> None:
    (tmp_path / "admin.json").write_text(
        json.dumps({"env": [], "roles": [{"name": "R", "apps": ["ativos"]}]})
    )
    (tmp_path / "analytics.json").write_text(
        json.dumps({"app_opens": {"ativos": 3}, "sections": {"coletor/folha": 1}})
    )
    (tmp_path / "backups" / "ativos").mkdir(parents=True)
    (tmp_path / "backups" / "ativos" / "ativos-1.zip").write_bytes(b"z")
    (tmp_path / "outbox").mkdir()
    (tmp_path / "outbox" / "servicos.json").write_text("{}")
    changed = ra.migrate(tmp_path)
    assert "admin.json" in changed and "analytics.json" in changed
    assert (tmp_path / "backups" / "inventory" / "inventory-1.zip").is_file() and (
        tmp_path / "outbox" / "servicedesk.json"
    ).is_file()
    assert json.loads((tmp_path / "analytics.json").read_text()) == {
        "app_opens": {"inventory": 3},
        "sections": {"collector/folha": 1},
    }
    assert ra.migrate(tmp_path) == []  # idempotent
    ra.migrate(tmp_path, reverse=True)
    assert json.loads((tmp_path / "admin.json").read_text())["roles"][0]["apps"] == ["ativos"]
    assert (tmp_path / "backups" / "ativos" / "ativos-1.zip").is_file()


def test_cli_rename_apps(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    from hub.admin.__main__ import main

    (tmp_path / "ux.json").write_text(
        json.dumps({"users": [{"favorites": ["coletor"], "recents": [{"path": "/apps/coletor"}]}]})
    )
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    assert main(["rename-apps"]) == 0 and "ux.json" in capsys.readouterr().out
    assert json.loads((tmp_path / "ux.json").read_text())["users"][0] == {
        "favorites": ["collector"],
        "recents": [{"path": "/apps/collector"}],
    }
    assert main(["rename-apps"]) == 0 and "nada a mudar" in capsys.readouterr().out


def test_sealed_values_are_resealed_under_the_new_names(tmp_path: Path) -> None:
    """Variable values are sealed with the variable name as AAD (`admin.env:<name>`) and backup secrets with their key
    (`ops.secret:<key>`): renaming without resealing left the hub unable to open them (boot loop)."""
    from hub.vault import Vault

    vault = Vault.open(tmp_path)
    admin = {"env": [{"name": "HUB_INTERNAL_TOKEN_ATIVOS", "kind": "secreta", "bindings": [{"app": "ativos", "env": "producao"}],
                      "value": vault.seal("tok-1", "admin.env:HUB_INTERNAL_TOKEN_ATIVOS")},
                     {"name": "DATABASE_URL_SERVICOS", "kind": "connection_string", "bindings": [],
                      "value": vault.seal("postgresql://servicos:x@h/servicos", "admin.env:DATABASE_URL_SERVICOS")}]}  # fmt: skip
    ops = {"secrets": {"backup.pg:ativos": vault.seal("pw", "ops.secret:backup.pg:ativos")}}
    (tmp_path / "admin.json").write_text(json.dumps(admin))
    (tmp_path / "ops.json").write_text(json.dumps(ops))
    ra.migrate(tmp_path, vault=vault)
    env = {v["name"]: v["value"] for v in json.loads((tmp_path / "admin.json").read_text())["env"]}
    assert (
        vault.open_value(env["HUB_INTERNAL_TOKEN_INVENTORY"], "admin.env:HUB_INTERNAL_TOKEN_INVENTORY")
        == "tok-1"
    )
    url = vault.open_value(env["DATABASE_URL_SERVICEDESK"], "admin.env:DATABASE_URL_SERVICEDESK")
    assert url == "postgresql://servicedesk:x@h/servicedesk"
    sec = json.loads((tmp_path / "ops.json").read_text())["secrets"]
    assert vault.open_value(sec["backup.pg:inventory"], "ops.secret:backup.pg:inventory") == "pw"
    ra.migrate(tmp_path, reverse=True, vault=vault)  # and back
    env = {v["name"]: v["value"] for v in json.loads((tmp_path / "admin.json").read_text())["env"]}
    assert (
        vault.open_value(env["HUB_INTERNAL_TOKEN_ATIVOS"], "admin.env:HUB_INTERNAL_TOKEN_ATIVOS") == "tok-1"
    )


def test_sealed_values_without_the_vault_stop_before_writing(tmp_path: Path) -> None:
    import pytest

    from hub.vault import Vault

    vault = Vault.open(tmp_path)
    doc = {
        "env": [
            {
                "name": "X_ATIVOS",
                "kind": "secreta",
                "bindings": [],
                "value": vault.seal("v", "admin.env:X_ATIVOS"),
            }
        ]
    }
    (tmp_path / "admin.json").write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="chave mestra"):
        ra.migrate(tmp_path)
    assert json.loads((tmp_path / "admin.json").read_text()) == doc  # untouched


def test_paths_follow_the_renamed_routes_too() -> None:
    """Recents and Inbox links point at pages whose first segment changed (collector `folha` → `sheet`…)."""
    assert ra.path("/apps/coletor/folha", REN) == "/apps/collector/sheet"
    assert ra.path("/apps/automacoes/execucoes/7", REN) == "/apps/automations/runs/7"
    assert ra.path("/apps/ativos/itens/3?x=1", REN) == "/apps/inventory/items/3?x=1"
    assert ra.path("/apps/fornecedores/suppliers", REN) == "/apps/suppliers/suppliers"
    back = ra.pairs(reverse=True)
    assert ra.path("/apps/collector/sheet", back) == "/apps/coletor/folha"
    doc = ra.ux({"users": [{"favorites": [], "recents": [{"path": "/apps/coletor/escanear"}]}]}, REN)
    assert doc["users"][0]["recents"][0]["path"] == "/apps/collector/scan"
