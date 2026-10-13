# Atualização de versão (hub Python)

Vale para instalações feitas por `platform-py/scripts/install.sh` ou pelo checklist de `docs/IMPLANTACAO.md`.
Leia antes o [`CHANGELOG.md`](../CHANGELOG.md) da versão nova (seção "Atenção" quando houver passo manual).
Comandos a partir de `platform-py/`; abrevie o compose assim:

```bash
dc() { docker compose --env-file .prod.env -f docker-compose.yml -f docker-compose.apps.yml -f docker-compose.prod.yml "$@"; }
curl -s http://127.0.0.1:${HUB_PORT:-8090}/healthz     # {"status":"ok","version":"<atual>"}
```

## Sem parada (recomendado)

A porta do hub é da borda (`<projeto>-edge`, Caddy), que só manda tráfego a quem responde ao `/healthz`. O
comando abaixo faz o backup, sobe uma cópia com a versão nova, troca o hub enquanto a cópia atende, observa por
5 min e, se a versão nova falhar, **volta sozinho** para a anterior:

```bash
git fetch --tags && git checkout v<versão>      # ou: git pull na main
python scripts/hub_update.py --build             # constrói do código atual
python scripts/hub_update.py --image <imagem>    # ou: imagem pronta (GHCR)
python scripts/hub_update.py --rollback          # voltar depois, à mão
```

Opções: `--project` (HUB_PROJECT, padrão `app-hub`), `--watch <s>` (observação, padrão 300), `--no-backup`,
`--backup-dir` (padrão `~/backup-hub/<data-hora>`). Conexões abertas (SSE, WebSocket) caem na troca e reconectam
sozinhas. A volta automática desfaz o código, não os dados: se a versão nova mudou o formato dos dados, restaure o
backup (§5). Por isso as migrações do hub são só aditivas (a versão anterior continua lendo os dados).

**Primeira vez com a borda** (instalações anteriores à 0.3.0): o `dc up -d` do §3 cria a borda, que assume a
`HUB_PORT` do hub (uma parada de ~10 s, só desta vez). Depois disso, use o `hub_update.py`.

Aviso de versão nova em Administração › Saúde: `HUB_UPDATE_CHECK=1` no `.prod.env`.

Os passos abaixo são o caminho manual (com parada curta).

## 1. Backup (sempre)

```bash
P=$(grep -E '^HUB_PROJECT=' .prod.env | cut -d= -f2); P=${P:-app-hub}
mkdir -p ~/backup-hub && cd ~/backup-hub
docker exec "$P-db"     pg_dumpall -U hub > hub-db.sql
docker exec "$P-app-db" pg_dumpall -U hub > app-db.sql
docker run --rm -v "$P-data:/d:ro" -v "$PWD:/b" alpine tar czf /b/hub-data.tgz -C /d .
docker run --rm -v "$P-objstore-data:/d:ro" -v "$PWD:/b" alpine tar czf /b/objstore.tgz -C /d .
cd -
```

Guarde também a chave mestra do cofre (`/data/master.key` ou `.secrets/master.key`) e o `.prod.env` — sem a
chave os segredos cifrados não abrem.

## 2. Código novo

```bash
git fetch --tags
git checkout v<versão>          # ou: git pull na main
diff <(grep -oE '^[A-Z_]+' prod.env.example | sort -u) <(grep -oE '^[A-Z_]+' .prod.env | sort -u)   # variáveis novas?
```

## 3. Reconstruir e subir

```bash
dc build                        # hub e apps do código-fonte (ou: -f docker-compose.ghcr.yml pull, HUB_VERSION=<versão>)
dc up -d                        # recria só o que mudou
curl -s http://127.0.0.1:${HUB_PORT:-8090}/healthz     # confira a versão nova
```

Só o hub mudou? `dc build app-hub-web && dc up -d --no-deps app-hub-web` (os apps seguem no ar).

## 4. Migrações

- **Banco de estado do hub** (`app-hub-db`): o hub cria/atualiza as próprias tabelas no boot (sem passo manual).
- **Dados em arquivo** (`/data`: usuários, cofre, administração): o formato é migrado no boot; versões antigas são
  lidas e regravadas no formato novo.
- **Bancos dos apps** (`app-hub-app-db`): cada app aplica as próprias migrações ao subir (`hub_sdk`).
- Passos manuais, quando existirem, ficam no CHANGELOG da versão.

## 5. Conferir e voltar atrás

Confira login, `/apps/<id>`, Administração › Saúde e os logs (`docker logs <projeto>-web`). Se algo der errado:

```bash
git checkout v<versão anterior> && dc build && dc up -d
# dados incompatíveis? restaure o backup do passo 1 (pare a pilha, restaure volumes e dumps, suba de novo)
```
