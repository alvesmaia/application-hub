# shellcheck shell=bash
# The one Postgres (sourced by install.sh, hub-start.sh and dev.sh): the hub's `hub` database next to one database per
# app in <project>-app-db, each app with its own role and limits.

db_summary() {
  echo "  • Postgres: um servidor para o hub e os apps (cada app com papel e limites próprios)"
}

old_db_guard() { # old_db_guard <project>: an install from before the single Postgres still has the hub's data in <project>-db
  docker inspect "$1-db" >/dev/null 2>&1 || return 0
  echo "este hub ainda tem o banco em $1-db (instalação com dois servidores): mova-o antes para o servidor único com" >&2
  echo "  bash platform-py/scripts/db-merge.sh --project $1" >&2
  exit 1
}

secrets_topup() { # existing .prod.env: secrets added to the template in later versions are generated (never shown)
  local line k added=""
  while IFS= read -r line; do
    case "$line" in *'=<openssl rand -hex 24>'*) ;; *) continue ;; esac
    k=${line%%=*}
    grep -q "^$k=" "$ENVF" && continue
    printf '%s=%s\n' "$k" "$(rnd)" >> "$ENVF"
    added+=" $k"
  done < <(tr -d '\r' < prod.env.example)
  if [ -n "$added" ]; then echo "segredos novos em platform-py/$ENVF:$added" >&2; fi
}
