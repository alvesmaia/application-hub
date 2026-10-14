"""Dashboard kit (light BI) for the apps: SQL datasets, filters, KPIs, server-side SVG charts and export.

No app JavaScript: the charts come out ready as SVG/HTML (``hub-dash-*`` classes with the hub tokens,
light and dark), the filters are a ``<form method="get">`` and the tables use the hub's behaviors
(``data-hub-filter``, ``data-hub-table``). CSV export (``;``, Excel pt-BR) and XLSX (``openpyxl``
write-only, extra ``hub-sdk[dashboards]``).

.. code-block:: python

    from hub_sdk import dashboards as dash, sql

    REGIAO = dash.Param("regiao", "Região", options=[("", "Todas"), ("N", "Norte"), ("S", "Sul")])
    VENDAS = dash.Dataset(
        "vendas_mes",
        "select mes, sum(valor) as total from vendas where (:regiao = '' or regiao = :regiao) group by mes",
        source="vendas-bi",                      # linked SQL connection (or dev: .hub/connections.toml)
        params=("regiao",), ttl=60,
    )
    board = dash.Dashboard("Vendas", [VENDAS], [REGIAO])

    values = board.values(request.query_params)
    r = await board.load("vendas_mes", values)
    svg = dash.line_chart(r, x="mes", y=["total"], title="Vendas por mês", unit="R$")
    csv = dash.to_csv(r)                         # or dash.export_response(r, "xlsx", "vendas")

No template (Jinja2): ``{{ svg }}`` (already a ``Markup``) and ``{{ dash.css_href(base) }}``; serve
:data:`CSS` as the app's asset (``dashboards.css``).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import math
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from fastapi.responses import Response
from markupsafe import Markup, escape

from hub_sdk import locale
from hub_sdk import sql as sql_mod
from hub_sdk.render import download_headers
from hub_sdk.sql import SqlConnection, SqlResult

__all__ = [
    "CSS",
    "Dashboard",
    "Dataset",
    "Param",
    "area_chart",
    "bar_chart",
    "chart",
    "donut_chart",
    "export_links",
    "export_response",
    "filters_form",
    "kpi",
    "kpis",
    "line_chart",
    "pie_chart",
    "table",
    "to_csv",
    "to_xlsx",
]

SERIES = 6  # cores categóricas (hub-dash-c0..c5)
W, H = 640, 260
PAD_L, PAD_R, PAD_T, PAD_B = 56, 16, 16, 40


# ---- parameters and datasets ----------------------------------------------------------------------


@dataclass(frozen=True)
class Param:
    """Dashboard filter. ``kind``: ``select`` (``options`` = [(value, label)]), ``text``, ``date``,
    ``number``. The value comes from the query string, validated here (never raw SQL: it goes as a ``:name`` parameter)."""

    name: str
    label: str
    kind: str = "select"
    options: Sequence[tuple[str, str]] = ()
    default: str = ""

    def parse(self, raw: str | None) -> str | int | float:
        v = (raw or "").strip()[:200]
        if not v:
            return self.default
        if self.kind == "select":
            return v if v in {o[0] for o in self.options} else self.default
        if self.kind == "date":
            try:
                return dt.date.fromisoformat(v).isoformat()
            except ValueError:
                return self.default
        if self.kind == "number":
            try:
                n = float(v)
            except ValueError:
                return self.default
            return int(n) if n.is_integer() else n
        return "".join(c for c in v if c.isprintable())


Source = str | SqlConnection | Callable[[], SqlConnection | None]


@dataclass
class Dataset:
    """Named query: ``sql`` with ``:name`` parameters (only those listed in ``params``), against a
    linked SQL connection (``source`` = name), a :class:`hub_sdk.sql.SqlConnection`, or a function that returns one.
    ``ttl`` = cache seconds per combination of parameters (0 = no cache)."""

    name: str
    sql: str
    source: Source
    params: tuple[str, ...] = ()
    ttl: int = 60
    max_rows: int | None = None

    def connection(self) -> SqlConnection:
        s = self.source
        conn = sql_mod.connection(s) if isinstance(s, str) else s if isinstance(s, SqlConnection) else s()
        if conn is None:
            raise sql_mod.SqlError(
                404, f"Conexão {s if isinstance(s, str) else self.name} não vinculada a este app."
            )
        return conn


@dataclass
class Dashboard:
    title: str
    datasets: Sequence[Dataset]
    params: Sequence[Param] = ()
    _cache: dict[tuple[Any, ...], tuple[float, SqlResult]] = field(default_factory=dict, repr=False)
    _locks: dict[tuple[Any, ...], asyncio.Lock] = field(default_factory=dict, repr=False)

    def values(self, query: Mapping[str, str]) -> dict[str, Any]:
        """Validated filter values from the query string."""
        return {p.name: p.parse(query.get(p.name)) for p in self.params}

    def dataset(self, name: str) -> Dataset:
        for d in self.datasets:
            if d.name == name:
                return d
        raise KeyError(name)

    async def load(
        self, name: str, values: Mapping[str, Any] | None = None, conn: SqlConnection | None = None
    ) -> SqlResult:
        """Runs (or returns from the cache) the dataset ``name`` with the values of its parameters."""
        ds = self.dataset(name)
        vals = values or {}
        args = {k: vals.get(k, "") for k in ds.params}
        key = (name, tuple(sorted((k, str(v)) for k, v in args.items())))
        now = time.monotonic()
        hit = self._cache.get(key)
        if ds.ttl > 0 and hit and now - hit[0] < ds.ttl:
            return hit[1]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:  # one query per combination, even with many simultaneous requests
            hit = self._cache.get(key)
            if ds.ttl > 0 and hit and time.monotonic() - hit[0] < ds.ttl:
                return hit[1]
            r = await (conn or ds.connection()).query(ds.sql, args, ds.max_rows)
            if ds.ttl > 0:
                if len(self._cache) > 256:
                    self._cache.clear()
                self._cache[key] = (time.monotonic(), r)
            return r

    def clear(self) -> None:
        self._cache.clear()


# ---- formatting ---------------------------------------------------------------------------------


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


# ---- KPIs ---------------------------------------------------------------------------------------


def kpi(label: str, value: Any, unit: str = "", hint: str = "", delta: float | None = None) -> Markup:
    """KPI card. ``delta`` (fraction, e.g. 0.12 = +12%) becomes an arrow and a color (up/down)."""
    d = ""
    if delta is not None:
        cls = "up" if delta > 0 else "down" if delta < 0 else "flat"
        arrow = {"up": "▲", "down": "▼", "flat": "■"}[cls]
        d = f'<span class="hub-dash-delta {cls}">{arrow} {_attr(locale.number(delta * 100, 1))}%</span>'
    h = f'<p class="hub-dash-kpi-hint">{_attr(locale.t(hint))}</p>' if hint else ""
    return _safe(
        f'<div class="hub-dash-kpi"><p class="hub-dash-kpi-label">{_attr(locale.t(label))}</p>'
        f'<p class="hub-dash-kpi-value">{_attr(fmt(value, unit))}</p>{d}{h}</div>'
    )


def kpis(items: Iterable[Markup]) -> Markup:
    return _safe('<div class="hub-dash-kpis">' + "".join(str(i) for i in items) + "</div>")


# ---- charts ---------------------------------------------------------------------------------

Series = Sequence[tuple[str, Sequence[Any]]]


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


def chart(
    kind: str,
    labels: Sequence[Any],
    series: Series,
    title: str = "",
    unit: str = "",
) -> Markup:
    """SVG chart: ``kind`` = ``line`` | ``area`` | ``bar`` | ``pie`` | ``donut``. ``series`` = [(name, values)]."""
    labs = [str(v) for v in labels]
    ser = [(n, [_num(v) for v in vals][: len(labs)]) for n, vals in series][:SERIES]
    if kind in ("pie", "donut"):
        return _pie(labs, ser[0] if ser else ("", []), title, unit, kind == "donut")
    if not labs or not ser:
        return _frame(
            title,
            f'<text class="hub-dash-axis" x="{W / 2}" y="{H / 2}" text-anchor="middle">{_attr(locale.t("Sem dados"))}</text>',
            [],
            locale.t("Sem dados"),
            kind,
        )
    allv = [v for _, vals in ser for v in vals]
    lo, hi = min(0.0, *allv), max(0.0, *allv)
    grid, y, x, slot = _axes(labs, lo, hi, unit, kind == "bar")
    parts = [grid]
    desc = []
    if kind == "bar":
        bw = slot * 0.7 / len(ser)
        for si, (name, vals) in enumerate(ser):
            for i, v in enumerate(vals):
                x0 = PAD_L + slot * i + slot * 0.15 + bw * si
                y0, y1 = y(0), y(v)
                parts.append(
                    f'<rect class="hub-dash-bar hub-dash-c{si}" x="{x0:.1f}" y="{min(y0, y1):.1f}" width="{max(bw - 2, 1):.1f}" '
                    f'height="{abs(y0 - y1):.1f}" rx="2"><title>{_attr(locale.t(name))} · {_attr(labs[i])}: {_attr(fmt(v, unit))}</title></rect>'
                )
    else:
        for si, (name, vals) in enumerate(ser):
            pts = [(x(i), y(v)) for i, v in enumerate(vals)]
            line = " ".join(f"{a:.1f},{b:.1f}" for a, b in pts)
            if kind == "area" and len(pts) > 1:
                base = y(max(lo, 0.0))
                parts.append(
                    f'<polygon class="hub-dash-area hub-dash-c{si}" points="{pts[0][0]:.1f},{base:.1f} {line} {pts[-1][0]:.1f},{base:.1f}"/>'
                )
            parts.append(f'<polyline class="hub-dash-line hub-dash-c{si}" points="{line}"/>')
            for i, (a, b) in enumerate(pts):
                parts.append(
                    f'<circle class="hub-dash-dot hub-dash-c{si}" cx="{a:.1f}" cy="{b:.1f}" r="3">'
                    f"<title>{_attr(locale.t(name))} · {_attr(labs[i])}: {_attr(fmt(vals[i], unit))}</title></circle>"
                )
    for name, vals in ser:
        desc.append(
            f"{locale.t(name)}: " + ", ".join(f"{labs[i]} {fmt(v, unit)}" for i, v in enumerate(vals[:24]))
        )
    legend = [(i, n) for i, (n, _) in enumerate(ser)] if len(ser) > 1 else []
    return _frame(title, "".join(parts), legend, "; ".join(desc)[:900], kind)


def _pie(labels: list[str], s: tuple[str, list[float]], title: str, unit: str, donut: bool) -> Markup:
    name, vals = s
    pairs = [(lab, v) for lab, v in zip(labels, vals, strict=False) if v > 0]
    total = sum(v for _, v in pairs)
    kind = "donut" if donut else "pie"
    if not pairs or total <= 0:
        return _frame(
            title,
            f'<text class="hub-dash-axis" x="{W / 2}" y="{H / 2}" text-anchor="middle">{_attr(locale.t("Sem dados"))}</text>',
            [],
            locale.t("Sem dados"),
            kind,
        )
    if len(pairs) > SERIES:  # o resto vira "Outros"
        head, tail = pairs[: SERIES - 1], pairs[SERIES - 1 :]
        pairs = [*head, (locale.t("Outros"), sum(v for _, v in tail))]
    cx, cy, r = W / 2, H / 2, H / 2 - 16
    ri = r * 0.6 if donut else 0.0
    parts, a0 = [], -math.pi / 2
    for i, (lab, v) in enumerate(pairs):
        a1 = a0 + v / total * 2 * math.pi
        tip = f"<title>{_attr(lab)}: {_attr(fmt(v, unit))} ({_attr(locale.number(v / total * 100, 1))}%)</title>"
        if len(pairs) == 1:
            ring = f'<circle class="hub-dash-slice hub-dash-c0" cx="{cx}" cy="{cy}" r="{r}">{tip}</circle>'
            if donut:
                ring += f'<circle class="hub-dash-hole" cx="{cx}" cy="{cy}" r="{ri:.1f}"/>'
            parts.append(ring)
            break
        large = 1 if a1 - a0 > math.pi else 0
        p1 = (cx + r * math.cos(a0), cy + r * math.sin(a0))
        p2 = (cx + r * math.cos(a1), cy + r * math.sin(a1))
        if donut:
            q1 = (cx + ri * math.cos(a1), cy + ri * math.sin(a1))
            q2 = (cx + ri * math.cos(a0), cy + ri * math.sin(a0))
            d = (f"M{p1[0]:.1f},{p1[1]:.1f} A{r:.1f},{r:.1f} 0 {large} 1 {p2[0]:.1f},{p2[1]:.1f} "
                 f"L{q1[0]:.1f},{q1[1]:.1f} A{ri:.1f},{ri:.1f} 0 {large} 0 {q2[0]:.1f},{q2[1]:.1f} Z")  # fmt: skip
        else:
            d = f"M{cx:.1f},{cy:.1f} L{p1[0]:.1f},{p1[1]:.1f} A{r:.1f},{r:.1f} 0 {large} 1 {p2[0]:.1f},{p2[1]:.1f} Z"
        parts.append(f'<path class="hub-dash-slice hub-dash-c{i}" d="{d}">{tip}</path>')
        a0 = a1
    if donut:
        parts.append(
            f'<text class="hub-dash-total" x="{cx}" y="{cy + 7}" text-anchor="middle">{_attr(compact(total))}</text>'
        )
    desc = f"{locale.t(name)}: " + ", ".join(f"{lab} {fmt(v, unit)}" for lab, v in pairs)
    return _frame(title, "".join(parts), [(i, lab) for i, (lab, _) in enumerate(pairs)], desc[:900], kind)


def line_chart(r: SqlResult, x: str, y: Sequence[str] | str, title: str = "", unit: str = "") -> Markup:
    labels, series = _from_result(r, x, y)
    return chart("line", labels, series, title, unit)


def area_chart(r: SqlResult, x: str, y: Sequence[str] | str, title: str = "", unit: str = "") -> Markup:
    labels, series = _from_result(r, x, y)
    return chart("area", labels, series, title, unit)


def bar_chart(r: SqlResult, x: str, y: Sequence[str] | str, title: str = "", unit: str = "") -> Markup:
    labels, series = _from_result(r, x, y)
    return chart("bar", labels, series, title, unit)


def pie_chart(r: SqlResult, label: str, value: str, title: str = "", unit: str = "") -> Markup:
    labels, series = _from_result(r, label, value)
    return chart("pie", labels, series, title, unit)


def donut_chart(r: SqlResult, label: str, value: str, title: str = "", unit: str = "") -> Markup:
    labels, series = _from_result(r, label, value)
    return chart("donut", labels, series, title, unit)


# ---- table, filters and export ---------------------------------------------------------------


def table(
    r: SqlResult,
    columns: Sequence[tuple[str, str]] | None = None,
    table_id: str = "hub-dash-table",
    page_size: int = 20,
    filter_label: str = "Filtrar linhas",
    units: Mapping[str, str] | None = None,
) -> Markup:
    """Table with the hub's filter and pagination (``data-hub-filter``/``data-hub-table``). ``columns`` =
    [(column, label)] (default: all)."""
    cols = list(columns or [(c, c) for c in r.columns])
    idx = [r.columns.index(c) for c, _ in cols]
    unit_of: dict[str, str] = dict(units) if units is not None else {}
    tid = _attr(table_id)
    first = r.rows[0] if r.rows else []
    numeric = {
        c
        for (c, _), i in zip(cols, idx, strict=True)
        if first and isinstance(first[i], int | float) and not isinstance(first[i], bool)
    }
    num_cls = ' class="hub-dash-num"'
    head = "".join(
        f'<th scope="col"{num_cls if c in numeric else ""}>{_attr(locale.t(lab))}</th>' for c, lab in cols
    )
    body = []
    for row in r.rows:
        tds = []
        for (c, _), i in zip(cols, idx, strict=True):
            v = row[i]
            num = isinstance(v, int | float) and not isinstance(v, bool)
            cls = ' class="hub-dash-num"' if num else ""
            tds.append(
                f"<td{cls}>{_attr(fmt(v, unit_of.get(c, '')) if num else ('' if v is None else v))}</td>"
            )
        body.append("<tr>" + "".join(tds) + "</tr>")
    empty_id = f"{tid}-vazio"
    fid = f"{tid}-filtro"
    note = (
        f'<p class="hub-dash-note">{_attr(locale.t("Mostrando as primeiras {n} linhas (limite da consulta).", n=len(r.rows)))}</p>'
        if r.truncated
        else ""
    )
    return _safe(
        f'<div class="hub-dash-table"><div class="form-field hub-dash-filter"><label for="{fid}">{_attr(locale.t(filter_label))}</label>'
        f'<input id="{fid}" type="search" data-hub-filter="#{tid}" data-hub-filter-empty="#{empty_id}" autocomplete="off"></div>'
        f'<div class="hub-table-scroll"><table id="{tid}" class="data-table" data-hub-table data-hub-page-size="{int(page_size)}">'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
        f'<div class="empty-state" id="{empty_id}" hidden><p>{_attr(locale.t("Nenhuma linha com este filtro."))}</p></div>{note}</div>'
    )


def filters_form(
    params: Sequence[Param], values: Mapping[str, Any], action: str, submit: str = "Aplicar"
) -> Markup:
    """Filters as ``<form method="get">`` (no JS): each change is applied by the button."""
    fields = []
    for p in params:
        fid = f"hub-dash-p-{_attr(p.name)}"
        cur = str(values.get(p.name, p.default))
        label = f'<label for="{fid}">{_attr(locale.t(p.label))}</label>'
        if p.kind == "select":
            opts = "".join(
                f'<option value="{_attr(v)}"{" selected" if v == cur else ""}>{_attr(locale.t(lab))}</option>'
                for v, lab in p.options
            )
            ctl = f'<select id="{fid}" name="{_attr(p.name)}">{opts}</select>'
        else:
            typ = {"date": "date", "number": "number"}.get(p.kind, "text")
            ctl = f'<input id="{fid}" name="{_attr(p.name)}" type="{typ}" value="{_attr(cur)}" autocomplete="off">'
        fields.append(f'<div class="form-field">{label}{ctl}</div>')
    return _safe(
        f'<form class="hub-dash-filters" method="get" action="{_attr(action)}">{"".join(fields)}'
        f'<div class="hub-actions"><button type="submit" class="primary-button">{_attr(locale.t(submit))}</button></div></form>'
    )


def export_links(href: str, values: Mapping[str, Any]) -> Markup:
    """«Export CSV/XLSX» links to the app's export route (``?format=csv|xlsx`` + filters)."""
    from urllib.parse import urlencode

    q = {k: str(v) for k, v in values.items() if str(v)}
    links = []
    for f, lab in (("csv", "Exportar CSV"), ("xlsx", "Exportar Excel")):
        url = f"{href}?{urlencode({**q, 'format': f})}"
        links.append(f'<a class="secondary-button" href="{_attr(url)}" download>{_attr(locale.t(lab))}</a>')
    return _safe('<div class="hub-actions hub-dash-export">' + "".join(links) + "</div>")


