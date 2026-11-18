# shellcheck shell=bash
# Non-interactive options of install.sh that go to .prod.env, and what a rerun (existing .prod.env) leaves out.
GIVEN="" # instance options given on the command line (a rerun keeps .prod.env: they are only compared)

rerun_ignored() { # rerun_ignored <.prod.env>: warns about each instance option that differs from the file
  local opt key val cur warned=""
  for opt in $GIVEN; do
    case "$opt" in
      --port) key=HUB_PORT val=$PORT ;;
      --bind) key=HUB_BIND val=$BIND ;;
      --public-url) key=HUB_PUBLIC_URL val=$PUBLIC_URL ;;
      --project) key=HUB_PROJECT val=$PROJECT ;;
      --subnet) key=HUB_SUBNET val=$SUBNET ;;
      --no-demo-apps) key=COMPOSE_PROFILES val="" ;;
      *) continue ;;
    esac
    cur=$(sed -n "s/^$key=//p" "$1" | tail -1 | tr -d '\r')
    [ "$cur" != "$val" ] || continue
    echo "aviso: $opt${val:+ $val} ignorado (.prod.env existente tem $key=$cur)" >&2
    warned=1
  done
  [ -z "$warned" ] || echo "  para mudar: edite platform-py/.prod.env e rode de novo (os segredos ficam)" >&2
}
