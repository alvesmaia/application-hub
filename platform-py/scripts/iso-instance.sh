#!/usr/bin/env bash
# Instância ISOLADA do hub para a validação completa (E2E com restauração/importação, blue/green, OAuth): estado
# vazio, banco de estado, banco dos apps, object store e os 4 apps PRÓPRIOS numa rede própria. Os contêineres usam
# o prefixo `app-hub-iso-*` e entram na rede com os apelidos da pilha (`app-hub-db`, `app-hub-app-db`,
# `app-hub-objstore`, `app-hub-app-<id>`, `app-hub-web`), então `apps.json`/`app-env.json` valem sem mudança — nada
# fala com produção. Senhas de dev (hubpy-*), usuários demo, rota hostil do E2E ligada, HUB_PUBLIC_URL = esta porta.
#   bash scripts/iso-instance.sh up      # http://127.0.0.1:8093 (senha demo em .iso-instance.env)
#   bash scripts/iso-instance.sh down    # remove contêineres, rede, volumes e imagens app-hub-iso-*
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
P=app-hub-iso
NET=$P-net
PORT=${ISO_HUB_PORT:-8093}
ENVF=.iso-instance.env
IMG=$P-web
APPS=(ativos fornecedores servicos coletor)
tok() { grep -A3 "\"HUB_INTERNAL_TOKEN_$(echo "$1" | tr a-z A-Z)\"" app-env.json | sed -n 's/.*"value": "\(.*\)".*/\1/p'; }

