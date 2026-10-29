# shellcheck shell=bash
# Postgres of the install (sourced by install.sh): single = one server for the hub and the apps (default; the `hub`
# database next to the apps', each app with its own role and limits) | split = one each (CPU and disk isolated too).
# Switching an installed hub means moving the data (dump and restore): docs/IMPLANTACAO.md.
DB_MODE=single DB_MODE_SET=0

db_question() {
  ask DB_MODE "Postgres: single = um servidor para o hub e os apps (recomendado) | split = um para cada (isola CPU e disco)" '^(single|split)$'
}

db_validate() { # an installed hub keeps its mode: switching needs the data moved
  case "$DB_MODE" in single | split) ;; *) echo "--db-mode: '$DB_MODE' inválido (single ou split)" >&2; exit 2 ;; esac
  local f=$LIB/../../.prod.env cur
  [ "$DB_MODE_SET" = 1 ] && [ -f "$f" ] || return 0
  cur=$(sed -n 's/^HUB_DB_MODE=//p' "$f" | tr -d '\r')
  if [ "${cur:-split}" != "$DB_MODE" ]; then
    echo "este hub já usa --db-mode ${cur:-split}: trocar exige mover os dados (dump e restore, docs/IMPLANTACAO.md)" >&2
    exit 2
  fi
}

db_summary() {
  if [ "$DB_MODE" = single ]; then echo "  • Postgres: um servidor para o hub e os apps (cada app com papel e limites próprios)"
  else echo "  • Postgres: dois servidores, um para o hub e um para os apps"; fi
}

db_write_new() { # new .prod.env only (an existing one keeps its mode): the hub connects to the apps' server as `hub`
  echo "HUB_DB_MODE=$DB_MODE"
  if [ "$DB_MODE" = single ]; then echo "HUB_CORE_DB_SCALE=0"; echo "HUB_CORE_DB_HOST=app-hub-app-db"; fi
}

db_share_password() { # single: one superuser, so the hub's password is the apps' server one
  [ "$DB_MODE" = single ] || return 0
  setv APP_HUB_DB_PASSWORD "$(sed -n 's/^APP_HUB_APP_DB_PASSWORD=//p' "$ENVF")"
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
