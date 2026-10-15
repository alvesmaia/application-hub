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
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from fastapi.responses import Response
from markupsafe import Markup

from hub_sdk import locale
from hub_sdk import sql as sql_mod
from hub_sdk._dash_svg import PAD_B as PAD_B
from hub_sdk._dash_svg import PAD_L as PAD_L
from hub_sdk._dash_svg import PAD_R as PAD_R
from hub_sdk._dash_svg import PAD_T as PAD_T
from hub_sdk._dash_svg import SERIES as SERIES
from hub_sdk._dash_svg import H as H
from hub_sdk._dash_svg import W as W
from hub_sdk._dash_svg import _axes, _bar_marks, _frame, _from_result, _line_marks, _no_data, _pie
from hub_sdk.dashboards_format import _attr, _num, _safe
from hub_sdk.dashboards_format import compact as compact
from hub_sdk.dashboards_format import fmt as fmt
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
        return _no_data(title, kind)
    allv = [v for _, vals in ser for v in vals]
    lo, hi = min(0.0, *allv), max(0.0, *allv)
    grid, y, x, slot = _axes(labs, lo, hi, unit, kind == "bar")
    if kind == "bar":
        marks = _bar_marks(ser, labs, unit, slot, y)
    else:
        marks = _line_marks(ser, labs, unit, kind == "area", x, y, lo)
    desc = [
        f"{locale.t(name)}: " + ", ".join(f"{labs[i]} {fmt(v, unit)}" for i, v in enumerate(vals[:24]))
        for name, vals in ser
    ]
    legend = [(i, n) for i, (n, _) in enumerate(ser)] if len(ser) > 1 else []
    return _frame(title, grid + "".join(marks), legend, "; ".join(desc)[:900], kind)


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
