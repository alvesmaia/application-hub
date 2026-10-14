#!/usr/bin/env bash
# Generic example: public dev environment through YOUR own Cloudflare Tunnel (+ Access recommended).
#   TUNNEL_ID=<uuid of your tunnel> TUNNEL_HOSTNAME=hub-dev.yourcompany.com bash scripts/dev-tunnel.sh up|down|status
#
# "Standalone container" model: cloudflared runs OUTSIDE compose, in the container
# `apphub-devtunnel` (restart unless-stopped), and forwards to host.docker.internal:8080
# — i.e., to whatever serves port 8080 on the host (the normal stack OR the dev mode of
# development, platform-py/scripts/dev.sh). Starting/stopping/recreating the stack does not drop the tunnel.
#
# up:     copies the credentials to .tunnel/ (gitignored), generates .tunnel/config-standalone.yml
#         and creates the container if it does not exist (if it exists, it only ensures it is running —
#         never recreates a tunnel in use; use `down` + `up` to change the configuration).
# down:   removes the apphub-devtunnel container (the site starts answering 502/tunnel errors).
# status: container state + the last log lines.
# hub-web behind the tunnel needs HUB_PUBLIC_URL/HUB_TRUSTED_PROXIES from the dev mode
# (docker-compose.dev.yml); the normal stack still responds, but without the visitor's real IP.
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
      # Keeps the public Host (the hub sees $HOSTNAME_PUB).
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
