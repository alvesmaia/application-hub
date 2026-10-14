"""Rows of the Connections page (pure: connection records in, display rows out)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from hub.connections.sql import KIND_LABEL, SQL_KINDS
from hub.connections.store import target, view
from hub.ux.texts import ago


def connection_row(
    c: dict[str, Any], by_id: dict[str, dict[str, Any]], tr_: Callable[[str], str], now: int, lang: str
) -> dict[str, Any]:
    label, cls = _test_status(c["last_test"], tr_, now, lang)
    return {
        "name": c["name"],
        "description": c["description"],
        "kind": KIND_LABEL.get(c["kind"]) or ("SharePoint" if c["kind"] == "sharepoint" else "S3 / MinIO"),
        "target": target(c),
        "credential": _credential(c, tr_),
        "chips": [
            f"{by_id.get(b['app'], {}).get('short', b['app'])} · "
            f"{tr_('conn.env_staging') if b['env'] == 'staging' else tr_('conn.env_prod')}"
            for b in c["bindings"]
        ],
        "test_label": label,
        "test_class": cls,
        "item": json.dumps(view(c), ensure_ascii=False, separators=(",", ":")),
    }


def _credential(c: dict[str, Any], tr_: Callable[[str], str]) -> str:
    if c["kind"] in SQL_KINDS:
        ro = tr_("conn.sql_readonly") if c["readonly"] else tr_("conn.sql_readwrite")
        return f"{c['username']} · •••••••• · {ro}"
    if c["kind"] == "sharepoint":
        return f"{tr_('conn.secret')} ••••••••"
    return f"{c['access_key']} · ••••••••"


def _test_status(
    lt: dict[str, Any] | None, tr_: Callable[[str], str], now: int, lang: str
) -> tuple[str, str]:
    if lt is None:
        return tr_("conn.never_tested"), "status-pill status-draft"
    if lt["ok"]:
        return f"{tr_('conn.test_ok')} · {ago(lt['at'], now, lang)}", "status-pill status-active"
    return f"{tr_('conn.test_failed')} · {ago(lt['at'], now, lang)}", "status-pill status-maintenance"


def storage_rows(
    storage: list[dict[str, Any]], by_id: dict[str, dict[str, Any]], tr_: Callable[[str], str]
) -> list[tuple[str, str, str, str, str]]:
    return [
        (
            by_id.get(p["app"], {}).get("name", p["app"]),
            tr_("conn.env_staging") if p["env"] == "staging" else tr_("conn.env_prod"),
            p["bucket"],
            p["user"],
            f"{p['app']}|{p['env']}",
        )
        for p in storage
    ]
