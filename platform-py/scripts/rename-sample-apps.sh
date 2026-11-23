#!/usr/bin/env bash
# 0.4.0: the sample apps' ids are in English (ativos→inventory, fornecedores→suppliers, servicos→servicedesk,
# coletor→collector, automacoes→automations). This renames what an existing install keeps under the old ids, without
# losing data. Run it AFTER `git checkout v0.4.0`, `install.sh --config-only --yes` and `dc build`, and BEFORE `dc up -d`:
#   1. dumps the apps' Postgres (pg_dumpall) to platform-py/.secrets/rename-apps/ (0600) and copies .prod.env there
#      (and the hub's /data JSON stores before step 6);
#   2. stops the hub and the old sample app containers;
#   3. Postgres: renames each database and role (passwords kept) and the job kinds inside it (`ativos.import`…);
#   4. MinIO: copies the old buckets to the new names (the old ones stay, for a rollback);
#   5. .prod.env: renames the per-app keys (APP_HUB_APP_DB_PASSWORD_<APP>, HUB_INTERNAL_TOKEN_<APP>, …), same values;
#   6. hub data (/data): `python -m hub.admin rename-apps` with the new hub image;
#   7. removes the old containers. Then `dc up -d` creates the new ones (printed at the end).
# Every step skips what is already done (safe to run again). `--reverse` goes back to the old ids.
#   bash platform-py/scripts/rename-sample-apps.sh [--project app-hub] [--yes] [--reverse]
set -euo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."
P=$(grep -E '^HUB_PROJECT=' .prod.env 2>/dev/null | cut -d= -f2 || true)
P=${P:-app-hub} YES=0 REV=0
while [ $# -gt 0 ]; do
  case "$1" in
    --project) P=$2; shift ;;
    -y | --yes) YES=1 ;;
    --reverse) REV=1 ;;
    *) echo "opção desconhecida: $1" >&2; exit 2 ;;
  esac
  shift
done
MAP="ativos:inventory fornecedores:suppliers servicos:servicedesk coletor:collector automacoes:automations"
pairs() { for m in $MAP; do if [ "$REV" = 1 ]; then echo "${m#*:} ${m%%:*}"; else echo "${m%%:*} ${m#*:}"; fi; done; }
DB=$P-app-db WEB=$P-web
docker inspect --type container "$DB" >/dev/null 2>&1 || { echo "$DB não existe: instale/suba a pilha antes" >&2; exit 1; }
if [ "$YES" = 0 ]; then
  [ -t 0 ] || { echo "sem terminal para confirmar: rode de novo com --yes" >&2; exit 1; }
  printf 'Parar o hub (%s) e os apps de exemplo e renomear os ids dos apps? (s/N): ' "$WEB"
  IFS= read -r a || a=n
  case "$a" in s | S | sim) ;; *) echo "cancelado: nada foi alterado" >&2; exit 1 ;; esac
fi

# 1. safety copies
DIR=${HUB_RENAME_DIR:-$PWD/.secrets/rename-apps}
mkdir -p "$DIR" && chmod 700 "$DIR"
TS=$(date +%Y%m%d-%H%M%S)
docker start "$DB" >/dev/null
for _ in $(seq 60); do docker exec "$DB" pg_isready -U hub >/dev/null 2>&1 && break; sleep 1; done
(umask 077; docker exec "$DB" pg_dumpall -U hub > "$DIR/app-db-$TS.sql"; cp .prod.env "$DIR/prod.env-$TS")
echo "cópias de segurança em $DIR (app-db-$TS.sql, prod.env-$TS)"

# 2. stop the hub and the old app containers (they are recreated with the new names by `dc up -d`)
old_containers() { pairs | while read -r o _; do for c in "$P-app-$o" "$P-app-${o}__staging"; do
  docker inspect --type container "$c" >/dev/null 2>&1 && echo "$c"; done; done; }
for c in $WEB $(old_containers); do docker stop "$c" >/dev/null 2>&1 && echo "parado: $c" || true; done

# 3. Postgres: database + role (demo apps: <id>; hub-provisioned: app_<id>[_staging]) and job kinds
q() { docker exec "$DB" psql -U hub -d "${2:-postgres}" -v ON_ERROR_STOP=1 -tAc "$1"; }
has_db() { [ "$(q "SELECT count(*) FROM pg_database WHERE datname='$1'")" = 1 ]; }
has_role() { [ "$(q "SELECT count(*) FROM pg_roles WHERE rolname='$1'")" = 1 ]; }
rename_db() { # rename_db <old> <new> <marker-app>
  if has_db "$1" && ! has_db "$2"; then
    q "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$1'" >/dev/null
    q "ALTER DATABASE \"$1\" RENAME TO \"$2\"" >/dev/null && echo "banco: $1 → $2"
  fi
  if has_role "$1" && ! has_role "$2"; then
    q "ALTER ROLE \"$1\" RENAME TO \"$2\"" >/dev/null && echo "papel: $1 → $2"
  fi
}
pairs | while read -r o n; do
  rename_db "$o" "$n"
  has_role "$n" && q "COMMENT ON ROLE \"$n\" IS 'hub-managed:demo=$n'" >/dev/null
  for env in "" _staging; do rename_db "app_$o$env" "app_$n$env"; done
  if has_db "$n" && [ "$(q "SELECT to_regclass('public.hub_jobs') IS NOT NULL" "$n")" = t ]; then
    k=$(q "UPDATE hub_jobs SET kind = '$n.' || substr(kind, length('$o.') + 1) WHERE kind LIKE '$o.%'" "$n")
    echo "tarefas de $n: ${k:-UPDATE 0}"
  fi
