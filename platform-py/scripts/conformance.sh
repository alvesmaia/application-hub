#!/usr/bin/env bash
# F7a: app contract conformance suite against the Python stack (`app-hub-*`, compose `app-hub`): the same
# `platform/conformance/run.sh` with the `app-hub-*` names (never touches `hub-*`). Runs each app in a disposable container
# `app-hub-conformance-<id>` on the network `app-hub-app-<id>`, stops `app-hub-web` (or `${CONF_PREFIX}web`) during the suite (it checks the
# outbox) and tests graceful shutdown (SIGTERM → exit 0 in < 10 s).
#   bash platform-py/scripts/conformance.sh [app...]   (from the repository root; OUT=report folder)
set -euo pipefail
cd "$(dirname "$0")/../../platform"
export MSYS_NO_PATHCONV=1
# Isolated instance: CONF_PREFIX=app-hub-iso- CONF_NET=app-hub-iso-net CONF_DB_NET= (everything on a single network).
CP=${CONF_PREFIX:-app-hub-}
DBNET=${CONF_DB_NET-app-hub_default}
apps=("$@")
[ ${#apps[@]} -eq 0 ] && apps=(ativos fornecedores servicos coletor)
OUT="${OUT:-loadtest/results/$(date +%F)/conformance}"
mkdir -p "$OUT"
host_out="$(cd "$OUT" && (pwd -W 2>/dev/null || pwd))"

docker build -q -t app-hub-conformance conformance >/dev/null

wait_healthy() {
  for _ in $(seq 1 90); do
    s="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1" 2>/dev/null || true)"
    [ "$s" = "healthy" ] && return 0
    sleep 2
  done
  echo "conformance: $1 não ficou saudável" >&2
  return 1
}

hub_running="$(docker inspect -f '{{.State.Running}}' ${CP}web 2>/dev/null || echo false)"
restore() {
  if [ "$hub_running" = "true" ]; then
    docker start ${CP}web >/dev/null && wait_healthy ${CP}web || true
  fi
}
trap restore EXIT
[ "$hub_running" = "true" ] && docker stop -t 10 ${CP}web >/dev/null

rows=""
fail=0
for app in "${apps[@]}"; do
  c="${CP}app-$app"
  env="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c")"
  token="$(printf '%s\n' "$env" | sed -n 's/^HUB_INTERNAL_TOKEN=//p')"
  db="$(printf '%s\n' "$env" | sed -n 's/^HUB_DB_URL=//p')"
  name="app-hub-conformance-$app"
  docker rm -fv "$name" >/dev/null 2>&1 || true
  docker create --name "$name" --network "${CONF_NET:-app-hub-app-$app}" \
    --label hub.ci=1 \
    -e CONF_APP="$app" -e CONF_URL="http://$c:8080" -e CONF_TOKEN="$token" -e CONF_DB_URL="$db" \
    -v "$host_out:/out" --user 0 \
    app-hub-conformance --junitxml="/out/$app.xml" >/dev/null
  [ -n "$DBNET" ] && docker network connect "$DBNET" "$name"  # the apps' database (app-hub-app-db) is on the compose default network
  set +e
  docker start -a "$name" > "$OUT/$app.log" 2>&1
  code=$?
  set -e
  docker rm -fv "$name" >/dev/null 2>&1 || true
  summary="$(grep -E '^=+ .* in [0-9.]+s' "$OUT/$app.log" | tail -1 | sed 's/=//g; s/^ *//; s/ *$//')"
  # Graceful shutdown: SIGTERM, drain and exit 0 before SIGKILL (10 s).
  t0=$(date +%s%N)
  docker stop -t 10 "$c" >/dev/null
  t1=$(date +%s%N)
  exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$c")"
  secs="$(awk "BEGIN{printf \"%.1f\", ($t1-$t0)/1e9}")"
  docker start "$c" >/dev/null
  wait_healthy "$c"
  graceful="saída $exit_code em ${secs} s"
  ok_sd=1
  if [ "$exit_code" != "0" ]; then ok_sd=0; fi
  if [ "$code" = "0" ] && [ "$ok_sd" = "1" ]; then res="✅"; else res="❌"; fail=1; fi
  stack="$(sed -n 's/.*"stack": *"\([^"]*\)".*/\1/p' "conformance/profiles/$app.json")"
  rows+="| $app | $stack | $summary | $graceful | $res |"$'\n'
  echo "conformance: $app ($stack): $summary · SIGTERM: $graceful · $res"
done

cat > "$OUT/conformance.md" <<EOF
# Conformidade do contrato de app — $(date -Iseconds)

Suíte \`platform/conformance\` (pytest, caixa-preta por HTTP; ver \`platform/docs/CONTRATO-APP.md\`)
rodando dentro das redes internas de cada app do compose, com o ${CP}web parado (a suíte confere
o outbox) e o teste de desligamento gracioso (\`docker stop -t 10\` → saída 0).

| App | Stack | pytest | SIGTERM | Resultado |
|---|---|---|---|---|
$rows
Logs e JUnit por app nesta pasta.
EOF
echo "conformance: relatório em $OUT/conformance.md"
exit $fail
