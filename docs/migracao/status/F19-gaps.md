# F19 — lacunas de produção

Entrega em `stack/python` (commit `feat(hub-py): F19 …`). Testes novos em `platform-py/tests/test_f19_gaps.py`
(+ self-heal de staging em `tests/test_f5b.py`).

## O que mudou

1. **Senhas de infraestrutura.** No boot o hub troca as senhas de dev (`hubpy-dev`, `hubpy-app-dev`,
   `hubpy-objstore-dev`) dentro dos valores das variáveis do cofre (DSNs, `HUB_S3_SECRET_KEY`) pelas de
   `APP_HUB_DB_PASSWORD`/`APP_HUB_APP_DB_PASSWORD`/`APP_HUB_OBJSTORE_PASSWORD` (só se ≥ 16 caracteres), com
   auditoria "Senhas de infraestrutura rotacionadas" (só nomes, nunca valores). `ConfigOps.replace_secret_values`.
   Rotação: senhas novas no `.prod.env`, `ALTER USER hub` nos dois Postgres, recriar `app-hub-objstore`, hub e apps.
2. **Build.** `HUB_BUILD_CPUS` (padrão 2, `cpusetcpus`), `HUB_BUILD_TIMEOUT_S` (padrão 900; fecha o fluxo e o build
   falha com "tempo limite"), `HUB_BUILD_MEMORY` como antes. `build/.dockerignore` do pacote é aplicado antes de
   gravar o contexto (`pipeline.build_context`; Dockerfile e o próprio `.dockerignore` ficam).
3. **Poda de imagens fixadas.** Depois de cada blue/green, só as `HUB_KEEP_IMAGES` (padrão 3) fixações mais
   recentes (`LastTagTime`) por app/ambiente: `keep-*` (produção) e `keep-stg-*` (staging); `baseline-*` não é
   podada; imagem em uso não é apagada pelo daemon.
4. **Staging.** Faixa discreta "Você está em staging · Voltar à produção" no shell com o cookie `hub_env=staging`
   (3 idiomas; CSS no `/assets/theme.css`). Supervisão: `<app>__staging` ausente é recriado a partir da última
   implantação `Implantado` de staging (recuo de 5 min, respeita "parado" e manutenção; auditoria "Staging recriado").
5. **Containers de produção recriados "sozinhos".** Investigado: os quatro `app-hub-app-*` foram criados juntos às
   15:53Z de 2026-10-08 com o rótulo `com.docker.compose.replace` e sem `hub.role` — foi o `docker compose up` da
   entrega F16 (prod-ready, troca para `.prod.env`), não o hub (rollout/supervisor gravam `hub.role=app`). Não é bug
   do hub. Cuidado operacional: `compose up` sem lista de serviços recria apps cuja configuração mudou e desfaz
   implantações feitas pelo hub; em produção use `up -d --no-deps <serviço>`.
6. **Várias réplicas.** `ops.json` sob `pg_advisory_lock` do banco de estado (`OpsStore.db`, como `admin.json`);
   `inbox.json`: cada mutação toma `inbox.json.lock` (flock/msvcrt, `hub/filelock.py`) e relê o que outra réplica
   gravou antes; arquivos temporários por PID.
7. **Âncora externa da auditoria.** `HUB_AUDIT_HEAD_FILE`: a cada evento uma linha JSONL com a cabeça assinada
   (seq, hash, id, assinatura, host, horário); `verify` bloqueia a cadeia se ela estiver atrás da âncora (rollback do
   diretório de dados). Tela Auditoria mostra "âncora externa" ou a falha. Produção: `docker-compose.prod.yml`
   monta `${HUB_AUDIT_HEAD_DIR:?}` em `/audit-head` (pasta fora do volume `app-hub-data`).
8. **i18n e 404.** Erros de `AdminError`/`OpsError` saem traduzidos (en/es) pelo catálogo (`hub/web/catalog.json`,
   18 mensagens novas); `/apps/<desconhecido>` renderiza "Aplicação não encontrada" dentro do shell (404).
9. **Product Owner.** Quem tem `ver_auditoria` sem `editar_configuracoes` vê o cartão "Uso da plataforma (30 dias)"
   (só leitura) no fim da Auditoria; `/admin/analytics` leva para `/admin/audit#uso`. Partial `_usage.html`.
10. **Imagens dos apps** reconstruídas pelo compose do projeto `app-hub` (sem o rótulo `hubpy` antigo).

## Validação

- `uv run hub-check`: VERDE (384 passed).
- Instância temporária (imagem do commit): ui-sweep `--quick` 0 achados (2 entradas novas na allowlist para os
  Markdown crus `/dev/docs/*.md` da F22); `@core` 70 passed, 3 falhas antigas e alheias (rota hostil desligada na
  cópia, título `app-hub-` do container, balão de stack ainda espera "Rust · Axum").

## Produção (2026-10-08)

- Backup antes: `C:/PROJETOS/PROD/backups/app-hub-20261008-t19/` (dumps dos dois Postgres, `app-hub-data.tgz`,
  `objstore-data.tgz`, cópia do `.prod.env`).
- Senhas novas (48 hex) no `.prod.env`, `ALTER USER hub` nos dois bancos, `HUB_AUDIT_HEAD_DIR=C:/PROJETOS/PROD/app-hub-audit-head`;
  `docker-compose.prod.yml`/`prod.env.example` copiados para o checkout de produção; imagem `app-hub-web` deste
  commit (anterior em `app-hub-web:pre-t19`); `up -d --no-build` de bancos, objstore, hub e apps.
- Hub: "senhas de infraestrutura atualizadas em 4 variáveis". Senha antiga recusada no Postgres e no MinIO; `mc`
  do hub com a nova OK (usuários do MinIO preservados). Staging removido e recriado pela supervisão (4/4 healthy).
- `/healthz` 200, `/login` 200, `/dev/cli/simple/` 200; apps healthy; âncora `head.jsonl` sendo escrita.

## Lacunas

- Telemetria (métricas/SLOs) continua por réplica (em memória); agregação no banco de estado não foi feita.
- Rotação futura (senha não-dev → outra) ainda é manual nas variáveis do cofre (o boot só troca as de dev).
- `HUB_AUDIT_HEAD_URL` (webhook) não implementado no hub Python; só o arquivo.
- Backup completo não foi disparado depois da rotação (sem sessão de administrador); conferidos os caminhos:
  dump por `docker exec` (socket local, sem senha) e `mc` com a credencial nova.
- A rotação marcou "Reinício pendente" nos apps de produção (os containers já têm a senha nova pelo compose):
  Aplicar ou ignorar.
- Checkout de produção (`application-hub-py`) tem `docker-compose.prod.yml`/`prod.env.example` copiados: rodar
  `git checkout -- . && git pull` lá para alinhar com a `main`.
