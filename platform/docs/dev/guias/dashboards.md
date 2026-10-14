# Dashboards

KPIs, charts and tables rendered on the server (SVG, no app JavaScript), from SQL.

```bash
hub project create vendas --template dashboard --yes   # or --with dashboards
cd vendas && uv sync && hub check && hub dev
```

The template includes the **Painel** (Dashboard) screen (4 KPIs, area, donut, table with filter, region filter, CSV/XLSX
export), the datasets in `src/<pacote>/painel.py` and sample data in `dev/`.

```python
from hub_sdk import dashboards as dash

BOARD = dash.Dashboard("Vendas", [
    dash.Dataset("por_mes", "select mes, sum(valor) as receita from vendas "
                 "where (:regiao = '' or regiao = :regiao) group by mes order by mes",
                 "vendas", params=("regiao",), ttl=60),
], [dash.Param("regiao", "Região", options=[("", "Todas"), ("Sul", "Sul")])])

values = BOARD.values(request.query_params)
r = await BOARD.load("por_mes", values)
grafico = dash.line_chart(r, "mes", "receita", "Receita", "R$")
```

- **In the hub**: create the connection under Conexões (Connections) (PostgreSQL, MySQL/MariaDB or SQL Server) and bind it to the app × environment;
  the app queries through the broker (`hub_sdk.sql.connection("vendas")`) — read-only, row/time limits,
  audit without data, password never in the app.
- **In `hub dev`**: `.hub/connections.toml` (copied from `dev/connections.toml`; `dev/<nome>.sql` becomes SQLite).
- Portable, parameterized SQL (`:nome`); serve `dash.CSS` together with `app.css`.

Reference: [SDK](/developers/sdk/hub-sdk-dashboards) and the full guide `DASHBOARDS.md`
([Contract](/developers/contrato) §22.1).
