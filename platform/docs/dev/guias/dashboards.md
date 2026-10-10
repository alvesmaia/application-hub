# Dashboards

KPIs, gráficos e tabelas renderizados no servidor (SVG, sem JavaScript do app), a partir de SQL.

```bash
hub project create vendas --template dashboard --yes   # ou --with dashboards
cd vendas && uv sync && hub check && hub dev
```

O template traz a tela **Painel** (4 KPIs, área, rosca, tabela com filtro, filtro por região, exportação
CSV/XLSX), os datasets em `src/<pacote>/painel.py` e dados de exemplo em `dev/`.

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

- **No hub**: crie a conexão em Conexões (PostgreSQL, MySQL/MariaDB ou SQL Server) e vincule ao app × ambiente;
  o app consulta pelo broker (`hub_sdk.sql.connection("vendas")`) — somente leitura, limites de linhas/tempo,
  auditoria sem dados, senha nunca no app.
- **No `hub dev`**: `.hub/connections.toml` (copiado de `dev/connections.toml`; `dev/<nome>.sql` vira SQLite).
- SQL portável e parametrizado (`:nome`); sirva `dash.CSS` junto do `app.css`.

Referência: [SDK](/developers/sdk/hub-sdk-dashboards) e o guia completo `DASHBOARDS.md`
([Contrato](/developers/contrato) §22.1).
