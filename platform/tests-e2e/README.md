# Testes E2E (onda 6) — Playwright

Roteiro de apresentação, várias abas e paridade `hub dev` × produção, contra o compose com os
usuários de demonstração (`HUB_DEMO_USERS=1`).

## Contra o hub Python (produção não tem contas demo)

`bash platform-py/scripts/tmp-instance.sh up` cria uma instância temporária (cópia do estado, demo ligado só nela,
sem socket do Docker, porta 8093) e `platform-py/.tmp-instance.env` (senha demo). Rode com
`HUB=http://127.0.0.1:8093 node ui-sweep/run.mjs --quick` (carregue o `.env` antes) e `tmp-instance.sh down` ao fim.

## Rodar

```bash
bash platform-py/scripts/tmp-instance.sh up   # instância com usuários demo (acima)
cd platform/tests-e2e
npm ci && npx playwright install chromium
npm run e2e                                # tudo (projetos demo + multitab), headless
npm run e2e:demo                           # só o roteiro de apresentação
npm run e2e:multitab                       # só o teste de várias abas
npm run e2e:report                         # abre o relatório HTML (reports/html)
```

Variáveis: `HUB` (padrão `http://localhost:8080`), `HUB_STAGING` (padrão
`http://staging.localhost:8080`), `HUB_BIN` (CLI `hub` de `platform-py/cli`; padrão `hub` no PATH),
`E2E_IMPORT_ROWS` (planilha da importação, padrão 200 000 linhas), `E2E_IMAGE_<APP>`/`E2E_IMAGE` (imagem do pacote,
`<imagem>@<Image ID local>`, obrigatória nos cenários que empacotam).

Saídas: `reports/html` (relatório), `reports/results.json`, `reports/junit.xml`; na falha,
`test-results/<teste>/` com screenshot, trace (`npx playwright show-trace …/trace.zip`) e vídeo.
Um só worker: os cenários mudam estado compartilhado (implantações, backups, variáveis).
As sessões dos três usuários são criadas uma vez (`tests/global-setup.ts` → `.auth/`).

## Cenários (`tests/`)

| Arquivo | Cenário |
|---|---|
| `01-auth.spec.ts` | login pelo formulário como admin, operador e leitor; `next=`; menu Administração; 12 áreas de admin permitidas/403 amigável por papel; API com as mesmas regras |
| `02-apps.spec.ts` | cada app pelo catálogo: loader na primeira abertura (`data-hub-loader=full`), cada seção com skeleton, sidebar ativa, tela única (rodapé da sidebar e header visíveis sem rolar), voltar do navegador; deep link/recarregar/app já aberto sem loader |
| `03-ativos.spec.ts` | busca, filtro e ordenação no servidor; mesmo ativo editado em dois contextos (admin e operador) → 409 com a mensagem de conflito; importação de 200 mil linhas com progresso → concluída + download do relatório de erros; exportação CSV (filtrada) e XLSX (tarefa → download) |
| `04-fornecedores-servicos.spec.ts` | anexo enviado e baixado byte a byte (sha256), ficha PDF, consulta externa (hub-extapi); relatório pesado do serviços + CSV; abrir chamado |
| `05-admin.spec.ts` | variáveis: criar pública e secreta com vínculos, revelar segredo, rotacionar token interno, "Aplicar (reiniciar)"; funções (matriz); logs em tempo real; saúde "Verificar agora"; usuários criar + redefinir senha (troca obrigatória no primeiro acesso); token de API; auditoria mostra as ações |
| `06-ops.spec.ts` | pacote gerado com `hub pack` (versão única) → os 7 passos → staging → Promover → Aprovar → produção → Rollback; Containers com o estado real do engine (parar/iniciar conferidos com `docker inspect`); Backup agora + Restaurar com "Em manutenção" durante a restauração; Tarefas: cancelar e reprocessar uma exportação em andamento |
| `07-staging.spec.ts` | "Ver em staging" → confirmação → host de staging (faixa STAGING, plano de controle 404) → sair no host principal encerra o staging (401) |
| `multitab.spec.ts` | 5 abas num contexto: apps e seções diferentes, recarregar, deep links sem loader, console de logs em 3 abas com **uma** conexão SSE (contada no servidor por `GET /api/admin/logs/stats` e nos eventos de rede), failover ao fechar a líder, 409 entre abas, nenhuma conexão sobra ao fechar tudo |

