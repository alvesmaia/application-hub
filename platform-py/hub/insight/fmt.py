"""pt-BR number formatting for the observability screens (decimal comma)."""

from __future__ import annotations


def num(v: float, places: int) -> str:
    return f"{v:.{places}f}".replace(".", ",")


def pct(v: float) -> str:
    return f"{num(v * 100, 1 if v >= 0.9995 or v == 0 else 2)}%"


def pc2(v: float) -> str:
    return f"{num(v * 100, 2)}%"
