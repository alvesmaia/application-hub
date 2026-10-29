#!/usr/bin/env bash
# Starts this hub after a reboot on a host without systemd for the user (install.sh adds it to the crontab as
# @reboot): the hub's own Podman API socket, then the stack. Idempotent: running it again changes nothing.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
set -a; . ./.prod.env; set +a
# shellcheck source=install/net.sh
. scripts/install/net.sh
# shellcheck source=install/podman.sh
. scripts/install/podman.sh
# shellcheck source=install/db.sh
. scripts/install/db.sh
if [ -n "${HUB_ENGINE_STORE:-}" ]; then ENGINE_STORE=$HUB_ENGINE_STORE; engine_env "$ENGINE_STORE"; fi
start_own_socket "${HUB_CONTAINER_SOCKET_HOST:?HUB_CONTAINER_SOCKET_HOST ausente no .prod.env}"
export DOCKER_HOST="unix://$HUB_CONTAINER_SOCKET_HOST" DISABLE_HC_SYSTEMD=true
old_db_guard "${HUB_PROJECT:-app-hub}"
docker compose --env-file .prod.env -f docker-compose.yml -f docker-compose.apps.yml -f docker-compose.prod.yml up -d