case "${1:-}" in
up)
  "$0" down >/dev/null 2>&1 || true
  docker build -q -f Dockerfile -t "$IMG" .. >/dev/null
  # imagens dos apps deste checkout (app-hub-iso-<id>): nunca reetiqueta as app-hub-<id> de produção
  for a in "${APPS[@]}"; do
    df=demo-apps-py/$a/Dockerfile
    [ "$a" = coletor ] && df=Dockerfile.coletor
    docker build -q -f "../platform/$df" -t "$P-$a" ../platform >/dev/null
  done
  docker network create --label hub.py=1 "$NET" >/dev/null
  for v in data db appdb obj; do docker volume create --label hub.py=1 "$P-$v" >/dev/null; done
  PW=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20)A1
  (umask 077; printf 'HUB_DEMO_PASSWORD=%s\nHUB=http://127.0.0.1:%s\nHUB_PUBLIC_URL=http://127.0.0.1:%s\n' "$PW" "$PORT" "$PORT" > "$ENVF")
  run() { docker run -d --label hub.py=1 --network "$NET" "$@" >/dev/null; }
  run --name $P-db --network-alias app-hub-db -v $P-db:/var/lib/postgresql/data \
    -e POSTGRES_USER=hub -e POSTGRES_PASSWORD=hubpy-dev -e POSTGRES_DB=hub postgres:16-alpine
  run --name $P-appdb --network-alias app-hub-app-db -v $P-appdb:/var/lib/postgresql/data -m 1g \
    -e POSTGRES_USER=hub -e POSTGRES_PASSWORD=hubpy-app-dev -e POSTGRES_DB=hub postgres:16-alpine
  run --name $P-objstore --network-alias app-hub-objstore -v $P-obj:/data -e MINIO_ROOT_USER=hubadmin \
    -e MINIO_ROOT_PASSWORD=hubpy-objstore-dev -e MINIO_BROWSER=off app-hub-minio:0.10.0 server /data
  # um banco por app (= infra/app-db-init.sh, sem montar o arquivo: CRLF num checkout Windows quebra o shebang)
  until docker exec $P-db pg_isready -U hub >/dev/null 2>&1 && docker exec $P-appdb psql -U hub -c 'select 1' >/dev/null 2>&1; do sleep 1; done
  for a in "${APPS[@]}"; do docker exec $P-appdb createdb -U hub "$a"; done
  # bucket dos anexos do fornecedores (em produção já existe no volume do objstore)
  for _ in $(seq 30); do
    docker run --rm --network "$NET" --entrypoint sh app-hub-mc:0.10.0 -c \
      'mc alias set r http://app-hub-objstore:9000 hubadmin hubpy-objstore-dev >/dev/null && mc mb -p r/fornecedores >/dev/null' && break
    sleep 1
  done
  hubtok=()
  for a in "${APPS[@]}"; do
    t=$(tok "$a")
    hubtok+=(-e "HUB_INTERNAL_TOKEN_$(echo "$a" | tr a-z A-Z)=$t")
    extra=()
    [ "$a" = fornecedores ] && extra=(-e HUB_S3_ENDPOINT_FORNECEDORES=http://app-hub-objstore:9000
      -e HUB_S3_BUCKET_FORNECEDORES=fornecedores -e HUB_S3_ACCESS_KEY_FORNECEDORES=hubadmin
      -e HUB_S3_SECRET_KEY_FORNECEDORES=hubpy-objstore-dev)
    [ "$a" = coletor ] && extra=(-e COLETOR_HUB_URL=http://app-hub-web:8090)
    run --name $P-app-$a --network-alias app-hub-app-$a -m 1g --restart unless-stopped \
      -e HUB_DB_URL=postgresql://hub:hubpy-app-dev@app-hub-app-db:5432/$a -e HUB_DEMO_E2E_HOSTILE=1 \
      -e HUB_INTERNAL_TOKEN="$t" "${extra[@]}" $P-$a
  done
  run --name $P-web --network-alias app-hub-web -p "127.0.0.1:$PORT:8090" -m 1g \
    -v $P-data:/data -v "$PWD/apps.json":/config/apps.json:ro -v "$PWD/app-env.json":/config/app-env.json:ro \
    -v /var/run/docker.sock:/var/run/docker.sock --group-add 0 \
    -e HUB_PLATFORM_DIR=/platform -e HUB_APPS_FILE=/config/apps.json -e HUB_DATA_DIR=/data \
    -e HUB_APP_ENV_FILE=/config/app-env.json \
    -e HUB_DB_URL=postgresql://hub:hubpy-dev@app-hub-db:5432/hub -e HUB_STATE_DB_URL=postgresql://hub:hubpy-dev@app-hub-db:5432/hub \
    -e HUB_CONTAINER_SOCKET=/var/run/docker.sock -e HUB_CONTAINER_PREFIX=$P-app- -e HUB_APP_NETWORK="$NET" \
    -e HUB_BACKUP_DB_CONTAINER=$P-appdb -e HUB_DB_HOST=app-hub-app-db:5432 -e HUB_BACKUP_SCHEDULER=0 \
    -e HUB_S3_ADMIN_URL=http://hubadmin:hubpy-objstore-dev@app-hub-objstore:9000 \
    -e HUB_BROKER_ADDR=0.0.0.0:8081 -e HUB_BROKER_PUBLIC_URL=http://app-hub-web:8081 \
    -e HUB_PUBLIC_URL="http://127.0.0.1:$PORT" -e HUB_ADMIN_ALLOWED_CIDRS=0.0.0.0/0,::/0 \
    -e HUB_DEMO_USERS=1 -e HUB_DEMO_PASSWORD="$PW" "${hubtok[@]}" \
    "$IMG" uvicorn hub.main:app --host 0.0.0.0 --port 8090
  for _ in $(seq 120); do curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null && break; sleep 1; done
  curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null || { docker logs --tail 30 $P-web; exit 1; }
  echo "instância isolada em http://127.0.0.1:$PORT (senha demo em $ENVF)"
  ;;
down)
  for c in $(docker ps -aq --filter "name=^$P-"); do docker rm -f "$c" >/dev/null; done
  docker network rm "$NET" >/dev/null 2>&1 || true
  for v in data db appdb obj; do docker volume rm "$P-$v" >/dev/null 2>&1 || true; done
  for v in $(docker volume ls -q --filter "name=^$P-"); do docker volume rm "$v" >/dev/null 2>&1 || true; done
  for i in $(docker images --format '{{.Repository}}:{{.Tag}}' | grep "^$P-app-"); do docker rmi "$i" >/dev/null 2>&1 || true; done
  rm -f "$ENVF"
  echo "instância isolada removida"
  ;;
run) # bash scripts/iso-instance.sh run npx playwright test …  (em platform/tests-e2e, com o ambiente da instância)
  shift
  set -a
  . "./$ENVF"
  E2E_CONTAINER_PREFIX=$P- E2E_DATA_NETWORK=$NET E2E_OBJSTORE_PASS=hubpy-objstore-dev E2E_ATIVOS_STACK=python E2E_APP_DB=$P-appdb
  ZD_IMAGE_NAME=$P-ativos HUB_URL=$HUB HUB_PASSWORD=$HUB_DEMO_PASSWORD   # py-resilience.mjs e mcp-client
  HUB_BIN=${HUB_BIN:-$(command -v hub)} HUB_MC_IMAGE=${HUB_MC_IMAGE:-app-hub-mc:0.10.0}
  command -v cygpath >/dev/null && HUB_BIN=$(cygpath -m "$HUB_BIN")
  for a in "${APPS[@]}"; do
    eval "E2E_IMAGE_$(echo "$a" | tr a-z A-Z)=$P-$a@$(docker image inspect -f '{{.Id}}' "$P-$a")"
  done
  set +a
  cd ../platform/tests-e2e
  "$@"
  ;;
*) echo "uso: $0 up|down|run <comando>" >&2; exit 2 ;;
esac
