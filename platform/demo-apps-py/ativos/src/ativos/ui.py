"""Utilitários de renderização: escape, query string,
avisos, encaixe de conteúdo dinâmico no workspace do mockup e formatação pt-BR (a tradução
para en é feita depois, por :class:`ativos.i18n.I18nMiddleware`)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import quote


def esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def query(pairs: Sequence[tuple[str, str]]) -> str:
    """``?k=v&…`` só com os pares não vazios."""
    q = [f"{k}={quote(v, safe='')}" for k, v in pairs if v]
    return "?" + "&".join(q) if q else ""


def param(q: Mapping[str, str], k: str) -> str:
    """Primeiro valor de um parâmetro de formulário/query, aparado."""
    return (q.get(k) or "").strip()


def notice(kind: str, title: str, text: str) -> str:
    return f'<div class="hub-notice hub-notice-{kind}" role="status"><strong>{esc(title)}</strong><p>{esc(text)}</p></div>'


def thousands(n: int) -> str:
    """12345678 → "12.345.678"."""
    s = f"{abs(int(n)):,}".replace(",", ".")
    return f"-{s}" if n < 0 else s


def money_cents(cents: int) -> str:
    """Centavos → "R$ 1.234,56"."""
    return f"R$ {thousands(cents // 100)},{abs(cents) % 100:02d}"


_BTN = '<button class="mobile-menu-button"'


def wrap(template: str, content: str) -> str:
    """Troca o conteúdo de ``<section class="workspace-content …">`` de um fragmento pelo
    ``content``, preservando a sidebar e o botão de menu móvel."""
    start = template.find('<section class="workspace-content')
    if start < 0:
        return content
    gt = template.find(">", start)
    open_end = start + (gt - start + 1 if gt >= 0 else 0)
    rest = template[open_end:]
    keep = 0
    if rest.startswith(_BTN):
        i = rest.find("</button>")
        keep = i + len("</button>") if i >= 0 else 0
    tail = "</section></div>" if "</section></div>" in template else ""
    return f"{template[: open_end + keep]}{content}{tail}"
