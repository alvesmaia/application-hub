# F7-rename — `hubpy*` vira `app-hub*` + imagem selada (2026-10-07)

## O que mudou
- Projeto compose `app-hub`; contêineres `app-hub-web|db|app-db|objstore|graphsim`, `app-hub-app-<id>` (+ `__staging`); redes `app-hub_default` e `app-hub-app-<id>` (sub-redes fixas 10.231.N.0/24 = `HUB_BROKER_APP_CIDRS`); volumes `app-hub-data|db-data|app-db-data|objstore-data` (nome explícito); imagens `app-hub-web`, `app-hub-<app>`, `app-hub-mc:0.10.0`, `app-hub-minio:0.10.0`, `app-hub-graphsim`.
- Trava do runtime (`hub/ops/runtime.py`): só `app-hub-*`. `apphub-devtunnel`, `app-hub`, `app-hubx-1`, `hub-*` seguem proibidos (teste `test_guard_refuses_everything_outside_hubpy`).
- Variáveis `HUBPY_*` viraram `APP_HUB_*`. **Senhas padrão não mudaram** (`hubpy-dev`, `hubpy-app-dev`, `hubpy-objstore-dev` são literais de senha dos bancos já inicializados).
- Imagem selada: `platform-py/Dockerfile` (contexto = raiz do repo, `Dockerfile.dockerignore`) copia `hub/` e os assets/i18n de `platform/`; sem montagem de código e sem `--reload`. O compose de produção só monta `apps.json`/`app-env.json` (configuração) e o socket. `docker-compose.dev.yml` (via `dev.sh up`) adiciona as montagens de código e o `--reload`.
- MinIO/mc: `infra/minio/Dockerfile` (compilação do código-fonte) é a origem reproduzível; as imagens atuais vieram de retag do `hub-minio:0.10.0` e do binário `mc` copiado do contêiner em execução.
- Estado do hub migrado por `platform-py/scripts/migrate_rename.py` (admin.json com valores do cofre abertos/selados de novo, ops.json, ux/inbox/analytics/deployments). Cadeia de auditoria e histórico de jobs no banco não foram reescritos (texto histórico antigo).

## Migração (feita)
1. Backup em `C:/PROJETOS/PROD/backups/app-hub-20261007/` (tar do volume de dados com a chave mestra, tar do objstore, `pg_dumpall` dos dois Postgres).
2. Build das imagens, retag das imagens dos apps em execução (mantém as versões implantadas), pré-cópia dos volumes com rsync.
3. Corte: parar pilha antiga, rsync final (delta), `migrate_rename.py`, `compose up --no-build`, recriar `app-hub-app-ativos__staging`.

## Rollback
```
cd platform-py
docker compose -p app-hub --env-file .dev.env -f docker-compose.yml -f docker-compose.apps.yml down   # NÃO usar -v
git checkout <commit anterior ao F7-rename> -- . && docker compose -p hubpy --env-file .dev.env -f docker-compose.yml -f docker-compose.apps.yml up -d --no-build
```
Os volumes antigos `hubpy_hubpy-*` ficam até o dono apagar; se já removidos, restaurar do backup: `tar xzf` de `hubpy_hubpy-data.tgz` num volume novo, `psql` do `.sql.gz`. O túnel (`apphub-devtunnel`) não muda (porta 8090).
