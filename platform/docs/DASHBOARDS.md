# Dashboards in apps (lightweight BI)

Guide for developers and agents. Dashboards are ordinary app screens: SQL → KPIs, SVG charts and tables,
rendered **on the server** (no app JavaScript), using the hub tokens (light and dark).

## Getting started

```bash
hub project create vendas --template dashboard --yes   # or --with dashboards
cd vendas && uv sync && hub check && hub dev            # http://localhost:4173/apps/vendas/painel
```

The template includes the **Painel** (Dashboard) screen (4 KPIs, area chart, donut, table with filter/pagination, region filter,
CSV/XLSX export), `src/<pkg>/painel.py` (datasets), `dev/connections.toml` + `dev/vendas.sql` (sample data)
and `tests/test_painel.py`.

## Data source

| Where | How |
|---|---|
| Hub | «Administração › Conexões» (Administration › Connections) → **Banco PostgreSQL / MySQL / SQL Server**; test and bind to the app × environment. The app queries through the broker (`hub_sdk.sql.connection("vendas")`); the password is only in the hub vault. |
| `hub dev` | `.hub/connections.toml` (copied from `dev/connections.toml` the first time; `dev/<nome>.sql` becomes `.hub/<nome>.db`). `url` = SQLite or a real database (`postgresql+psycopg://…`, `mysql+pymysql://…`, `mssql+pytds://…` with the driver in the project). |
| The app's own database | `hub_sdk.sql.from_engine(hub_sdk.db.engine())`. |

Broker rules: read-only by default (one `SELECT`/`WITH` statement, read transaction), row limit
(`truncated` when cut) and time limit, audit without data. Write **portable** and **parameterized** SQL (`:nome`);
never an f-string with a user value.

## API (`hub_sdk.dashboards`, extra `hub-sdk[dashboards]`)

```python
from hub_sdk import dashboards as dash

REGIAO = dash.Param("regiao", "Região", options=[("", "Todas"), ("Sul", "Sul")])   # select|text|date|number
BOARD = dash.Dashboard("Vendas", [
    dash.Dataset("por_mes", "select mes, sum(valor) as receita from vendas "
                 "where (:regiao = '' or regiao = :regiao) group by mes order by mes",
                 "vendas", params=("regiao",), ttl=60),            # cache per filter combination
], [REGIAO])

values = BOARD.values(request.query_params)        # validated (option outside the list → default)
r = await BOARD.load("por_mes", values)            # SqlResult: columns, rows, truncated, dicts(), column()
dash.kpis([dash.kpi("Receita", 1234.5, "R$", delta=0.12)])
dash.line_chart(r, "mes", ["receita"], "Receita", "R$")   # also area_chart, bar_chart, pie_chart, donut_chart
dash.table(r, [("mes", "Mês"), ("receita", "Receita")], units={"receita": "R$"})
dash.filters_form(BOARD.params, values, f"{base}/painel")  # <form method="get">
dash.export_links(f"/_frag/{APP_ID}/painel/exportar", values)
dash.export_response(r, "xlsx", "vendas")         # or "csv" (";", BOM, formulas neutralized)
```

Everything returns `Markup` (values escaped). In the template: `{{ grafico }}`. Serve `dash.CSS` together with `app.css`
(the shell only loads `demo.css` and `app.css`). Downloads: `download` link to `/_frag/<app>/…`.

## Best practices

- Aggregate in the database (`group by`); bring only what is needed to the screen (`max_rows`).
- Up to 6 series per chart; pie/donut groups the excess into «Outros» (Others).
- Missing connection → show a warning (the template shows «Vincule a conexão…» (Bind the connection…)), never 503 (the shell interprets it as a restart).
- Test with the local connection (`HUB_SQL_LOCAL_FILE`) — see `tests/test_painel.py`.
