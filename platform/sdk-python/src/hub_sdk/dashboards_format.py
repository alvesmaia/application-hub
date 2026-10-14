"""Number formatting and escaping shared by :mod:`hub_sdk.dashboards` (KPIs, tables, charts)."""

from __future__ import annotations

import math
from typing import Any

from markupsafe import Markup, escape

from hub_sdk import locale


def fmt(v: Any, unit: str = "", places: int | None = None) -> str:
    if v is None:
        return "–"
    if isinstance(v, bool) or not isinstance(v, int | float):
        return str(v)
    if places is None:
        places = 0 if float(v).is_integer() or abs(v) >= 1000 else 2
    s = locale.number(float(v), places)
    if unit in ("R$", "US$", "€", "$"):
        return f"{unit} {s}"
    return f"{s} {unit}".strip() if unit else s


def compact(v: float) -> str:
    a = abs(v)
    for div, suf in ((1e9, " bi"), (1e6, " mi"), (1e3, " mil")):
        if a >= div:
            return locale.number(v / div, 1 if a < div * 10 else 0) + locale.t(suf)
    return locale.number(v, 0 if float(v).is_integer() else 1)


def _num(v: Any) -> float:
    if isinstance(v, bool) or v is None:
        return 0.0
    if isinstance(v, int | float):
        return float(v) if math.isfinite(float(v)) else 0.0
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return 0.0


def _ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    out: list[float] = []
    v = start
    while not out or out[-1] < hi - 1e-9:  # the last step covers the maximum
        out.append(round(v, 10))
        v += step
    return out


def _safe(html: str) -> Markup:
    """HTML built here: every value coming from data/user went through :func:`_attr` (escaping)."""
    return Markup(html)  # noqa: S704 — only own markup, escaped values


def _attr(s: Any) -> str:
    return str(escape(str(s)))
