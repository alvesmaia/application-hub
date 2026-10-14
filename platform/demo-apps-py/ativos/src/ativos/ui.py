"""Rendering utilities: escaping, query string,
warnings, fitting dynamic content into the mockup workspace, and pt-BR formatting (translation
to en is done later, by :class:`ativos.i18n.I18nMiddleware`)."""

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
    """``?k=v&…`` only with the non-empty pairs."""
    q = [f"{k}={quote(v, safe='')}" for k, v in pairs if v]
    return "?" + "&".join(q) if q else ""


def param(q: Mapping[str, str], k: str) -> str:
    """First value of a form/query parameter, trimmed."""
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
    """Replaces the content of ``<section class="workspace-content …">`` of a fragment with
    ``content``, preserving the sidebar and the mobile menu button."""
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
