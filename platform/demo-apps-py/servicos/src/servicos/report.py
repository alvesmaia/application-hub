"""Relatório gerencial de chamados: ``GROUP BY`` categoria × período sobre a tabela inteira
(milhões de linhas), apoiado no índice ``tickets_report`` (varredura só de índice)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from hub_sdk.render import BOM, csv_line

from servicos.model import CATEGORIES
from servicos.ui import esc, notice, query, thousands

FRAG = "/_frag"
GRANULARITIES = (("mes", "Por mês"), ("semana", "Por semana"), ("dia", "Por dia"))
MAX_DAYS = 3 * 366  # período máximo de um relatório
ROW_BYTES = 88  # estimativa: Row + 24 bytes de texto
TRUNC = {"dia": "day", "semana": "week"}

SQL = (
    "SELECT category, date_trunc($3, opened_at AT TIME ZONE 'UTC')::date AS period, count(*)::int8, "
    "count(*) FILTER (WHERE status IN ('Resolvido','Fechado'))::int8, "
    "coalesce(avg(extract(epoch FROM resolved_at - opened_at)) FILTER (WHERE resolved_at IS NOT NULL) / 3600.0, 0)::float8, "
    "count(*) FILTER (WHERE resolved_at IS NOT NULL AND resolved_at <= sla_due)::int8 "
    "FROM tickets WHERE opened_at >= $1 AND opened_at < $2 AND ($4 = '' OR category = $4) "
    "GROUP BY 1, 2 ORDER BY 2, 1"
)


def _date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


@dataclass(frozen=True)
class Params:
    from_: date
    to: date  # inclusivo
    gran: str
    category: str

    @classmethod
    def from_query(cls, q: Mapping[str, str]) -> Params:
        today = datetime.now(UTC).date()

        def get(k: str) -> str:
            return (q.get(k) or "").strip()

        to = _date(get("ate")) or today
        frm = _date(get("de")) or to - timedelta(days=180)
        frm = min(frm, to)
        if (to - frm).days > MAX_DAYS:
            frm = to - timedelta(days=MAX_DAYS)
        gran = next((g for g, _ in GRANULARITIES if g == get("agrupar")), "mes")
        cat = next((c for c in CATEGORIES if c == get("categoria")), "")
        return cls(frm, to, gran, cat)

    def key(self) -> str:
        return f"{self.from_}|{self.to}|{self.gran}|{self.category}"

    def qs(self) -> str:
        return query(
            [
                ("de", str(self.from_)),
                ("ate", str(self.to)),
                ("agrupar", self.gran),
                ("categoria", self.category),
            ]
        )

    def describe(self) -> str:
        cat = f" · {self.category}" if self.category else ""
        return f"{self.from_:%d/%m/%Y} a {self.to:%d/%m/%Y} · {self.gran}{cat}"

    def trunc(self) -> str:
        return TRUNC.get(self.gran, "month")


@dataclass(frozen=True)
class Row:
    category: str
    period: date
    total: int
    resolved: int
    avg_hours: float
    within_sla: int


@dataclass(frozen=True)
class Report:
    rows: list[Row]
    elapsed_ms: int

    def approx_bytes(self) -> int:
        return len(self.rows) * ROW_BYTES


async def run(pool: Any, p: Params) -> Report:
    started = time.monotonic()
    start = datetime(p.from_.year, p.from_.month, p.from_.day, tzinfo=UTC)
    end = datetime(p.to.year, p.to.month, p.to.day, tzinfo=UTC) + timedelta(days=1)
    async with pool.acquire() as c:
        rows = await c.fetch(SQL, start, end, p.trunc(), p.category)
    return Report(
        [Row(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows],
        int((time.monotonic() - started) * 1000),
    )


def pct(a: int, b: int) -> str:
    return "—" if b == 0 else f"{a * 100 / b:.1f}%".replace(".", ",")


def period_label(p: Params, d: date) -> str:
    if p.gran == "mes":
        return f"{d:%m/%Y}"
    if p.gran == "semana":
        return f"sem. de {d:%d/%m/%Y}"
    return f"{d:%d/%m/%Y}"


def render(r: Report, p: Params, from_cache: bool) -> str:
    total = sum(x.total for x in r.rows)
    resolved = sum(x.resolved for x in r.rows)
    sla = sum(x.within_sla for x in r.rows)
    hours = sum(x.avg_hours * x.resolved for x in r.rows) / resolved if resolved else 0.0
    out = (
        '<div class="metric-grid hub-metrics"><div class="metric-card"><div class="metric-label">Chamados no período</div>'
        f'<div class="metric-value">{thousands(total)}</div><div class="metric-change">{esc(p.describe())}</div></div>'
        '<div class="metric-card"><div class="metric-label">Resolvidos</div>'
        f'<div class="metric-value">{thousands(resolved)}</div><div class="metric-change">{pct(resolved, total)} do total</div></div>'
        '<div class="metric-card"><div class="metric-label">Tempo médio de resolução</div>'
        f'<div class="metric-value">{hours:.1f} h</div><div class="metric-change">{pct(sla, resolved)} dentro do SLA</div></div></div>'
    )
    cache = " (cache da sessão)" if from_cache else ""
    out += (
        '<section class="app3-panel"><div class="hub-panel-head"><div><span class="app3-overline">RESULTADO</span>'
        f"<h2>{thousands(len(r.rows))} linhas · {r.elapsed_ms} ms{cache}</h2></div>"
        f'<a class="app3-outline" download href="{FRAG}/servicos/reports/export.csv{esc(p.qs())}">Exportar CSV</a></div>'
        '<div class="app3-table-scroll"><table class="app3-table"><thead><tr><th>PERÍODO</th><th>CATEGORIA</th>'
        "<th>CHAMADOS</th><th>RESOLVIDOS</th><th>TEMPO MÉDIO</th><th>NO SLA</th></tr></thead><tbody>"
    )
    if not r.rows:
        out += f'<tr><td colspan="6">{notice("info", "Sem chamados no período", "Escolha outro intervalo.")}</td></tr>'
    for x in r.rows[:2000]:
        out += (
            f"<tr><td>{esc(period_label(p, x.period))}</td><td>{esc(x.category)}</td><td>{thousands(x.total)}</td>"
            f"<td>{thousands(x.resolved)} ({pct(x.resolved, x.total)})</td><td>{x.avg_hours:.1f} h</td>"
            f"<td>{pct(x.within_sla, x.resolved)}</td></tr>"
        )
    return out + "</tbody></table></div></section>"


def csv(r: Report) -> str:
    out = BOM + csv_line(
        ["periodo", "categoria", "chamados", "resolvidos", "tempo_medio_horas", "dentro_do_sla"]
    )
    for x in r.rows:
        out += csv_line(
            [
                f"{x.period:%Y-%m-%d}",
                x.category,
                str(x.total),
                str(x.resolved),
                f"{x.avg_hours:.2f}".replace(".", ","),
                str(x.within_sla),
            ]
        )
    return out
