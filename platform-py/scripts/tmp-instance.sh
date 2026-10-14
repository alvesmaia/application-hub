#!/usr/bin/env bash
# TEMPORARY hub instance for E2E/ui-sweep: copy of the /data state, demo users enabled, NO Docker socket
# and NO state database (does not supervise or change the apps; the gateway only reads). Never production.
#   bash scripts/tmp-instance.sh up      # app-hub-web-tmp at http://127.0.0.1:8093
#   bash scripts/tmp-instance.sh down    # removes the temporary container and volume
# Then: cd ../platform/tests-e2e && set -a && . ../../platform-py/.tmp-instance.env && set +a \
#   && HUB=http://127.0.0.1:8093 node ui-sweep/run.mjs --quick
# Extra variables for the container: TMP_EXTRA_ENV="-e HUB_SSO_PROVIDER=entra -e ..." (e.g. SSO with tests/fake_entra.py).
set -euo pipefail
cd "$(dirname "$0")/.."
NAME=app-hub-web-tmp
VOL=app-hub-data-tmp
ENVF=.tmp-instance.env
PORT=${TMP_HUB_PORT:-8093}
SRC=${SRC_ENV_FILE:-.prod.env}

case "${1:-}" in
up)
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker volume rm "$VOL" >/dev/null 2>&1 || true
  docker volume create --label hub.py=1 "$VOL" >/dev/null
  MSYS_NO_PATHCONV=1 docker run --rm -v app-hub-data:/s:ro -v "$VOL":/d alpine sh -c 'cp -a /s/. /d/ && rm -f /d/primeiro-acesso.txt'
  PW=${HUB_DEMO_PASSWORD:-$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20)A1}
  (umask 077; printf 'HUB_DEMO_PASSWORD=%s\nHUB=http://127.0.0.1:%s\n' "$PW" "$PORT" > "$ENVF")
  # re-enables the demo accounts only in the copy
  MSYS_NO_PATHCONV=1 docker run --rm -v "$VOL":/data --entrypoint python "${TMP_IMAGE:-app-hub-web}" -c '
import json
p="/data/auth.json"; d=json.load(open(p))
for u in d["users"]:
    if u["email"].endswith("@demo.local"): u["active"]=True
json.dump(d, open(p,"w"), separators=(",",":"))'
  tokens=$(grep -E '^HUB_INTERNAL_TOKEN_' "$SRC" | sed 's/^/-e /' | tr '\n' ' ')
  # shellcheck disable=SC2086
  MSYS_NO_PATHCONV=1 docker run -d --name "$NAME" --label hub.py=1 --network app-hub_default -p "127.0.0.1:$PORT:8090" \
    -v "$VOL":/data -v "$PWD/apps.json":/config/apps.json:ro \
    -e HUB_PLATFORM_DIR=/platform -e HUB_APPS_FILE=/config/apps.json -e HUB_DATA_DIR=/data \
    -e HUB_DEMO_USERS=1 -e HUB_DEMO_PASSWORD="$PW" $tokens ${TMP_EXTRA_ENV:-} "${TMP_IMAGE:-app-hub-web}" \
    uvicorn hub.main:app --host 0.0.0.0 --port 8090 >/dev/null
  echo "instância temporária em http://127.0.0.1:$PORT (senha demo em $ENVF)"
  ;;
down)
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker volume rm "$VOL" >/dev/null 2>&1 || true
  rm -f "$ENVF"
  echo "instância temporária removida"
  ;;
*) echo "uso: $0 up|down" >&2; exit 2 ;;
esac