def to_csv(r: SqlResult) -> bytes:
    from hub_sdk.render import BOM, csv_line

    out = [BOM, csv_line(r.columns)]
    out += [csv_line("" if v is None else str(v) for v in row) for row in r.rows]
    return "".join(out).encode("utf-8")


def to_xlsx(r: SqlResult, sheet: str = "Dados") -> bytes:
    """XLSX in write-only mode (``openpyxl``; extra ``hub-sdk[dashboards]``). Text that looks like a formula is
    neutralized as in the CSV."""
    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    ws = wb.create_sheet(sheet[:31] or "Dados")
    ws.append(list(r.columns))
    for row in r.rows:
        ws.append([("'" + v) if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v for v in row])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_response(r: SqlResult, fmt_: str, filename: str) -> Response:
    """Download response (``csv`` or ``xlsx``) with ``Content-Disposition`` and ``no-store``."""
    if fmt_ == "xlsx":
        ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return Response(to_xlsx(r), headers=download_headers(ctype, f"{filename}.xlsx"))
    return Response(to_csv(r), headers=download_headers("text/csv; charset=utf-8", f"{filename}.csv"))


# ---- style (served by the app as an asset) -------------------------------------------------------

CSS = """/* hub_sdk.dashboards — gráficos e KPIs com os tokens do hub (claro e escuro). */
:root{--hub-dash-c0:#3b5bdb;--hub-dash-c1:#1f9d5a;--hub-dash-c2:#b87500;--hub-dash-c3:#7a5af8;--hub-dash-c4:#d64545;--hub-dash-c5:#0e8c80}
:root[data-theme="dark"]{--hub-dash-c0:#7d9bff;--hub-dash-c1:#4cc584;--hub-dash-c2:#e0a43a;--hub-dash-c3:#a993ff;--hub-dash-c4:#f08080;--hub-dash-c5:#3cc2b4}
@media (prefers-color-scheme: dark){:root[data-theme="auto"]{--hub-dash-c0:#7d9bff;--hub-dash-c1:#4cc584;--hub-dash-c2:#e0a43a;--hub-dash-c3:#a993ff;--hub-dash-c4:#f08080;--hub-dash-c5:#3cc2b4}}
.hub-dash-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:0 0 16px}
.hub-dash-kpi{background:var(--color-surface,#fff);border:1px solid var(--color-border,#d8dde6);border-radius:8px;padding:12px 14px}
.hub-dash-kpi p{margin:0}
.hub-dash-kpi-label{color:var(--color-text-muted,#5b6475);font-size:12px}
.hub-dash-kpi-value{color:var(--color-text-strong,#111827);font-size:24px;font-weight:700;line-height:1.3}
.hub-dash-kpi-hint{color:var(--color-text-muted,#5b6475);font-size:12px}
.hub-dash-delta{font-size:12px;font-weight:600}
.hub-dash-delta.up{color:var(--color-success,#1f7a46)}
.hub-dash-delta.down{color:var(--color-danger,#b42318)}
.hub-dash-delta.flat{color:var(--color-text-muted,#5b6475)}
.hub-dash-grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
.hub-dash-chart{margin:0;min-width:0}
.hub-dash-title{font-size:14px;font-weight:600;margin:0 0 6px;color:var(--color-text-strong,#111827)}
.hub-dash-svg{display:block;width:100%;height:auto;overflow:visible}
.hub-dash-grid{stroke:var(--color-border,#d8dde6);stroke-width:1}
.hub-dash-axis{fill:var(--color-text-muted,#5b6475);font-size:12px}
.hub-dash-total{fill:var(--color-text-strong,#111827);font-size:22px;font-weight:700}
.hub-dash-line{fill:none;stroke-width:2.5;stroke-linejoin:round;stroke-linecap:round}
.hub-dash-area{stroke:none;opacity:.18}
.hub-dash-dot{stroke:var(--color-surface,#fff);stroke-width:1.5}
.hub-dash-slice{stroke:var(--color-surface,#fff);stroke-width:2}
.hub-dash-hole{fill:var(--color-surface,#fff)}
.hub-dash-c0{--c:var(--hub-dash-c0)}.hub-dash-c1{--c:var(--hub-dash-c1)}.hub-dash-c2{--c:var(--hub-dash-c2)}
.hub-dash-c3{--c:var(--hub-dash-c3)}.hub-dash-c4{--c:var(--hub-dash-c4)}.hub-dash-c5{--c:var(--hub-dash-c5)}
.hub-dash-bar,.hub-dash-area,.hub-dash-dot,.hub-dash-slice{fill:var(--c)}
.hub-dash-line{stroke:var(--c)}
.hub-dash-legend{display:flex;flex-wrap:wrap;gap:4px 14px;list-style:none;margin:6px 0 0;padding:0;font-size:12px;color:var(--color-text-secondary,#374151)}
.hub-dash-legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;background:var(--c)}
.hub-dash-filters{display:flex;flex-wrap:wrap;align-items:flex-end;gap:12px}
.hub-dash-filters .form-field{min-width:160px;margin:0}
.hub-dash-filters .hub-actions{margin:0}
.hub-dash-filter{max-width:320px}
.hub-dash-num{text-align:right;font-variant-numeric:tabular-nums}
.hub-dash-note{color:var(--color-text-muted,#5b6475);font-size:12px;margin:8px 0 0}
.hub-dash-export{display:flex;gap:8px;flex-wrap:wrap}
.hub-dash-export a{text-decoration:none}
"""
