#!/usr/bin/env bash
# Exemplo genérico: ambiente dev público por um Cloudflare Tunnel SEU (+ Access recomendado).
#   TUNNEL_ID=<uuid do seu túnel> TUNNEL_HOSTNAME=hub-dev.suaempresa.com bash scripts/dev-tunnel.sh up|down|status
#
# Modelo "container avulso": o cloudflared roda FORA do compose, no container
# `apphub-devtunnel` (restart unless-stopped), e encaminha para host.docker.internal:8080
# — ou seja, para o que estiver servindo a porta 8080 do host (pilha normal OU modo de
# desenvolvimento, platform-py/scripts/dev.sh). Subir/descer/recriar a pilha não derruba o túnel.
#
# up:     copia as credenciais para .tunnel/ (gitignored), gera .tunnel/config-standalone.yml
#         e cria o container se ele não existir (se já existe, só garante que está no ar —
#         nunca recria um túnel em uso; use `down` + `up` para trocar a configuração).
# down:   remove o container apphub-devtunnel (o site passa a responder 502/erro do túnel).
# status: estado do container + últimas linhas do log.
# O hub-web atrás do túnel precisa de HUB_PUBLIC_URL/HUB_TRUSTED_PROXIES do modo dev
# (docker-compose.dev.yml); a pilha normal responde, mas sem o IP real do visitante.
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
NAME=apphub-devtunnel
IMAGE="${TUNNEL_IMAGE:-cloudflare/cloudflared:2025.9.1}"
TUNNEL_ID="${TUNNEL_ID:-}"
HOSTNAME_PUB="${TUNNEL_HOSTNAME:-}"
if [ "${1:-status}" = up ] && { [ -z "$TUNNEL_ID" ] || [ -z "$HOSTNAME_PUB" ]; }; then
  echo "defina TUNNEL_ID e TUNNEL_HOSTNAME (o seu túnel Cloudflare)" >&2
  exit 2
fi
SRC="${TUNNEL_SOURCE_CREDENTIALS:-$HOME/.cloudflared/$TUNNEL_ID.json}"
hostpath() { (cd "$1" && (pwd -W 2>/dev/null || pwd)); }

case "${1:-status}" in
  up)
    if docker inspect "$NAME" >/dev/null 2>&1; then
      docker start "$NAME" >/dev/null
      echo "túnel já existe ($NAME) — no ar: https://$HOSTNAME_PUB"
      exit 0
    fi
    [ -f "$SRC" ] || [ -f .tunnel/credentials.json ] || { echo "credenciais do túnel não encontradas: $SRC" >&2; exit 1; }
    mkdir -p .tunnel
    [ -f "$SRC" ] && cp "$SRC" .tunnel/credentials.json
    chmod 600 .tunnel/credentials.json 2>/dev/null || true
    cat > .tunnel/config-standalone.yml <<CFG
tunnel: $TUNNEL_ID
credentials-file: /etc/cloudflared/credentials.json
no-autoupdate: true
ingress:
  - hostname: $HOSTNAME_PUB
    service: http://host.docker.internal:8080
    originRequest:
      # Mantém o Host público (o hub vê $HOSTNAME_PUB).
      httpHostHeader: $HOSTNAME_PUB
  - service: http_status:404
CFG
    dir="$(hostpath .tunnel)"
    docker run -d --name "$NAME" --restart unless-stopped --memory 64m \
      --add-host host.docker.internal:host-gateway \
      -v "$dir/config-standalone.yml:/etc/cloudflared/config.yml:ro" \
      -v "$dir/credentials.json:/etc/cloudflared/credentials.json:ro" \
      "$IMAGE" tunnel --no-autoupdate --config /etc/cloudflared/config.yml run >/dev/null
    echo "túnel no ar: https://$HOSTNAME_PUB → host.docker.internal:8080 (protegido pelo Cloudflare Access)"
    ;;
  down)
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    echo "túnel removido ($NAME)"
    ;;
  status)
    docker ps -a --filter "name=^${NAME}\$" --format '{{.Names}}\t{{.Status}}\t{{.Image}}'
    docker logs --tail 5 "$NAME" 2>&1 || true
    ;;
  *) echo "uso: $0 up|down|status" >&2; exit 2 ;;
esac
