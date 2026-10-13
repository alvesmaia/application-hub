"""O shell no idioma do usuário: menu, textos do JS e paleta Ctrl+K (catálogos pt-BR/en) e o tema."""

from __future__ import annotations

import json
from copy import deepcopy
from functools import cache
from pathlib import Path
from typing import Any, cast

from hub.ux.texts import _LANGS, _table, t, tr

_WEB = Path(__file__).parent.parent / "web"
# rótulos do menu traduzidos por chave (os demais vêm do catálogo)
_KEYED = {
    "Início": "shell.home",
    "Aplicações": "shell.apps",
    "Status": "shell.status",
    "Inbox": "shell.inbox",
}


_GROUPS = {"Operação": ("Operations",), "Governança": ("Governance",)}


def label(text: str, lang: str) -> str:
    if text in _KEYED:
        return t(_KEYED[text], lang)
    if text in _GROUPS and lang in _LANGS[1:]:
        return _GROUPS[text][_LANGS.index(lang) - 1]
    return tr(text, lang)


@cache
def nav_for(lang: str) -> dict[str, Any]:
    nav = cast(dict[str, Any], json.loads((_WEB / "nav.json").read_text(encoding="utf-8")))
    nav = deepcopy(nav)
    for it in nav["top"]:
        it["label"] = label(it["label"], lang)
    for g in nav["groups"]:
        g["name"] = label(g["name"], lang)
        for it in g["items"]:
            it["label"] = label(it["label"], lang)
    return nav


@cache
def i18n_for(lang: str) -> str:
    """`data-hub-i18n`: os textos do JS do shell no idioma (`lang` = o próprio código)."""
    base = cast(dict[str, str], json.loads((_WEB / "shell_i18n.json").read_text(encoding="utf-8")))
    col = _LANGS.index(lang) if lang in _LANGS else 0
    table = _table()
    out = {k: (lang if k == "lang" else table[k][col] if k in table else v) for k, v in base.items()}
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


@cache
def platform_palette(lang: str) -> list[dict[str, Any]]:
    rows = json.loads((_WEB / "shell_palette.json").read_text(encoding="utf-8"))
    return [
        r
        | {
            "title": t(_KEYED[r["title"]], lang) if r["title"] in _KEYED else r["title"],
            "kind": tr(r["kind"], lang),
        }
        for r in rows
    ]


def theme_of(pref: str) -> tuple[str, str]:
    """(`data-theme`, `color-scheme`): o padrão é claro; `auto` segue o sistema."""
    return {"dark": ("dark", "dark"), "auto": ("auto", "light dark")}.get(pref, ("light", "light"))


def visible_nav(nav: dict[str, Any], perms: list[str]) -> dict[str, Any]:
    """Menu de Administração só com o que o usuário pode abrir."""
    from hub.auth import admin_required

    def ok(href: str) -> bool:
        parts = href.strip("/").split("/")
        return any(p in perms for p in admin_required(parts[1] if len(parts) > 1 else ""))

    groups = []
    for g in nav["groups"]:
        items = [i for i in g["items"] if ok(i["href"])]
        if items:
            groups.append(g | {"items": items})
    return nav | {"groups": groups}


# Abas por página: (caminho, valor de ?tab=, rótulo pt-BR). O conjunto inteiro aparece sob o título.
TAB_SETS: tuple[tuple[tuple[str, str, str], ...], ...] = (
    (("/admin/users", "", "Usuários"), ("/admin/users", "roles", "Funções")),
    (("/admin/logs", "", "Logs"), ("/admin/audit", "", "Auditoria")),
    (("/admin/env", "", "Variáveis"), ("/admin/connections", "", "Conexões")),
)


def tabs_for(path: str, tab: str, perms: list[str]) -> list[tuple[str, str, bool]] | None:
    """Abas (href, rótulo, ativa) da página, só com o que o usuário pode abrir."""
    from hub.auth import admin_required

    for tabs in TAB_SETS:
        if any(p == path for p, _, _ in tabs):
            out = [
                (p + (f"?tab={q}" if q else ""), label, p == path and q == tab)
                for p, q, label in tabs
                if any(x in perms for x in admin_required(p.strip("/").split("/")[1]))
            ]
            return out if len(out) > 1 else None
    return None


def eyebrow_for(path: str, lang: str) -> str:
    """Linha acima do título de uma página: o grupo do menu da rota (`/admin/users` → "Acesso")."""
    path = path.split("?")[0]
    if path == "/admin" or path.startswith("/admin/"):
        best: tuple[int, str] | None = None
        for g in nav_for(lang)["groups"]:
            for it in g["items"]:
                for r in [str(it["href"]), *it.get("also", [])]:
                    if (path == r or path.startswith(r + "/")) and (best is None or len(r) > best[0]):
                        best = (len(r), str(g["name"]))
        return best[1] if best else t("shell.admin", lang)
    for prefix, key in (("/applications", "shell.apps"), ("/status", "shell.status"), ("/inbox", "shell.inbox"),
                        ("/search", "search.title"), ("/account", "acct.eyebrow")):  # fmt: skip
        if path.startswith(prefix):
            return t(key, lang)
    return t("shell.home", lang) if path == "/" else ""
