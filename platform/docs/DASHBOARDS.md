# Dashboards nos apps (BI leve)

Guia para desenvolvedores e agentes. Dashboards são telas comuns do app: SQL → KPIs, gráficos SVG e tabelas,
renderizados **no servidor** (sem JavaScript do app), com os tokens do hub (claro e escuro).

## Começar

```bash
hub project create vendas --template dashboard --yes   # ou --with dashboards
cd vendas && uv sync && hub check && hub dev            # http://localhost:4173/apps/vendas/painel
```

O template traz: tela **Painel** (4 KPIs, gráfico de área, rosca, tabela com filtro/paginação, filtro por região,
exportação CSV/XLSX), `src/<pkg>/painel.py` (datasets), `dev/connections.toml` + `dev/vendas.sql` (dados de exemplo)
e `tests/test_painel.py`.

## Fonte dos dados

| Onde | Como |
|---|---|
| Hub | Administração › Conexões → **Banco PostgreSQL / MySQL / SQL Server**; teste e vincule ao app × ambiente. O app consulta pelo broker (`hub_sdk.sql.connection("vendas")`); senha só no cofre do hub. |
| `hub dev` | `.hub/connections.toml` (copiado de `dev/connections.toml` na 1ª vez; `dev/<nome>.sql` vira `.hub/<nome>.db`). `url` = SQLite ou banco real (`postgresql+psycopg://…`, `mysql+pymysql://…`, `mssql+pytds://…` com o driver no projeto). |
| Banco do próprio app | `hub_sdk.sql.from_engine(hub_sdk.db.engine())`. |

Regras do broker: somente leitura por padrão (uma instrução `SELECT`/`WITH`, transação de leitura), limite de linhas
(`truncated` quando corta) e de tempo, auditoria sem dados. Escreva SQL **portável** e **parametrizado** (`:nome`);
nunca f-string com valor de usuário.

## API (`hub_sdk.dashboards`, extra `hub-sdk[dashboards]`)

```python
from hub_sdk import dashboards as dash

REGIAO = dash.Param("regiao", "Região", options=[("", "Todas"), ("Sul", "Sul")])   # select|text|date|number
BOARD = dash.Dashboard("Vendas", [
    dash.Dataset("por_mes", "select mes, sum(valor) as receita from vendas "
                 "where (:regiao = '' or regiao = :regiao) group by mes order by mes",
                 "vendas", params=("regiao",), ttl=60),            # cache por combinação de filtros
], [REGIAO])

values = BOARD.values(request.query_params)        # validados (opção fora da lista → padrão)
r = await BOARD.load("por_mes", values)            # SqlResult: columns, rows, truncated, dicts(), column()
dash.kpis([dash.kpi("Receita", 1234.5, "R$", delta=0.12)])
dash.line_chart(r, "mes", ["receita"], "Receita", "R$")   # também area_chart, bar_chart, pie_chart, donut_chart
dash.table(r, [("mes", "Mês"), ("receita", "Receita")], units={"receita": "R$"})
dash.filters_form(BOARD.params, values, f"{base}/painel")  # <form method="get">
dash.export_links(f"/_frag/{APP_ID}/painel/exportar", values)
dash.export_response(r, "xlsx", "vendas")         # ou "csv" (";", BOM, fórmulas neutralizadas)
```

Tudo devolve `Markup` (valores escapados). No template: `{{ grafico }}`. Sirva `dash.CSS` junto do `app.css`
(o shell só carrega `demo.css` e `app.css`). Downloads: link `download` para `/_frag/<app>/…`.

## Boas práticas

- Agregue no banco (`group by`); traga para a tela só o necessário (`max_rows`).
- Até 6 séries por gráfico; pizza/rosca agrupa o excedente em «Outros».
- Conexão ausente → mostre aviso (o template mostra «Vincule a conexão…»), nunca 503 (o shell entende como reinício).
- Teste com a conexão local (`HUB_SQL_LOCAL_FILE`) — veja `tests/test_painel.py`.
