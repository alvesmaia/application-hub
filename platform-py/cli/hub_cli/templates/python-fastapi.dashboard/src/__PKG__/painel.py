"""Sales dashboard (dashboards kit): SQL datasets, filter and KPIs.

The ``vendas`` connection is a platform SQL connection (Administração › Conexões → PostgreSQL, MySQL/MariaDB or
SQL Server, bound to this app × environment). Under ``hub dev`` it comes from ``.hub/connections.toml`` (SQLite with
the data from ``dev/vendas.sql``) — same code. The SQL below is portable (SQLite/Postgres/MySQL/SQL Server):
parameters ``:name``, no f-strings.
"""

from __future__ import annotations

from typing import Any

from hub_sdk import dashboards as dash

CONNECTION = "vendas"
WHERE = "(:regiao = '' or regiao = :regiao)"

REGION = dash.Param(
    "regiao",
    "Região",
    options=[("", "Todas"), ("Norte", "Norte"), ("Sul", "Sul"), ("Leste", "Leste"), ("Oeste", "Oeste")],
)

BOARD = dash.Dashboard(
    "Vendas",
    [
        dash.Dataset(
            "summary",
            f"select coalesce(sum(valor), 0) as receita, coalesce(sum(pedidos), 0) as pedidos, "  # noqa: S608 — fixed WHERE
            f"count(distinct produto) as produtos from vendas where {WHERE}",
            CONNECTION,
            params=("regiao",),
        ),
        dash.Dataset(
            "by_month",
            f"select mes, sum(valor) as receita, sum(pedidos) as pedidos from vendas where {WHERE} "  # noqa: S608
            "group by mes order by mes",
            CONNECTION,
            params=("regiao",),
        ),
        dash.Dataset(
            "by_region",
            f"select regiao, sum(valor) as receita from vendas where {WHERE} group by regiao order by regiao",  # noqa: S608
            CONNECTION,
            params=("regiao",),
        ),
        dash.Dataset(
            "detail",
            f"select mes, regiao, produto, pedidos, valor from vendas where {WHERE} "  # noqa: S608
            "order by mes, regiao, produto",
            CONNECTION,
            params=("regiao",),
            ttl=30,
        ),
    ],
    [REGION],
)

COLUMNS = [
    ("mes", "Mês"),
    ("regiao", "Região"),
    ("produto", "Produto"),
    ("pedidos", "Pedidos"),
    ("valor", "Valor"),
]


async def view(values: dict[str, Any]) -> dict[str, Any]:
    """Template context: KPIs, 2 charts and the table (ready-made HTML/SVG, no JS)."""
    summary = (await BOARD.load("summary", values)).dicts()[0]
    by_month = await BOARD.load("by_month", values)
    revenues = by_month.column("receita")
    delta = (revenues[-1] - revenues[-2]) / revenues[-2] if len(revenues) > 1 and revenues[-2] else None
    avg_ticket = summary["receita"] / summary["pedidos"] if summary["pedidos"] else 0
    by_region = await BOARD.load("by_region", values)
    detail = await BOARD.load("detail", values)
    return {
        "kpis": dash.kpis(
            [
                dash.kpi("Receita", summary["receita"], "R$", hint="no período", delta=delta),
                dash.kpi("Pedidos", summary["pedidos"]),
                dash.kpi("Ticket médio", round(avg_ticket, 2), "R$"),
                dash.kpi("Produtos vendidos", summary["produtos"]),
            ]
        ),
        "line": dash.area_chart(by_month, "mes", "receita", "Receita por mês", "R$"),
        "donut": dash.donut_chart(by_region, "regiao", "receita", "Receita por região", "R$"),
        "table": dash.table(detail, COLUMNS, "vendas-detalhe", units={"valor": "R$"}),
    }
