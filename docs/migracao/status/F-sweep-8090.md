# F-sweep-8090 — ui-sweep e Playwright @core contra Python hub :8090 (2026-10-06, 2ª rodada)

## Resumo executivo

**Status:** PARCIALMENTE BLOQUEADO. Autenticação corrigida (commit 9793874); ui-sweep rodou, Playwright interrompido por timeout.

- **ui-sweep quick:** 54 rotas descobertas, 2/4 testes passaram (desktop), 108 achados fora de allowlist
- **Playwright @core:** 22 falhas documentadas, 49 não rodaram, worker crashes, timeouts em páginas/API
- **Bloqueadores principais:** RBAC bugs (/admin 404, admin-menu para leitor), timeouts, crashes do Chromium

## Teste ui-sweep: resultados

### Descoberta de rotas
- **Rotas totais:** 54
- **Profundidade:** 2
- **Links excluídos:** 3
- **Arquivo:** `platform/loadtest/results/2026-10-06/ui-sweep-163146-quick/routes.json`

Rotas incluem: `/`, `/admin/*` (12+), `/account/*` (4), `/applications`, `/apps/*` (20+), `/inbox`, `/search`, `/status`.

### Testes Playwright
- **Total:** 4 (admin × 2 temas × 2 viewports)
- **Passaram:** 2 ✅
  - `@sweep admin-light-1440` (7.0m)
  - `@sweep admin-dark-1440` (7.0m)
- **Falharam:** 2 ❌
  - `@sweep admin-light-390`: `Error: browserContext.newPage: Target crashed` (59ms)
  - `@sweep admin-dark-390`: `Error: browserContext.newPage: Target crashed` (60ms)

### Visitação
- **Total de visitas:** 108 (54 rotas × 2 temas × 1 papel)
- **Navegação:** 108/108 successful
- **Achados fora de allowlist:** 108
- **Relatório completo:** `C:\PROJETOS\PROD\application-hub-py\platform\loadtest\results\2026-10-06\ui-sweep-163146-quick\report.md`

**Nota:** Teste interrompido após ~10 minutos (timeout de background task de 30 min). Viewport móvel (390px) causou crashes no Chromium.

## Teste Playwright @core: resultados

### Resumo de execução
- **Total de testes com @core:** 71
- **Falharam:** 22 (31%)
- **Não rodaram (skipped):** 49 (69%)
- **Passou:** 0
- **Tempo total:** 30+ min (interrompido por timeout de background task)

### Top 10 achados (por severidade e impacto)

| # | Teste | Erro | Linha | Tipo | Severidade |
|---|---|---|---|---|---|
| 1 | `01-auth.spec.ts:14:7 › login como admin: permissões...` | `admin /admin` — `expect(404).toBe(200)` | `tests/01-auth.spec.ts:35:45` | RBAC bug — /admin não acessível | **HIGH** |
| 2 | `01-auth.spec.ts:14:7 › login como operador: permissões...` | `operador /admin` — `expect(404).toBe(200)` | `tests/01-auth.spec.ts:35:45` | RBAC bug — /admin não acessível | **HIGH** |
| 3 | `01-auth.spec.ts:14:7 › login como leitor: permissões...` | `expect(locator('#hub-admin-menu')).toHaveCount(0)` mas encontrou 1 | `tests/01-auth.spec.ts:29:54` | RBAC bug — admin-menu visível para leitor | **HIGH** |
| 4 | `02-apps.spec.ts:15:7 › abrir ativos: carregador...` | `TimeoutError: page.goto: Timeout 45000ms exceeded` (`/applications`) | `tests/02-apps.spec.ts:19:16` | Performance ou não implementado | **HIGH** |
| 5 | `03-ativos.spec.ts:14:5 › ativos: busca, ordenação...` | `TimeoutError: page.goto: Timeout 45000ms exceeded` (`/apps/ativos/assets`) | `tests/helpers.ts:53:14` | Performance | **HIGH** |
| 6 | `05-admin.spec.ts:51:5 › funções e permissões...` | `TimeoutError: page.goto: Timeout 45000ms exceeded` (`/admin/roles`) | `tests/05-admin.spec.ts:52:14` | Performance | **HIGH** |
| 7 | `13-onda12-oauth.spec.ts:15:5 › consentimento OAuth...` | `TimeoutError: apiRequestContext.post: Timeout 20000ms exceeded` (`/oauth/register`) | `tests/13-onda12-oauth.spec.ts:17:25` | OAuth não respondendo | **HIGH** |
| 8 | `13-onda12-oauth.spec.ts:88:5 › pedido de autorização...` | `TimeoutError: apiRequestContext.post: Timeout 20000ms exceeded` (`/oauth/register`) | `tests/13-onda12-oauth.spec.ts:90:25` | OAuth não respondendo | **HIGH** |
| 9 | `02-apps.spec.ts:58:5 › caminho antigo do app...` | `TimeoutError: apiRequestContext.get: Timeout 20000ms exceeded` (`/ativos/assets?q=...`) | `tests/02-apps.spec.ts:60:31` | Redirecionamento lento ou falha | **MEDIUM** |
| 10 | `14-onda12-antiphishing.spec.ts:29:5 › (a) fragmento hostil...` | `TimeoutError: page.goto: Timeout 45000ms exceeded` (`/apps/ativos/e2e-hostil`) | `tests/14-onda12-antiphishing.spec.ts:38:26` | Rota não existente ou lenta | **MEDIUM** |

