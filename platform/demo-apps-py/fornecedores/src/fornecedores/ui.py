"""Utilitários de renderização (espelho de ``hub-demo-apps/src/ui.rs``)."""

from __future__ import annotations

from collections.abc import Mapping


def esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def param(q: Mapping[str, str], k: str) -> str:
    """Primeiro valor de um parâmetro de formulário/query, aparado."""
    return (q.get(k) or "").strip()


def notice(kind: str, title: str, text: str) -> str:
    return f'<div class="hub-notice hub-notice-{kind}" role="status"><strong>{esc(title)}</strong><p>{esc(text)}</p></div>'


def thousands(n: int) -> str:
    """12345678 → "12.345.678"."""
    s = f"{abs(int(n)):,}".replace(",", ".")
    return f"-{s}" if n < 0 else s
