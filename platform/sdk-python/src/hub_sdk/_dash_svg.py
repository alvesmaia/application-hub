"""SVG drawing of :mod:`hub_sdk.dashboards` charts: frame, axes, bars, lines and pie/donut slices."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence

from markupsafe import Markup

from hub_sdk import locale
from hub_sdk.dashboards_format import _attr, _num, _safe, _ticks, compact, fmt
from hub_sdk.sql import SqlResult

SERIES = 6  # cores categóricas (hub-dash-c0..c5)
W, H = 640, 260
PAD_L, PAD_R, PAD_T, PAD_B = 56, 16, 16, 40


def _from_result(
    r: SqlResult, x: str, y: Sequence[str] | str
) -> tuple[list[str], list[tuple[str, list[float]]]]:
    ys = [y] if isinstance(y, str) else list(y)
    labels = [str(v) if v is not None else "–" for v in r.column(x)]
    return labels, [(c, [_num(v) for v in r.column(c)]) for c in ys]


def _frame(title: str, body: str, legend: list[tuple[int, str]], desc: str, kind: str) -> Markup:
    tid = "hub-dash-t-" + re.sub(r"[^a-z0-9]+", "-", f"{kind}-{title}".lower()).strip("-")[:48]
    leg = ""
    if legend:
        leg = (
            '<ul class="hub-dash-legend">'
            + "".join(
                f'<li><i class="hub-dash-c{i % SERIES}" aria-hidden="true"></i>{_attr(locale.t(n))}</li>'
                for i, n in legend
            )
            + "</ul>"
        )
    head = f'<h3 class="hub-dash-title" id="{tid}">{_attr(locale.t(title))}</h3>' if title else ""
    label = f'aria-labelledby="{tid}"' if title else f'aria-label="{_attr(desc)}"'
    return _safe(
        f'<figure class="hub-dash-chart hub-dash-k-{kind}">{head}'
        f'<svg class="hub-dash-svg" viewBox="0 0 {W} {H}" role="img" {label} preserveAspectRatio="xMidYMid meet">'
        f"<desc>{_attr(desc)}</desc>{body}</svg>{leg}</figure>"
    )


def _axes(
    labels: list[str], lo: float, hi: float, unit: str, band: bool
) -> tuple[str, Callable[[float], float], Callable[[int], float], float]:
    ticks = _ticks(lo, hi)
    lo, hi = min(ticks[0], lo), max(ticks[-1], hi)
    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    n = max(len(labels), 1)
    slot = pw / n

    def y(v: float) -> float:
        return PAD_T + ph - (v - lo) / (hi - lo) * ph

    def x(i: int) -> float:
        return PAD_L + slot * (i + 0.5) if band else PAD_L + (pw * i / (n - 1) if n > 1 else pw / 2)

    parts = []
    for t in ticks:
        yy = y(t)
        parts.append(
            f'<line class="hub-dash-grid" x1="{PAD_L}" x2="{W - PAD_R}" y1="{yy:.1f}" y2="{yy:.1f}"/>'
        )
        parts.append(
            f'<text class="hub-dash-axis" x="{PAD_L - 8}" y="{yy + 4:.1f}" text-anchor="end">{_attr(compact(t))}</text>'
        )
    every = max(1, math.ceil(n / 8))
    for i, lab in enumerate(labels):
        if i % every == 0 or (i == n - 1 and n <= 8):
            short = lab if len(lab) <= 12 else lab[:11] + "…"
            parts.append(
                f'<text class="hub-dash-axis" x="{x(i):.1f}" y="{H - PAD_B + 18}" text-anchor="middle">{_attr(short)}</text>'
            )
    return "".join(parts), y, x, slot


def _no_data(title: str, kind: str) -> Markup:
    return _frame(
        title,
        f'<text class="hub-dash-axis" x="{W / 2}" y="{H / 2}" text-anchor="middle">{_attr(locale.t("Sem dados"))}</text>',
        [],
        locale.t("Sem dados"),
        kind,
    )


def _pie(labels: list[str], s: tuple[str, list[float]], title: str, unit: str, donut: bool) -> Markup:
    name, vals = s
    pairs = [(lab, v) for lab, v in zip(labels, vals, strict=False) if v > 0]
    total = sum(v for _, v in pairs)
    kind = "donut" if donut else "pie"
    if not pairs or total <= 0:
        return _no_data(title, kind)
    if len(pairs) > SERIES:  # o resto vira "Outros"
        head, tail = pairs[: SERIES - 1], pairs[SERIES - 1 :]
        pairs = [*head, (locale.t("Outros"), sum(v for _, v in tail))]
    parts = _slices(pairs, total, unit, donut)
    if donut:
        parts.append(
            f'<text class="hub-dash-total" x="{_CX}" y="{_CY + 7}" text-anchor="middle">{_attr(compact(total))}</text>'
        )
    desc = f"{locale.t(name)}: " + ", ".join(f"{lab} {fmt(v, unit)}" for lab, v in pairs)
    return _frame(title, "".join(parts), [(i, lab) for i, (lab, _) in enumerate(pairs)], desc[:900], kind)


_CX, _CY, _R = W / 2, H / 2, H / 2 - 16


def _slices(pairs: list[tuple[str, float]], total: float, unit: str, donut: bool) -> list[str]:
    def tip(lab: str, v: float) -> str:
        return f"<title>{_attr(lab)}: {_attr(fmt(v, unit))} ({_attr(locale.number(v / total * 100, 1))}%)</title>"

    ri = _R * 0.6 if donut else 0.0
    if len(pairs) == 1:  # a single slice is a whole circle (an arc cannot close on itself)
        lab, v = pairs[0]
        ring = f'<circle class="hub-dash-slice hub-dash-c0" cx="{_CX}" cy="{_CY}" r="{_R}">{tip(lab, v)}</circle>'
        if donut:
            ring += f'<circle class="hub-dash-hole" cx="{_CX}" cy="{_CY}" r="{ri:.1f}"/>'
        return [ring]
    parts: list[str] = []
    a0 = -math.pi / 2
    for i, (lab, v) in enumerate(pairs):
        a1 = a0 + v / total * 2 * math.pi
        parts.append(
            f'<path class="hub-dash-slice hub-dash-c{i}" d="{_slice_path(a0, a1, ri)}">{tip(lab, v)}</path>'
        )
        a0 = a1
    return parts


def _slice_path(a0: float, a1: float, ri: float) -> str:
    """Path of the slice from angle ``a0`` to ``a1``; ``ri`` > 0 = donut (ring segment)."""
    cx, cy, r = _CX, _CY, _R
    large = 1 if a1 - a0 > math.pi else 0
    p1 = (cx + r * math.cos(a0), cy + r * math.sin(a0))
    p2 = (cx + r * math.cos(a1), cy + r * math.sin(a1))
    if not ri:
        return f"M{cx:.1f},{cy:.1f} L{p1[0]:.1f},{p1[1]:.1f} A{r:.1f},{r:.1f} 0 {large} 1 {p2[0]:.1f},{p2[1]:.1f} Z"
    q1 = (cx + ri * math.cos(a1), cy + ri * math.sin(a1))
    q2 = (cx + ri * math.cos(a0), cy + ri * math.sin(a0))
    return (f"M{p1[0]:.1f},{p1[1]:.1f} A{r:.1f},{r:.1f} 0 {large} 1 {p2[0]:.1f},{p2[1]:.1f} "
            f"L{q1[0]:.1f},{q1[1]:.1f} A{ri:.1f},{ri:.1f} 0 {large} 0 {q2[0]:.1f},{q2[1]:.1f} Z")  # fmt: skip