### Worker crashes (Chromium)

**Código de erro:** `3221225794`  
**Testes afetados:** 10+ (i18n, modais, login, viewport, configurações, mobile, admin, skeleton, catálogo, coletor)  
**Padrão:** crash ocorre após ~22 testes, sugerindo memory leak ou file descriptor exaurido.

Exemplos:
- `16-onda12-i18n.spec.ts:13:7 › inglês › Administração e Operações em inglês...`
- `17-ux-modais.spec.ts:14:5 › modal do hub (Aplicações › Variáveis)...`
- `18-login-viewport.spec.ts:13:7 › login sem rolagem em 1280x720`
- `19-conta.spec.ts:83:5 › apps autorizados...`
- `20-ui-revisao2.spec.ts:13:5 › Configurações: tema em 3 opções...`

### Limpeza E2E incompleta

**Erro:** `global-teardown TimeoutError: apiRequestContext.get: Timeout 30000ms exceeded`  
**Endpoint:** `GET http://localhost:8090/api/admin/tokens`  
**Tipo:** Performance — rota lenta ou não respondendo

## Categorização de achados

| Categoria | Contagem | Exemplos | Tipo |
|---|---|---|---|
| Timeout de página (45s) | 4+ | `/applications`, `/admin/roles`, `/apps/ativos/assets`, `/apps/ativos/e2e-hostil` | Performance ou não implementado |
| Timeout de API (20-30s) | 3+ | `/oauth/register`, `/api/admin/tokens`, redirecionamento `/ativos/assets?q=...` | Performance ou não respondendo |
| RBAC bug (404 ou visibilidade) | 3 | `/admin` 404 (admin+operador), `#hub-admin-menu` visível para leitor | Python-port bug |
| Worker crash (Chromium) | 10+ | Post-teste após múltiplas operações | Memory leak ou fds exauridos |
| Chromium crash (mobile) | 2 | Viewport 390px em ui-sweep | Layout ou resource issue |

## Diagnóstico por tipo

### Python-port bugs confirmados
1. **RBAC:** rota `/admin` retorna 404 para admin/operador (deveria retornar 200)
   - Arquivo: `hub/app.py`, `hub/web/routes.py`
   - Hipótese: rota não mapeada ou RBAC muito restritivo
   
2. **RBAC:** elemento `#hub-admin-menu` renderizado para leitor (deveria ser ausente)
   - Arquivo: `hub/web/templates/*.html`
   - Hipótese: renderização condicional não implementada

3. **OAuth:** `/oauth/register` timeout 20s (não responde)
   - Arquivo: `hub/oauth.py` ou equivalente
   - Hipótese: não implementado ou extremamente lento

### Performance/infraestrutura
- Timeouts sistemáticos em `page.goto(45s)` sugerem servidor lento, database lento ou rotas pesadas
- Worker crashes (code 3221225794) após ~22 testes sugerem memory leak ou fds não fechados
- Chromium crash em viewport 390px sugerem issue com layout responsivo

### Testes não rodados
49 testes skipped (69%). Possível razão:
- Dependência em testes anteriores que falharam
- Worker crash impedindo sequência
- Timeout durante execução

## Recursos de teste utilizados

- **Python hub:** `hubpy-web` em `http://localhost:8090`, uptime ~2.5h
- **ui-sweep:** 54 rotas, 108 visitas, 2 temas (desktop OK, mobile crash)
- **Playwright:** 71 testes @core, 22 falharam, 49 não rodaram
- **Duração:** ui-sweep ~10 min (interrompido), Playwright ~30+ min (interrompido)
- **Browser:** Chromium 153.0.8010.12, Node 20.18
- **Sistema:** Windows 11

## Próximas etapas de correção

1. **RBAC /admin (HIGH):** Verificar routing e middleware RBAC em `hub/app.py`, `hub/web/routes.py`
2. **RBAC admin-menu (HIGH):** Implementar renderização condicional em templates Jinja2
3. **OAuth (HIGH):** Verificar implementação de `/oauth/register` em `hub/oauth.py`
4. **Performance (HIGH):** Profiling de page.goto timeouts; verificar database, gateway, app startup
5. **Memory leak (HIGH):** Analisar fds/conexões em logs de worker; possível recursive/ciclo não fechado
6. **Mobile layout (MEDIUM):** Verificar CSS responsivo em viewport 390px

---

**Data:** 2026-10-06 17:15 UTC  
**Agente:** Claude Haiku 4.5 (haiku-4-5-20251001)  
**Fixos:** Commit 9793874 (autenticação login)  
**Status:** F-sweep-8090.md (este arquivo)
