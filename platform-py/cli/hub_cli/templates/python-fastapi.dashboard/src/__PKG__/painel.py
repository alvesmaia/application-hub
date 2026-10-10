"""Painel de vendas (kit de dashboards): datasets SQL, filtro e KPIs.

A conexão ``vendas`` é uma conexão SQL da plataforma (Administração › Conexões → PostgreSQL, MySQL/MariaDB ou
SQL Server, vinculada a este app × ambiente). No ``hub dev`` ela vem de ``.hub/connections.toml`` (SQLite com
os dados de ``dev/vendas.sql``) — mesmo código. Os SQL abaixo são portáveis (SQLite/Postgres/MySQL/SQL Server):
parâmetros ``:nome``, nada de f-string.
"""

from __future__ import annotations

from typing import Any

from hub_sdk import dashboards as dash

CONNECTION = "vendas"
WHERE = "(:regiao = '' or regiao = :regiao)"

REGIAO = dash.Param(
    "regiao",
    "Região",
    options=[("", "Todas"), ("Norte", "Norte"), ("Sul", "Sul"), ("Leste", "Leste"), ("Oeste", "Oeste")],
)

BOARD = dash.Dashboard(
    "Vendas",
    [
        dash.Dataset(
            "resumo",
            f"select coalesce(sum(valor), 0) as receita, coalesce(sum(pedidos), 0) as pedidos, "  # noqa: S608 — WHERE fixo
            f"count(distinct produto) as produtos from vendas where {WHERE}",
            CONNECTION,
            params=("regiao",),
        ),
        dash.Dataset(
            "por_mes",
            f"select mes, sum(valor) as receita, sum(pedidos) as pedidos from vendas where {WHERE} "  # noqa: S608
            "group by mes order by mes",
            CONNECTION,
            params=("regiao",),
        ),
        dash.Dataset(
            "por_regiao",
            f"select regiao, sum(valor) as receita from vendas where {WHERE} group by regiao order by regiao",  # noqa: S608
            CONNECTION,
            params=("regiao",),
        ),
        dash.Dataset(
            "detalhe",
            f"select mes, regiao, produto, pedidos, valor from vendas where {WHERE} "  # noqa: S608
            "order by mes, regiao, produto",
            CONNECTION,
            params=("regiao",),
            ttl=30,
        ),
    ],
    [REGIAO],
)

COLUMNS = [
    ("mes", "Mês"),
    ("regiao", "Região"),
    ("produto", "Produto"),
    ("pedidos", "Pedidos"),
    ("valor", "Valor"),
]


async def view(values: dict[str, Any]) -> dict[str, Any]:
    """Contexto do template: KPIs, 2 gráficos e a tabela (HTML/SVG prontos, sem JS)."""
    resumo = (await BOARD.load("resumo", values)).dicts()[0]
    por_mes = await BOARD.load("por_mes", values)
    receitas = por_mes.column("receita")
    delta = (receitas[-1] - receitas[-2]) / receitas[-2] if len(receitas) > 1 and receitas[-2] else None
    ticket = resumo["receita"] / resumo["pedidos"] if resumo["pedidos"] else 0
    regioes = await BOARD.load("por_regiao", values)
    detalhe = await BOARD.load("detalhe", values)
    return {
        "kpis": dash.kpis(
            [
                dash.kpi("Receita", resumo["receita"], "R$", hint="no período", delta=delta),
                dash.kpi("Pedidos", resumo["pedidos"]),
                dash.kpi("Ticket médio", round(ticket, 2), "R$"),
                dash.kpi("Produtos vendidos", resumo["produtos"]),
            ]
        ),
        "linha": dash.area_chart(por_mes, "mes", "receita", "Receita por mês", "R$"),
        "rosca": dash.donut_chart(regioes, "regiao", "receita", "Receita por região", "R$"),
        "tabela": dash.table(detalhe, COLUMNS, "vendas-detalhe", units={"valor": "R$"}),
    }
