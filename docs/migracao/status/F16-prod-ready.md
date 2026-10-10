# F16 — pronto para produção (2026-10-08)

Hub Python em `hub.example.com` passou do modo dev/demo para configuração de produção.

## Entregue
- `platform-py/docker-compose.prod.yml` (overlay) + `prod.env.example` + `app-env.prod.json` (semente sem `HUB_DEMO_E2E_HOSTILE`).
  Variáveis obrigatórias (`${VAR:?}`), `.prod.env` gitignored e barrado pelo `secret_guard`, cookie Secure,
  chave mestra explícita (`/data/master.key`; falha se faltar), rotas de teste dos apps = `0`, sem `HUB_DEMO_*`.
- `python -m hub.admin create-admin|deactivate-demo` (`hub/admin/__main__.py`): senha temporária só em arquivo 0600,
  `must_change_password`; nada secreto na saída.
- `scripts/tmp-instance.sh up|down`: instância temporária (cópia do estado, demo ligado só nela, sem socket Docker, porta 8093) para E2E/ui-sweep.
- `scripts/prod_cleanup.mjs`: limpeza única do estado (imprime contagens, nunca valores).
- `docs/IMPLANTACAO.md`: checklist de servidor novo (Python).

## Produção (feito)
- Backup: `C:\PROJETOS\PROD\backups\app-hub-20261008-prod-ready\` (volume `app-hub-data`, `pg_dumpall` dos dois bancos, `.dev.env`).
- Hub e apps recriados com `.prod.env` (mesmas senhas de banco/objstore, tokens internos e chave mestra: dados continuam legíveis).
- Contas `@demo.local` (admin, operador, leitor + 6 `publicador-*` de teste) **desativadas** (auditoria preservada), senha removida, sessões apagadas, tokens revogados.
- Administrador real `admin@example.com` (Administrador) com troca de senha obrigatória; senha temporária em `primeiro-acesso.txt` na pasta do backup (fora do git).

## Removido do estado
- 12 tokens de teste (f4b-*, f7a-*, mcp-client-check*, Step-up MCP*) e 13 clientes OAuth dinâmicos (re-registram sozinhos).
- 6 aprovações de script `e2e-*` (inclui `e2e-tampered.js` do servicos).
- 18 variáveis: 16 `E2E_*`, `E2E_COFRE_*` e `HUB_DEMO_E2E_HOSTILE`; reinícios pendentes descartados (servicos/coletor reiniciados antes).
- 6 incidentes "Lentidão E2E" e a flag `novo-resumo` (E2E).
- 25 itens do Inbox arquivados (app contratos removido + avisos dos clientes de teste do MCP).
- Mantidos: bancos do contratos, SLO do ativos, histórico de implantações, auditoria.

## Verificação
`/healthz`, `/login`, `.well-known/oauth-*` 200; contas demo 401; novo admin entra e é redirecionado à troca de senha (não trocada).
ui-sweep `--quick` na instância temporária (cópia do estado limpo): 0 achados.

## Rollback
1. Subir a config antiga: `docker compose --env-file .dev.env -f docker-compose.yml -f docker-compose.apps.yml up -d` (`.dev.env` guardado também no backup).
2. Estado: com o hub parado, restaurar `app-hub-data.tgz` no volume (`docker run --rm -v app-hub-data:/d -v <pasta>:/b alpine sh -c "cd /d && tar xzf /b/app-hub-data.tgz"`); bancos, se preciso, `psql < pg-hub.sql` / `pg-app.sql`.

## Lacunas
- Senhas de Postgres/MinIO continuam as do modo dev (padrões do compose; rede interna/loopback): rotacionar exige `ALTER USER` e recriar o MinIO.
- Contêineres `__staging` seguem no ar; implantações antigas com falha ficam no histórico.
- O compose de produção roda hoje a partir do worktree; após o merge, `git pull` em `application-hub-py` (o `.prod.env` já foi copiado para lá) e reconstruir a imagem para ter `hub.admin`.
- Login real do novo admin (troca de senha) e `/apps/*` autenticados em produção ficam para o dono; cobertos na instância temporária.
