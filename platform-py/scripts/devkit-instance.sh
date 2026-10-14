#!/usr/bin/env bash
# TEMPORARY hub to prove `hub deploy` (F22): copy of the /data state, own database, own network and container prefix
# (`app-hub-devkit-app-*`) — never touches the apps or the production database. Docker socket mounted
# (builds and starts the deployed app). Demo users enabled only in the copy.
#   bash scripts/devkit-instance.sh up     # app-hub-devkit at http://127.0.0.1:8094 (demo password in .devkit-instance.env)
#   bash scripts/devkit-instance.sh down   # removes containers, network, volumes and images of the test app
set -euo pipefail
cd "$(dirname "$0")/.."
NAME=app-hub-devkit
DB=app-hub-devkit-db
NET=app-hub-devkit-net
VOL=app-hub-devkit-data
IMG=app-hub-devkit-web
ENVF=.devkit-instance.env
PORT=${DEVKIT_HUB_PORT:-8094}
export MSYS_NO_PATHCONV=1

case "${1:-}" in
up)
  "$0" down >/dev/null 2>&1 || true
  docker build -q -f Dockerfile -t "$IMG" .. >/dev/null
  docker network create --label hub.py=1 "$NET" >/dev/null
  docker volume create --label hub.py=1 "$VOL" >/dev/null
  docker run --rm -v app-hub-data:/s:ro -v "$VOL":/d alpine sh -c 'cp -a /s/. /d/ && rm -f /d/primeiro-acesso.txt /d/apps.dynamic.json'
  PW=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20)A1
  DBPW=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)
  (umask 077; printf 'HUB_DEMO_PASSWORD=%s\nHUB=http://127.0.0.1:%s\n' "$PW" "$PORT" > "$ENVF")
  docker run -d --name "$DB" --label hub.py=1 --network "$NET" -e POSTGRES_USER=hub -e POSTGRES_PASSWORD="$DBPW" \
    -e POSTGRES_DB=hub postgres:16-alpine >/dev/null
  until docker exec "$DB" pg_isready -U hub >/dev/null 2>&1; do sleep 1; done
  docker run --rm -v "$VOL":/data --entrypoint python "$IMG" -c '
import json
p="/data/auth.json"; d=json.load(open(p))
for u in d["users"]:
    if u["email"].endswith("@demo.local"): u["active"]=True
json.dump(d, open(p,"w"), separators=(",",":"))'
  echo '[]' > .devkit-apps.json
  docker run -d --name "$NAME" --label hub.py=1 --network "$NET" -p "127.0.0.1:$PORT:8090" \
    -v "$VOL":/data -v "$PWD/.devkit-apps.json":/config/apps.json:ro -v /var/run/docker.sock:/var/run/docker.sock \
    -e HUB_PLATFORM_DIR=/platform -e HUB_APPS_FILE=/config/apps.json -e HUB_DATA_DIR=/data \
    -e HUB_STATE_DB_URL="postgresql://hub:$DBPW@$DB:5432/hub" -e HUB_DB_URL="postgresql://hub:$DBPW@$DB:5432/hub" \
    -e HUB_CONTAINER_SOCKET=/var/run/docker.sock -e HUB_CONTAINER_PREFIX=app-hub-devkit-app- \
    -e HUB_APP_NETWORK="$NET" -e HUB_BACKUP_DB_CONTAINER="$DB" -e HUB_DB_HOST="$DB:5432" \
    -e HUB_BACKUP_SCHEDULER=0 -e HUB_BROKER_ADDR=off -e HUB_ADMIN_ALLOWED_CIDRS=0.0.0.0/0 \
    -e HUB_DEMO_USERS=1 -e HUB_DEMO_PASSWORD="$PW" "$IMG" uvicorn hub.main:app --host 0.0.0.0 --port 8090 >/dev/null
  until curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null; do sleep 1; done
  echo "hub temporário em http://127.0.0.1:$PORT (senha demo em $ENVF)"
  ;;
down)
  for c in $(docker ps -aq --filter "name=^app-hub-devkit"); do docker rm -f "$c" >/dev/null; done
  docker network rm "$NET" >/dev/null 2>&1 || true
  docker volume rm "$VOL" >/dev/null 2>&1 || true
  for i in $(docker images -q --filter "reference=app-hub-app-estoque*"); do docker rmi -f "$i" >/dev/null 2>&1 || true; done
  rm -f "$ENVF" .devkit-apps.json
  echo "hub temporário removido"
  ;;
*) echo "uso: $0 up|down" >&2; exit 2 ;;
esac
