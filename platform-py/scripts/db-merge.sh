#!/usr/bin/env bash
# Installs before the single Postgres had two servers: the hub's database in <project>-db and the apps' in
# <project>-app-db. This moves the `hub` database into <project>-app-db (the only server from now on):
#   1. stops the hub; 2. dumps `hub` from <project>-db to platform-py/.secrets/db-merge/ (0600);
#   3. restores it into <project>-app-db, which must not have tables in `hub` yet (never overwritten);
#   4. compares the row count of every table; 5. stops <project>-db and renames it <project>-db-merged-<date>.
# The old container and its volume stay for a rollback (rename it back and start it, with the previous compose).
# Then recreate the hub with the current compose (printed at the end).
#   bash platform-py/scripts/db-merge.sh [--project app-hub] [--yes]
set -euo pipefail
export MSYS_NO_PATHCONV=1
P=app-hub YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --project) P=$2; shift ;;
    -y | --yes) YES=1 ;;
    *) echo "opção desconhecida: $1" >&2; exit 2 ;;
  esac
  shift
done
OLD=$P-db NEW=$P-app-db WEB=$P-web
if ! docker inspect "$OLD" >/dev/null 2>&1; then echo "nada a fundir: $OLD não existe (o hub já usa um servidor só)"; exit 0; fi
if [ "$YES" = 0 ]; then
  [ -t 0 ] || { echo "sem terminal para confirmar: rode de novo com --yes" >&2; exit 1; }
  printf 'Parar o hub (%s) e mover o banco hub de %s para %s? (s/N): ' "$WEB" "$OLD" "$NEW"
  IFS= read -r a || a=n
  case "$a" in s | S | sim) ;; *) echo "cancelado: nada foi alterado" >&2; exit 1 ;; esac
fi
docker inspect "$NEW" >/dev/null 2>&1 || { echo "$NEW não existe: suba a pilha atual antes (o servidor dos apps)" >&2; exit 1; }
ready() { for _ in $(seq 60); do docker exec "$1" pg_isready -U hub -d hub >/dev/null 2>&1 && return 0; sleep 1; done; echo "$1 não respondeu" >&2; exit 1; }
q() { docker exec "$1" psql -U hub -d hub -v ON_ERROR_STOP=1 -tAc "$2"; }
docker start "$OLD" "$NEW" >/dev/null
ready "$OLD"
ready "$NEW"
USER_TABLES="FROM pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema')"
if [ "$(q "$NEW" "SELECT count(*) $USER_TABLES")" != 0 ]; then
  echo "o banco hub de $NEW já tem tabelas: nada foi alterado (confira se o hub já roda nele)" >&2; exit 1
fi
if docker inspect "$WEB" >/dev/null 2>&1; then echo "parando $WEB"; docker stop "$WEB" >/dev/null; fi
DIR=${HUB_MERGE_DIR:-$(cd "$(dirname "$0")/.." && pwd)/.secrets/db-merge}
mkdir -p "$DIR" && chmod 700 "$DIR"
DUMP=$DIR/hub-$(date +%Y%m%d-%H%M%S).dump
(umask 077; docker exec "$OLD" pg_dump -U hub -Fc hub > "$DUMP")
echo "cópia do banco hub em $DUMP"
docker exec -i "$NEW" pg_restore -U hub -d hub --exit-on-error < "$DUMP"
COUNTS="SELECT string_agg(format('SELECT %L, count(*) FROM %I.%I', schemaname || '.' || tablename, schemaname, tablename),
  ' UNION ALL ' ORDER BY schemaname, tablename) $USER_TABLES"
count_rows() { local sql; sql=$(q "$1" "$COUNTS"); [ -z "$sql" ] || q "$1" "$sql ORDER BY 1"; }
if [ "$(count_rows "$OLD")" != "$(count_rows "$NEW")" ]; then
  echo "contagem de linhas diferente entre $OLD e $NEW: $OLD continua como estava; o hub segue nele com: docker start $WEB" >&2
  exit 1
fi
MERGED=$OLD-merged-$(date +%Y%m%d)
docker stop "$OLD" >/dev/null && docker rename "$OLD" "$MERGED"
echo "banco hub movido para $NEW (contagem de linhas conferida); $OLD parado como $MERGED (rollback)"
echo "suba o hub no servidor único:"
echo "  cd platform-py && docker compose --env-file .prod.env -f docker-compose.yml -f docker-compose.apps.yml -f docker-compose.prod.yml up -d --no-deps app-hub-web"
