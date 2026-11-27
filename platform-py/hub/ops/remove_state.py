"""Remove an application: what each store loses (pure functions over the loaded documents).

The audit trail, incidents, metrics, analytics and usage stay as history; everything that configures the app goes.
A removed id left in a list would make the next save of that list fail ("Aplicação desconhecida")."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from hub.connections.provision import names as bucket_name
from hub.connections.store import ConnError
from hub.ops.provision import db_name
from hub.ux.store import under_app as under_app

Doc = dict[str, Any]


def confirms(typed: str, app: str, name: str) -> bool:
    """The typed confirmation is the app's display name or id (case-insensitive, trimmed)."""
    t = " ".join(typed.split()).casefold()
    return bool(t) and t in (app.casefold(), " ".join(name.split()).casefold())


def _mine(key: str, app: str) -> bool:
    """Keys of the maps: `app` or `app|env…`."""
    return key == app or key.startswith(app + "|")


def _strip_env(d: Doc, app: str) -> list[str]:
    """Drops the app's bindings; a variable left bound to nothing is deleted (names returned)."""
    gone: list[str] = []
    for v in list(d.get("env", [])):
        kept = [b for b in v.get("bindings", []) if b.get("app") != app]
        if len(kept) == len(v.get("bindings", [])):
            continue
        v["bindings"] = kept
        if not kept:
            d["env"].remove(v)
            gone.append(str(v["name"]))
    return gone


def _strip_lists(d: Doc, app: str) -> None:
    for r in d.get("roles", []):
        if isinstance(r.get("apps"), list):
            r["apps"] = [a for a in r["apps"] if a != app]
    settings = d.get("settings") or {}
    for k in ("catalog_hidden_apps", "status_public_apps"):
        if isinstance(settings.get(k), list):
            settings[k] = [a for a in settings[k] if a != app]
    if "health_apps" in d:
        d["health_apps"] = [a for a in d["health_apps"] if a != app]


def strip_admin(d: Doc, app: str) -> list[str]:
    """admin.json: variables, pending restart, role/setting lists, flags, SLOs and monitored service."""
    gone = _strip_env(d, app)
    d.get("pending_restart", {}).pop(app, None)
    _strip_lists(d, app)
    flags = d.get("flags", [])
    if any(f.get("app") == app for f in flags):
        d["flags"] = [f for f in flags if f.get("app") != app]
        d["flags_version"] = d.get("flags_version", 0) + 1
    for k in ("slos", "health"):
        if k in d:
            d[k] = [x for x in d[k] if x.get("app") != app]
    return gone


def backup_secret_keys(app: str) -> set[str]:
    """`ops.json` secrets of the backup credentials (database role, bucket user) of both environments."""
    keys = {f"backup.pg:{db_name(app, e)}" for e in ("producao", "staging")}
    with suppress(ConnError):  # an id without a valid bucket name never had one
        keys |= {f"backup.s3:{bucket_name(app, e)}" for e in ("producao", "staging")}
    return keys


def strip_ops(d: Doc, app: str, keep_data: bool, keep_file: str) -> None:
    """ops.json: deployments, policies, approvals, maps by app; backups (except `keep_file`) unless `keep_data`."""
    for k in ("deployments", "policies", "script_approvals"):
        d[k] = [x for x in d.get(k, []) if x.get("app") != app]
    for k in ("limits", "schedules", "maintenance"):
        d[k] = {key: v for key, v in d.get(k, {}).items() if not _mine(key, app)}
    d["stopped"] = [s for s in d.get("stopped", []) if not _mine(s, app)]
    if keep_data:
        return
    d["backups"] = [
        b for b in d.get("backups", []) if b.get("app") != app or (keep_file and b.get("file") == keep_file)
    ]
    drop = backup_secret_keys(app)
    d["secrets"] = {k: v for k, v in d.get("secrets", {}).items() if k not in drop}


def strip_tokens(d: Doc, app: str, now: int) -> list[str]:
    """auth.json: personal tokens lose the app; one left with no app is revoked (`apps: []` would mean ALL apps)."""
    revoked: list[str] = []
    for t in d.get("tokens", []):
        apps = t.get("apps") or []
        if app not in apps:
            continue
        t["apps"] = [a for a in apps if a != app]
        if not t["apps"] and t.get("revoked_at") is None:
            t["revoked_at"] = now
            revoked.append(str(t["id"]))
    return revoked


def strip_connections(items: list[Doc], app: str) -> list[str]:
    """connections.json: bindings and anonymous links of the app; returns the connections that changed."""
    changed: list[str] = []
    for c in items:
        binds = [b for b in c.get("bindings", []) if b.get("app") != app]
        links = [a for a in c.get("anonymous_link_apps") or [] if a != app]
        if binds != c.get("bindings", []) or links != (c.get("anonymous_link_apps") or []):
            c["bindings"], c["anonymous_link_apps"] = binds, links
            changed.append(str(c["name"]))
    return changed
