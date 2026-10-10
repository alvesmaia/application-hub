#!/usr/bin/env bash
# Runtime de desenvolvimento do hub Python (projeto compose "app-hub", porta 8090).
#   dev.sh up                       modo dev: uvicorn --reload, assets do disco sem cache, URL do túnel (HUB_DEV_PUBLIC_URL)
#   dev.sh down                     volta à pilha normal (sem modo dev); `stop` derruba a pilha app-hub inteira
#   dev.sh status|logs
#   dev.sh reload-on|reload-off     recarga ao vivo do navegador (SSE /_dev/reload); vale no próximo dev-up (ou já, se em dev)
#   dev.sh obs-up|obs-down          perfil observability (OTel Collector + Jaeger :16696) com OTLP ligado no hub
# Toca só em app-hub-*; nunca no Rust (hub-*) nem no túnel.
set -euo pipefail
cd "$(dirname "$0")/.."
STATE=dist/dev-mode.env
c() { docker compose -p app-hub --profile demo-apps ${ENVF:+--env-file "$ENVF"} -f docker-compose.yml -f docker-compose.apps.yml "$@"; }
[ -f .dev.env ] && ENVF=.dev.env  # tokens internos dos apps = os do cofre do hub (HUB_INTERNAL_TOKEN_<APP>)
reload_flag() { [ -f "$STATE" ] && grep -q '^HUB_DEV_RELOAD=1' "$STATE" && echo 1 || echo 0; }
in_dev() { [ "$(docker inspect -f '{{index .Config.Labels "hub.dev"}}' app-hub-web 2>/dev/null || true)" = 1 ]; }
dev() { export HUB_DEV_RELOAD; HUB_DEV_RELOAD="$(reload_flag)"; c -f docker-compose.dev.yml "$@"; }
obs() { export APP_HUB_OTLP_ENDPOINT=http://app-hub-otel-collector:4318; c -f docker-compose.observability.yml --profile observability "$@"; }
wait_healthy() {
  local t0=$SECONDS
  until curl -fsS -o /dev/null http://127.0.0.1:8090/healthz 2>/dev/null; do
    [ $((SECONDS - t0)) -lt 120 ] || { echo "app-hub-web não respondeu em 120 s" >&2; docker logs --tail 30 app-hub-web >&2; return 1; }
    sleep 2
  done
}
setup_store() {
  docker exec app-hub-web mc alias set app-hub http://app-hub-objstore:9000 hubadmin "${APP_HUB_OBJSTORE_PASSWORD:-app-hub-objstore-dev}" >/dev/null
  docker exec app-hub-web mc mb --ignore-existing app-hub/fornecedores >/dev/null
}
case "${1:-}" in
  up) mkdir -p dist; [ -f "$STATE" ] || echo HUB_DEV_RELOAD=0 > "$STATE"
    dev up -d --build --force-recreate app-hub-web; setup_store; wait_healthy
    echo "modo dev no ar em http://localhost:8090 (recarga ao vivo: $([ "$(reload_flag)" = 1 ] && echo ligada || echo desligada); público: ${HUB_DEV_PUBLIC_URL:-http://localhost:8090})" ;;
  down) c up -d --build --force-recreate app-hub-web; setup_store; wait_healthy; echo "pilha normal no ar em http://localhost:8090" ;;
  stop) c down ;;
  reload-on|reload-off) mkdir -p dist; echo "HUB_DEV_RELOAD=$([ "$1" = reload-on ] && echo 1 || echo 0)" > "$STATE"
    if in_dev; then dev up -d --no-deps --force-recreate app-hub-web; setup_store; wait_healthy; echo "recarga ao vivo: $([ "$(reload_flag)" = 1 ] && echo ligada || echo desligada)"
    else echo "gravado ($1); vale no próximo 'dev.sh up'"; fi ;;
  obs-up) obs up -d --build; setup_store; wait_healthy; echo "observability no ar: Jaeger http://127.0.0.1:16696 (OTLP ligado no hub)" ;;
  obs-down) docker rm -f app-hub-otel-collector app-hub-jaeger >/dev/null 2>&1 || true; c up -d --no-deps --force-recreate app-hub-web; setup_store ;;
  status) if in_dev; then echo "modo: DESENVOLVIMENTO (recarga ao vivo: $([ "$(reload_flag)" = 1 ] && echo ligada || echo desligada))"; else echo "modo: normal"; fi
    c ps ;;
  logs) shift; c logs -f --tail=100 "$@" ;;
  *) echo "uso: dev.sh up|down|stop|status|logs|reload-on|reload-off|obs-up|obs-down" >&2; exit 2 ;;
esac
