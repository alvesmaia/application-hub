"""Catálogo de apps para as páginas (cartões, stack, versão implantada, situação) e rótulos de recentes."""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from hub.gateway.registry import AppEntry, Registry
from hub.ux.texts import t, tn

_SECTIONS = {
    "dashboard": "Visão geral",
    "assets": "Inventário de ativos",
    "movements": "Movimentações",
    "reports": "Relatórios",
    "settings": "Configurações",
    "requests": "Solicitações",
    "approvals": "Aprovações",
    "knowledge": "Base de conhecimento",
    "contracts": "Contratos",
    "documents": "Documentos",
    "analysis": "Análises",
    "indicators": "Indicadores",
    "import": "Importação",
    "export": "Exportação",
}
_STATE = {
    "down": ("status.down", "status-pill status-maintenance"),
    "degraded": ("status.degraded", "status-pill status-review"),
    "unstable": ("status.unstable", "status-pill status-review"),
    "ok": ("status.ok", "status-pill status-active"),
}


@dataclass
class Card:
    id: str
    name: str
    description: str
    version: str
    icon: str
    icon_style: str
    fav: bool
    stack_rows: list[tuple[str, str]]
    stack_libs: list[str]


def state_label(key: str, lang: str = "pt-BR") -> tuple[str, str]:
    label, css = _STATE.get(key, _STATE["ok"])
    return t(label, lang), css


def section_label(section: str) -> str:
    if section in _SECTIONS:
        return _SECTIONS[section]
    text = section.replace("-", " ").replace("_", " ")
    return text[:1].upper() + text[1:]


def recent_path(path: str) -> str | None:
    """Caminho canônico de Recentes (`/apps/x/dashboard` = `/apps/x`); `None` para rota só de teste."""
    path = path.split("?", 1)[0].rstrip("/")
    rel = path.removeprefix("/apps") if path.startswith("/apps/") else path
    segs = rel.lstrip("/").split("/")
    if len(segs) >= 2 and segs[1].lower().startswith("e2e"):
        return None
    if len(segs) == 2 and segs[1] == "dashboard":
        return f"/apps/{segs[0]}"
    return f"/apps{rel}"


def path_label(reg: Registry, path: str) -> str | None:
    rel = path.removeprefix("/apps") if path.startswith("/apps/") else path
    segs = rel.lstrip("/").split("/")
    entry = reg.get(segs[0])
    if entry is None:
        return None
    sec = segs[1].split("?", 1)[0] if len(segs) > 1 else None
    return f"{entry.name} · {section_label(sec)}" if sec and sec != "dashboard" else entry.name


def deployed_versions(data_dir: Path) -> dict[str, str]:
    """Release ativa por app: `deployments.json` (legado) e, por cima, a implantação `Implantado` em Produção do `ops.json`."""
    out: dict[str, str] = {}
    with suppress(OSError, ValueError, AttributeError):
        raw = json.loads((data_dir / "deployments.json").read_text(encoding="utf-8"))
        out.update({str(k): str(v) for k, v in raw.items()})
    with suppress(OSError, ValueError, AttributeError, KeyError):
        deps = json.loads((data_dir / "ops.json").read_text(encoding="utf-8")).get("deployments", [])
        for x in sorted(deps, key=lambda x: x.get("id", 0)):
            if x.get("env") == "Produção" and x.get("status") == "Implantado" and x.get("version"):
                out[str(x["app"])] = str(x["version"])
    return out


def stack_rows(e: AppEntry, version: str, lang: str = "pt-BR") -> tuple[list[tuple[str, str]], list[str]]:
    m = e.manifest
    rows: list[tuple[str, str]] = []
    libs: list[str] = []
    stack = (m.stack if m else None) or None
    if stack:
        lang_fw = (
            f"{stack['language']} · {stack['framework']}" if stack.get("framework") else stack["language"]
        )
        rows.append((t("apps.stack_lang", lang), lang_fw))
        for key, label in (("sdk", "apps.stack_sdk"), ("db", "apps.stack_db")):
            if stack.get(key):
                rows.append((t(label, lang), stack[key]))
        libs = list(stack.get("libs") or [])
    elif m and m.runtime:
        name = {"rust": "Rust", "python": "Python"}.get(
            m.runtime.get("stack", ""), m.runtime.get("stack", "")
        )
        rows.append((t("apps.stack_lang", lang), name))
    api_on = (m.api.get("enabled", True)) if m else True
    mcp_on = api_on and (m.mcp.get("enabled", True) if m else True)
    api = t("apps.stack_api_on" if api_on else "apps.stack_api_off", lang)
    mcp = t("apps.stack_mcp_on" if mcp_on else "apps.stack_mcp_off", lang)
    rows.append((t("apps.stack_api", lang), f"{api} · {mcp}"))
    rows.append((t("apps.stack_version", lang), f"v{version.lstrip('v')}"))
    return rows, libs


def cards(
    reg: Registry,
    data_dir: Path,
    favorites: list[str],
    visible: set[str] | None,
    *,
    allowed: bool,
    lang: str = "pt-BR",
) -> list[Card]:
    versions = deployed_versions(data_dir)
    out: list[Card] = []
    for e in reg.entries():
        if (visible is None or e.id in visible) != allowed:
            continue
        d = e.display
        ver = versions.get(e.id) or e.version
        rows, libs = stack_rows(e, ver, lang)
        out.append(
            Card(
                id=e.id,
                name=e.name,
                description=d.get("description", ""),
                version=ver,
                icon=d.get("icon", ""),
                icon_style=f"background:{d.get('color', '')};color:{d.get('ink', '')}",
                fav=e.id in favorites,
                stack_rows=rows,
                stack_libs=libs,
            )
        )
    return out


def card_texts(c: Card, lang: str = "pt-BR") -> dict[str, str]:
    return {
        "fav_label": tn("apps.unpin" if c.fav else "apps.pin", c.name, lang),
        "stack_label": tn("apps.stack_more", c.name, lang),
        "version_title": tn("apps.version", c.version, lang),
    }