done
DOC=inventory.cadastro_documentado DOC_EN=inventory.documented_registration
if [ "$REV" = 1 ]; then DOC=ativos.documented_registration DOC_EN=ativos.cadastro_documentado; fi
for d in inventory ativos; do has_db "$d" && [ "$(q "SELECT to_regclass('public.hub_jobs') IS NOT NULL" "$d")" = t ] &&
  q "UPDATE hub_jobs SET kind = '$DOC_EN' WHERE kind = '$DOC'" "$d" >/dev/null; done

# 4. MinIO: copy the old buckets (sample attachments and hub-provisioned app-<id>/stg-<id>) to the new names
MC=$(grep -E '^HUB_MC_IMAGE=' .prod.env | cut -d= -f2); MC=${MC:-$P-mc:0.10.0}
OBJ=$P-objstore
if docker inspect --type container "$OBJ" >/dev/null 2>&1; then
  mc() { docker run --rm --network "container:$OBJ" -e MC_HOST_o="http://hubadmin:$(grep -E '^APP_HUB_OBJSTORE_PASSWORD=' .prod.env | cut -d= -f2)@127.0.0.1:9000" --entrypoint mc "$MC" "$@"; }
  pairs | while read -r o n; do for pre in "" app- stg-; do
    if mc ls "o/$pre$o" >/dev/null 2>&1 && ! mc ls "o/$pre$n" >/dev/null 2>&1; then
      mc mb -q "o/$pre$n" && mc mirror -q "o/$pre$o" "o/$pre$n" >/dev/null && echo "bucket: $pre$o → $pre$n (o antigo ficou)"
    fi
  done; done
fi

# 5. .prod.env keys
PY=$(command -v python3 || command -v python) # python3 on the servers; python on some hosts
"$PY" - "$REV" <<'PY'
import re, sys
from pathlib import Path
m = {"ATIVOS": "INVENTORY", "FORNECEDORES": "SUPPLIERS", "SERVICOS": "SERVICEDESK", "COLETOR": "COLLECTOR", "AUTOMACOES": "AUTOMATIONS"}
if sys.argv[1] == "1":
    m = {v: k for k, v in m.items()}
p = Path(".prod.env"); lines = p.read_text().splitlines(); keys = {l.split("=", 1)[0] for l in lines if "=" in l}
def new_key(k):
    for old, new in m.items():
        k = re.sub(rf"(?<=_){old}(?=_|$)", new, k)
    return k
# the old key's value wins (same secrets as before); `install.sh --config-only` may already have added the new key
old_vals = {new_key(k): l.split("=", 1)[1] for l in lines if "=" in l and not l.lstrip().startswith("#")
            for k in [l.split("=", 1)[0]] if new_key(k) != k}
out, moved = [], []
for l in lines:
    k = l.split("=", 1)[0] if "=" in l and not l.lstrip().startswith("#") else ""
    if k and new_key(k) != k:
        moved.append(f"{k} -> {new_key(k)}")
        if new_key(k) in keys:
            continue  # the new line below takes the old value
        l = new_key(k) + "=" + l.split("=", 1)[1]
    elif k in old_vals:
        l = k + "=" + old_vals[k]
    out.append(l)
p.write_text("\n".join(out) + "\n")
for x in moved:
    print(f".prod.env: {x}")
PY

# 6. hub data, with the new image (the code of this checkout). Values in the vault are sealed with their variable
#    name: the command reseals them under the new names, so it needs the same master key as the hub.
REVF=""; [ "$REV" = 1 ] && REVF=--reverse
MK=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$WEB" | grep '^HUB_MASTER_KEY_FILE=' || true)
(umask 077; docker run --rm --volumes-from "$WEB" --entrypoint sh "$P-web" -c 'cd /data && tar czf - *.json' > "$DIR/hub-data-$TS.tgz")
echo "cópia dos dados do hub em $DIR/hub-data-$TS.tgz"
docker run --rm --volumes-from "$WEB" -e HUB_DATA_DIR=/data ${MK:+-e "$MK"} --entrypoint python "$P-web" -m hub.admin rename-apps $REVF

# 7. old containers and networks out (the new ones come from `dc up -d`; an old network keeps the same address range
#    as its new one, so it must go first)
for c in $(old_containers); do docker rm -f "$c" >/dev/null && echo "removido: $c"; done
pairs | while read -r o _; do
  net=$P-app-$o
  docker network inspect "$net" >/dev/null 2>&1 || continue
  docker network disconnect -f "$net" "$WEB" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null && echo "rede removida: $net"
done
echo
echo "pronto. Agora suba a pilha com os ids novos:"
echo "  docker compose --env-file .prod.env -f docker-compose.yml -f docker-compose.apps.yml -f docker-compose.prod.yml up -d"
echo "Staging dos apps de exemplo: implante de novo pela tela Implantações (o contêiner de staging é recriado lá)."
