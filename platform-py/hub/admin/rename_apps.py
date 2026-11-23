"""Renames the sample apps' ids in the hub's /data (0.4.0: ativos→inventory, fornecedores→suppliers, servicos→servicedesk,
coletor→collector, automacoes→automations). Only keys and fields that hold an app id are rewritten — never free text
(descriptions, notes, messages); the audit chain is hash-linked and signed, so past events keep the old ids. Idempotent:
running it twice, or over data already renamed, changes nothing. `reverse=True` undoes it."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

RENAMES = {"ativos": "inventory", "fornecedores": "suppliers", "servicos": "servicedesk", "coletor": "collector",
           "automacoes": "automations"}  # fmt: skip
Doc = dict[str, Any]
# First path segment of the pages whose route also went English (by new app id).
ROUTES = {"collector": {"folha": "sheet", "escanear": "scan", "diagnostico": "diagnostics", "consulta": "lookup",
                        "contagem": "count", "recebimento": "receiving", "insumos": "supplies", "sessao": "session"},
          "automations": {"catalogo": "catalog", "execucoes": "runs", "gatilhos": "triggers", "pacotes": "packages",
                          "processos": "processes"},
          "inventory": {"itens": "items"}}  # fmt: skip


def pairs(reverse: bool = False) -> dict[str, str]:
    return {v: k for k, v in RENAMES.items()} if reverse else dict(RENAMES)


def sub(text: str, ren: dict[str, str]) -> str:
    """Every id token (lower case, or upper case as in `HUB_INTERNAL_TOKEN_ATIVOS`) not glued to letters or digits."""
    for old, new in ren.items():
        text = re.sub(rf"(?<![A-Za-z0-9]){old}(?![A-Za-z0-9])", new, text)
        text = re.sub(rf"(?<![A-Za-z0-9]){old.upper()}(?![A-Za-z0-9])", new.upper(), text)
    return text


def path(p: str, ren: dict[str, str]) -> str:
    """`/apps/<id>/<section>…` with the new id and, when it was renamed, the new section."""
    p = sub(p, ren)
    m = re.match(r"^(/apps/)([a-z]+)/([a-z]+)(.*)$", p, re.S)
    if not m:
        return p
    if m[2] in ROUTES:  # forward: the routes table is keyed by the English id
        table = ROUTES[m[2]]
    else:  # reverse: back to the Portuguese sections of the old id
        table = {v: k for k, v in ROUTES.get(RENAMES.get(m[2], ""), {}).items()}
    return f"{m[1]}{m[2]}/{table.get(m[3], m[3])}{m[4]}"


def _keys(d: dict[str, Any], ren: dict[str, str]) -> dict[str, Any]:
    return {sub(k, ren): v for k, v in d.items()}


def _field(items: list[Doc], name: str, ren: dict[str, str]) -> None:
    for it in items:
        if isinstance(it.get(name), str):
            it[name] = sub(it[name], ren)


def _ids(values: list[Any], ren: dict[str, str]) -> list[Any]:
    return [sub(v, ren) if isinstance(v, str) else v for v in values]


_DSN_VARS = re.compile(r"^(DATABASE_URL|HUB_DB_URL|HUB_S3_BUCKET|HUB_S3_ENDPOINT)")


class Resealer:
    """Sealed values carry their location as AAD (`admin.env:<name>`, `ops.secret:<key>`): a renamed name or key needs
    the value opened under the old one and sealed again under the new one (the master key is required)."""

    def __init__(self, vault: Any | None) -> None:
        self.vault = vault

    def move(self, value: Any, old_aad: str, new_aad: str, edit: Callable[[str], str] = str) -> Any:
        if not isinstance(value, str) or not value.startswith("vault:"):
            return edit(value) if isinstance(value, str) else value
        if self.vault is None:
            if old_aad == new_aad:
                return value  # nothing moves; a sealed DSN stays as it is
            raise ValueError(f"valor cifrado em {old_aad}: rode com a chave mestra (HUB_MASTER_KEY_FILE)")
        plain = self.vault.open_value(value, old_aad)
        new = edit(plain)
        return value if (new, new_aad) == (plain, old_aad) else self.vault.seal(new, new_aad)


def admin(doc: Doc, ren: dict[str, str], seal: Resealer | None = None) -> Doc:
    """admin.json: variables (names, bindings, connection strings), role apps, catalog/status settings, health,
    flags, SLOs and incidents."""
    seal = seal or Resealer(None)
    for v in doc.get("env", []):
        old, v["name"] = v["name"], sub(v["name"], ren)
        _field(v.get("bindings", []), "app", ren)
        dsn = v.get("kind") == "connection_string" or bool(_DSN_VARS.match(v["name"]))
        v["value"] = seal.move(v.get("value", ""), f"admin.env:{old}", f"admin.env:{v['name']}",
                               (lambda x: sub(x, ren)) if dsn else str)  # fmt: skip
    for r in doc.get("roles", []):
        if isinstance(r.get("apps"), list):
            r["apps"] = _ids(r["apps"], ren)
    settings = doc.get("settings", {})
    for k in ("catalog_hidden_apps", "status_public_apps"):
        if isinstance(settings.get(k), list):
            settings[k] = _ids(settings[k], ren)
    for k in ("health", "flags", "slos", "incidents"):
        _field(doc.get(k, []), "app", ren)
    return doc


def ops(doc: Doc, ren: dict[str, str], seal: Resealer | None = None) -> Doc:
    """ops.json: deployments (the current one is looked up by app), backups, policies, approvals and the maps keyed
    by app (`limits`), `app|env` (`schedules`, `maintenance`) or database/bucket (`secrets`, sealed per key)."""
    for k in ("deployments", "backups", "policies", "script_approvals"):
        _field(doc.get(k, []), "app", ren)
        _field(doc.get(k, []), "file", ren)
    for k in ("limits", "schedules", "maintenance"):
        if isinstance(doc.get(k), dict):
            doc[k] = _keys(doc[k], ren)
    seal = seal or Resealer(None)
    if isinstance(doc.get("secrets"), dict):
        doc["secrets"] = {sub(k, ren): seal.move(v, f"ops.secret:{k}", f"ops.secret:{sub(k, ren)}")
                          for k, v in doc["secrets"].items()}  # fmt: skip
    return doc


def connections(doc: Doc, ren: dict[str, str]) -> Doc:
    for it in doc.get("items", []):
        _field(it.get("bindings", []), "app", ren)
    return doc


def inbox(doc: Doc, ren: dict[str, str]) -> Doc:
    for it in doc.get("items", []):
        for k in ("app", "url", "href", "link"):
            if isinstance(it.get(k), str):
                it[k] = path(it[k], ren)
    if isinstance(doc.get("grants"), dict):
        doc["grants"] = {u: _ids(a, ren) for u, a in doc["grants"].items()}
    return doc


def ux(doc: Doc, ren: dict[str, str]) -> Doc:
    for u in doc.get("users", []):
        if isinstance(u.get("favorites"), list):
            u["favorites"] = _ids(u["favorites"], ren)
        for r in u.get("recents", []):
            if isinstance(r.get("path"), str):
                r["path"] = path(r["path"], ren)
    return doc


def auth(doc: Doc, ren: dict[str, str]) -> Doc:
    for t in doc.get("tokens", []):
        if isinstance(t.get("apps"), list):
            t["apps"] = _ids(t["apps"], ren)
    return doc


def keyed(doc: Any, ren: dict[str, str]) -> Any:
    """analytics, usage, metrics and uptime: maps whose keys embed the app (`app`, `app|env`, `app/sec`, `name:app`,
    `user<TAB>app`), at any depth."""
    if isinstance(doc, dict):
        return {sub(k, ren): keyed(v, ren) for k, v in doc.items()}
    return doc


STORES: dict[str, Callable[..., Any]] = {
    "admin.json": admin, "ops.json": ops, "connections.json": connections, "inbox.json": inbox, "ux.json": ux,
    "auth.json": auth, "analytics.json": keyed, "usage.json": keyed, "metrics.json": keyed,
    "uptime-daily.json": keyed,
}  # fmt: skip
DIRS = ("backups", "apps", "outbox")  # /data/<dir>/<app>[__staging] (outbox: <app>.json)


def _move(p: Path, ren: dict[str, str]) -> Path | None:
    target = p.with_name(sub(p.name, ren))
    if target == p or target.exists():
        return None
    p.rename(target)
    return target


def _rename_paths(root: Path, ren: dict[str, str]) -> list[str]:
    """Folders and files named after the app; backup files inside too (`<app>-<ts>.zip`, matching ops.json `file`)."""
    moved: list[str] = []
    for d in DIRS:
        base = root / d
        for p in sorted(base.iterdir()) if base.is_dir() else []:
            q = _move(p, ren) or p
            if q != p:
                moved.append(f"{d}/{p.name} → {q.name}")
            if d == "backups" and q.is_dir():
                moved += [f"{d}/{q.name}/{f.name}" for f in sorted(q.iterdir()) if _move(f, ren)]
    return moved


def migrate(root: Path, reverse: bool = False, vault: Any | None = None) -> list[str]:
    """Rewrites the stores under `root` (the hub's /data) in place; returns what changed (names only, never values).
    `vault` reseals the values whose name moves; without it, such a value stops the run before anything is written
    (every store is computed first)."""
    ren, seal = pairs(reverse), Resealer(vault)
    planned: list[tuple[Path, str]] = []
    for name, fn in STORES.items():
        p = root / name
        if not p.is_file():
            continue
        before = json.loads(p.read_text(encoding="utf-8"))
        out = (
            fn(json.loads(json.dumps(before)), ren, seal)
            if fn in (admin, ops)
            else fn(json.loads(json.dumps(before)), ren)
        )
        if out != before:
            planned.append((p, json.dumps(out, ensure_ascii=False, indent=1)))
    for p, text in planned:
        p.write_text(text + "\n", encoding="utf-8")
    return [p.name for p, _ in planned] + _rename_paths(root, ren)
