"""Remove an application: the pure pieces (registry, removed list, what each store loses, database verdict)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hub.gateway.registry import AppEntry, Registry
from hub.ops import newapp
from hub.ops import remove_db as rdb
from hub.ops import remove_state as rs


def reg(*ids: str) -> Registry:
    return Registry([AppEntry(id=a, name=f"App {a}", upstream="http://u") for a in ids])


# ---- registry and the removed list --------------------------------------------------------------------
def test_registry_remove_drops_the_app_and_its_staging() -> None:
    r = reg("inventory", "suppliers")
    r.staging["inventory"] = "http://stg"
    assert r.remove("inventory") is True and r.ids() == ["suppliers"] and "inventory" not in r.staging
    assert r.remove("inventory") is False


def test_unregister_rewrites_dynamic_and_keeps_the_app_out_on_the_next_boot(tmp_path: Path) -> None:
    r = reg("inventory")
    apps = r.ids()
    newapp.register(
        tmp_path, r, apps, {"id": "novoapp", "name": "Novo", "version": "1.0.0", "routes": ["/novoapp/"]}
    )
    newapp.unregister(tmp_path, r, "novoapp")
    newapp.unregister(tmp_path, r, "inventory")  # a sample app (read-only registry file)
    assert r.ids() == []
    assert json.loads((tmp_path / newapp.DYNAMIC).read_text(encoding="utf-8")) == []
    assert newapp.removed_ids(tmp_path) == ["inventory", "novoapp"]
    fresh = reg("inventory", "suppliers")  # boot: the read-only file still lists inventory
    newapp.load_dynamic(tmp_path, fresh)
    assert newapp.apply_removed(tmp_path, fresh) == ["inventory"] and fresh.ids() == ["suppliers"]


def test_a_new_deployment_of_a_removed_id_brings_it_back(tmp_path: Path) -> None:
    r = reg()
    newapp.unregister(tmp_path, r, "novoapp")
    assert newapp.register(
        tmp_path, r, [], {"id": "novoapp", "name": "Novo", "version": "1", "routes": ["/novoapp/"]}
    )
    assert newapp.removed_ids(tmp_path) == []


def test_removed_list_ignores_a_broken_file(tmp_path: Path) -> None:
    (tmp_path / newapp.REMOVED).write_text("{nope", encoding="utf-8")
    assert newapp.removed_ids(tmp_path) == []


# ---- confirmation ---------------------------------------------------------------------------------------
def test_confirm_accepts_the_name_or_the_id_case_insensitive_and_trimmed() -> None:
    for typed in ("Gestão de Ativos", "  gestão de ativos ", "inventory", "INVENTORY"):
        assert rs.confirms(typed, "inventory", "Gestão de Ativos"), typed
    for typed in ("", "invent", "Gestão", "suppliers"):
        assert not rs.confirms(typed, "inventory", "Gestão de Ativos"), typed


# ---- admin.json -----------------------------------------------------------------------------------------
def admin_doc() -> dict[str, Any]:
    both = [{"app": "inventory", "env": "producao"}, {"app": "suppliers", "env": "producao"}]
    return {
        "env": [
            {
                "name": "HUB_INTERNAL_TOKEN_INVENTORY",
                "managed": True,
                "bindings": [{"app": "inventory", "env": "producao"}],
            },
            {"name": "DATABASE_URL_INVENTORY_STAGING", "bindings": [{"app": "inventory", "env": "staging"}]},
            {"name": "HUB_STORAGE_URL", "bindings": both},
            {"name": "LOOSE", "bindings": []},  # bound to nothing before: not ours to delete
        ],
        "pending_restart": {"inventory": 1, "suppliers": 2},
        "roles": [{"name": "A", "apps": ["inventory", "suppliers"]}, {"name": "B", "apps": None}],
        "settings": {"catalog_hidden_apps": ["inventory"], "status_public_apps": ["inventory", "suppliers"]},
        "flags": [{"app": "inventory", "key": "f"}, {"app": "suppliers", "key": "g"}],
        "flags_version": 3,
        "slos": [{"app": "inventory", "env": "producao"}],
        "health": [{"name": "Gestão de Ativos", "app": "inventory"}, {"name": "Autenticação", "app": None}],
        "health_apps": ["inventory", "suppliers"],
        "incidents": [{"app": "inventory", "title": "kept as history"}],
        "audit": [{"id": 1, "scope": "inventory"}],
    }


def test_strip_admin_drops_the_apps_config_and_keeps_history() -> None:
    d = admin_doc()
    gone = rs.strip_admin(d, "inventory")
    assert gone == ["HUB_INTERNAL_TOKEN_INVENTORY", "DATABASE_URL_INVENTORY_STAGING"]
    assert [v["name"] for v in d["env"]] == ["HUB_STORAGE_URL", "LOOSE"]
    assert d["env"][0]["bindings"] == [{"app": "suppliers", "env": "producao"}]
    assert d["pending_restart"] == {"suppliers": 2}
    assert d["roles"] == [{"name": "A", "apps": ["suppliers"]}, {"name": "B", "apps": None}]
    assert d["settings"] == {"catalog_hidden_apps": [], "status_public_apps": ["suppliers"]}
    assert d["flags"] == [{"app": "suppliers", "key": "g"}] and d["flags_version"] == 4
    assert d["slos"] == [] and d["health"] == [{"name": "Autenticação", "app": None}]
    assert d["health_apps"] == ["suppliers"]
    assert d["incidents"] and d["audit"] == [{"id": 1, "scope": "inventory"}]


# ---- ops.json -------------------------------------------------------------------------------------------
def ops_doc() -> dict[str, Any]:
    return {
        "deployments": [{"app": "inventory", "package": "inventory-1.hubapp"}, {"app": "suppliers"}],
        "policies": [{"app": "inventory"}, {"app": "suppliers"}],
        "script_approvals": [{"app": "inventory"}],
        "backups": [
            {"app": "inventory", "file": "inventory-1.zip"},
            {"app": "inventory", "file": "inventory-2.zip"},
            {"app": "suppliers", "file": "suppliers-1.zip"},
        ],
        "limits": {"inventory": {}, "suppliers": {}},
        "schedules": {"inventory|producao": {}, "inventoryx|producao": {}},
        "maintenance": {"inventory|staging": {}},
        "stopped": ["inventory|producao", "suppliers|producao"],
        "secrets": {
            "backup.pg:app_inventory": "s",
            "backup.s3:app-inventory": "s",
            "backup.pg:app_suppliers": "s",
        },
    }


def test_strip_ops_drops_everything_but_the_pre_removal_backup() -> None:
    d = ops_doc()
    rs.strip_ops(d, "inventory", keep_data=False, keep_file="inventory-2.zip")
    assert d["deployments"] == [{"app": "suppliers"}] and d["policies"] == [{"app": "suppliers"}]
    assert d["script_approvals"] == [] and d["limits"] == {"suppliers": {}}
    assert d["schedules"] == {"inventoryx|producao": {}} and d["maintenance"] == {}
    assert d["stopped"] == ["suppliers|producao"]
    assert [b["file"] for b in d["backups"]] == ["inventory-2.zip", "suppliers-1.zip"]
    assert d["secrets"] == {"backup.pg:app_suppliers": "s"}


def test_strip_ops_keeping_data_keeps_backups_and_their_credentials() -> None:
    d = ops_doc()
    rs.strip_ops(d, "inventory", keep_data=True, keep_file="")
    assert len(d["backups"]) == 3 and "backup.pg:app_inventory" in d["secrets"]
    assert d["deployments"] == [{"app": "suppliers"}]


# ---- auth.json, connections, preferences ----------------------------------------------------------------
def test_tokens_lose_the_app_and_one_left_with_no_app_is_revoked() -> None:
    d = {"tokens": [{"id": "t1", "apps": ["inventory"], "revoked_at": None},
                    {"id": "t2", "apps": ["inventory", "suppliers"], "revoked_at": None},
                    {"id": "t3", "apps": [], "revoked_at": None}]}  # fmt: skip
    assert rs.strip_tokens(d, "inventory", 99) == ["t1"]
    assert d["tokens"][0]["revoked_at"] == 99 and d["tokens"][0]["apps"] == []
    assert d["tokens"][1]["apps"] == ["suppliers"] and d["tokens"][2]["revoked_at"] is None  # [] = all apps


def test_connections_are_unbound_from_the_app() -> None:
    items = [{"name": "docs", "bindings": [{"app": "inventory", "env": "producao"}, {"app": "suppliers", "env": "staging"}],
              "anonymous_link_apps": ["inventory", "suppliers"]}]  # fmt: skip
    assert rs.strip_connections(items, "inventory") == ["docs"]
    assert items[0]["bindings"] == [{"app": "suppliers", "env": "staging"}]
    assert items[0]["anonymous_link_apps"] == ["suppliers"]
    assert rs.strip_connections(items, "inventory") == []


def test_under_app_matches_its_pages_only() -> None:
    assert rs.under_app("/apps/inventory", "inventory") and rs.under_app(
        "/apps/inventory/items?q=1", "inventory"
    )
    assert not rs.under_app("/apps/inventoryx", "inventory") and not rs.under_app(
        "/admin/runtime", "inventory"
    )


# ---- database verdict -------------------------------------------------------------------------------------
def test_database_is_dropped_only_with_the_hub_marker() -> None:
    ok = {"hub-managed:app=inventory"}
    assert rdb.verdict("app_inventory", None, None, "", ok) == "absent"
    assert (
        rdb.verdict(
            "app_inventory", "hub-managed:app=inventory", "app_inventory", "hub-managed:app=inventory", ok
        )
        == "drop"
    )
    assert (
        rdb.verdict("app_inventory", "hub-managed:app=inventory", None, "", ok) == "drop"
    )  # role left alone
    assert rdb.verdict("app_inventory", "outra", "app_inventory", "", ok) == "refuse"
    assert rdb.verdict("app_inventory", "hub-managed:app=inventory", "postgres", "", ok) == "refuse"
    assert rdb.verdict("app_inventory", None, "app_inventory", "", ok) == "refuse"  # database without marker
    assert rdb.verdict("app_inventory", None, "app_inventory", "hub-managed:app=inventory", ok) == "drop"


def test_markers_of_a_sample_app_include_the_demo_one() -> None:
    assert rdb.markers("inventory", "producao") == {"hub-managed:app=inventory", "hub-managed:demo=inventory"}
    assert rdb.markers("inventory", "staging") == {"hub-managed:app=inventory:env=staging"}


def test_drop_sql_forces_the_database_and_drops_the_hub_roles() -> None:
    sql = "\n".join(rdb.drop_sql("app_inventory"))
    assert 'DROP DATABASE IF EXISTS "app_inventory" WITH (FORCE)' in sql
    assert (
        'DROP ROLE IF EXISTS "app_inventory"' in sql and 'DROP ROLE IF EXISTS "hub_read_app_inventory"' in sql
    )
    assert 'DROP ROLE IF EXISTS "hub_bkp_app_inventory"' in sql
    assert "ro\\_%" in sql and "'app_inventory'" in sql  # read accounts forget the database
