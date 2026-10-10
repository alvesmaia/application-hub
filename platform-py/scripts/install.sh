#!/usr/bin/env bash
# Instalação do zero do Application Hub numa máquina que só tem Docker (Compose >= 2.24), git e bash.
#
#   git clone https://github.com/alvesmaia/application-hub.git && cd application-hub
#   bash platform-py/scripts/install.sh --admin-email voce@empresa.com
#   # ou, sem clonar antes:  bash install.sh --clone [--ref main] [--dir ./application-hub] ...
#
# O que faz (idempotente — rodar de novo só sobe o que falta e nunca troca segredos existentes):
#   1. gera platform-py/.prod.env (0600) a partir de prod.env.example com segredos novos (senhas do Postgres/MinIO,
#      tokens internos dos apps) e as opções desta instância (projeto, porta, faixa de rede, apps de exemplo);
#   2. compila do código-fonte as imagens do MinIO/mc (infra/minio/Dockerfile), do hub e dos apps;
#   3. cria a chave mestra do cofre no volume de dados e guarda uma cópia em platform-py/.secrets/master.key
#      (mova-a para fora do servidor);
#   4. cria o primeiro administrador; a senha temporária vai para platform-py/.secrets/primeiro-acesso.txt (0600);
#   5. sobe a pilha e espera o /healthz.
#
# Opções:
#   --admin-email E      e-mail do primeiro administrador (padrão admin@example.com)
#   --public-url URL     origem vista pelo navegador (padrão http://localhost:<porta>; produção: https://…)
#   --port N             porta do hub no host (padrão 8090)          --bind IP   (padrão 127.0.0.1)
#   --project NOME       projeto compose / prefixo dos contêineres (padrão app-hub; deve começar com app-hub)
#   --subnet A.B         faixa das redes dos apps A.B.<n>.0/24 (padrão 10.231)
#   --no-demo-apps       não sobe os 4 apps de exemplo (padrão: sobem, para a primeira avaliação)
#   --clone              clona o repositório antes (--repo URL, --ref REF, --dir PASTA)
set -euo pipefail
export MSYS_NO_PATHCONV=1

EMAIL=admin@example.com PUBLIC_URL="" PORT=8090 BIND=127.0.0.1 PROJECT=app-hub SUBNET=10.231 DEMO=1
CLONE=0 REPO=https://github.com/alvesmaia/application-hub.git REF=main DIR=./application-hub
while [ $# -gt 0 ]; do
  case "$1" in
    --admin-email) EMAIL=$2; shift ;;
    --public-url) PUBLIC_URL=$2; shift ;;
    --port) PORT=$2; shift ;;
    --bind) BIND=$2; shift ;;
    --project) PROJECT=$2; shift ;;
    --subnet) SUBNET=$2; shift ;;
    --no-demo-apps) DEMO=0 ;;
    --clone) CLONE=1 ;;
    --repo) REPO=$2; shift ;;
    --ref) REF=$2; shift ;;
    --dir) DIR=$2; shift ;;
    -h | --help) sed -n '2,27p' "$0"; exit 0 ;;
    *) echo "opção desconhecida: $1" >&2; exit 2 ;;
  esac
  shift
done
case "$PROJECT" in app-hub | app-hub-*) ;; *) echo "--project deve começar com app-hub (trava do hub)" >&2; exit 2 ;; esac
command -v docker >/dev/null || { echo "Docker não encontrado" >&2; exit 1; }
docker compose version >/dev/null || { echo "Docker Compose v2 não encontrado" >&2; exit 1; }

if [ "$CLONE" = 1 ]; then
  [ -d "$DIR/.git" ] || git clone --depth 1 --branch "$REF" "$REPO" "$DIR"
  cd "$DIR/platform-py"
else
  cd "$(dirname "$0")/.."
fi
[ -f docker-compose.yml ] && [ -f prod.env.example ] || { echo "rode a partir de um clone do repositório" >&2; exit 1; }

: "${PUBLIC_URL:=http://localhost:$PORT}"
ENVF=.prod.env
rnd() { od -An -tx1 -N24 /dev/urandom | tr -d ' \n'; }
if [ ! -f "$ENVF" ]; then
  umask 077
  sed -e "s#^HUB_PUBLIC_URL=.*#HUB_PUBLIC_URL=$PUBLIC_URL#" -e "s#^HUB_AUDIT_HEAD_DIR=.*#HUB_AUDIT_HEAD_DIR=./.secrets/audit-head#" \
    prod.env.example | while IFS= read -r line; do
      case "$line" in
        *'=<openssl rand -hex 24>'*) printf '%s=%s\n' "${line%%=*}" "$(rnd)" ;;
        *) printf '%s\n' "$line" ;;
      esac
    done > "$ENVF"
  {
    echo
    echo "# --- Instância (gerado por scripts/install.sh) ---"
    echo "HUB_PROJECT=$PROJECT"
    echo "HUB_PORT=$PORT"
    echo "HUB_BIND=$BIND"
    echo "HUB_SUBNET=$SUBNET"
    echo "HUB_MINIO_IMAGE=$PROJECT-minio:0.10.0"
    echo "HUB_MC_IMAGE=$PROJECT-mc:0.10.0"
    if [ "$PORT" != 8090 ]; then # portas de depuração (só 127.0.0.1) fora das da instância padrão
      echo "HUB_APPDB_PORT=$((PORT + 10000))"
      echo "HUB_APP_PORT_ATIVOS=$((PORT + 1011))"
      echo "HUB_APP_PORT_FORNECEDORES=$((PORT + 1012))"
      echo "HUB_APP_PORT_SERVICOS=$((PORT + 1013))"
      echo "HUB_APP_PORT_COLETOR=$((PORT + 1015))"
    fi
    if [ "$DEMO" = 1 ]; then echo "COMPOSE_PROFILES=demo-apps"; else echo "HUB_APPS_JSON=./apps.empty.json"; fi
  } >> "$ENVF"
  [ -n "${DOCKER_GID:-}" ] && echo "DOCKER_GID=$DOCKER_GID" >> "$ENVF"
  [ "$(uname -s)" = Linux ] && [ -z "${DOCKER_GID:-}" ] && echo "DOCKER_GID=$(stat -c %g /var/run/docker.sock)" >> "$ENVF"
  echo "segredos novos em platform-py/$ENVF (0600)"
else
  echo "usando platform-py/$ENVF existente (segredos mantidos)"
fi
umask 077
mkdir -p .secrets/audit-head
# shellcheck disable=SC1090
set -a; . "./$ENVF"; set +a
P=$HUB_PROJECT
dc() { docker compose --env-file "$ENVF" -f docker-compose.yml -f docker-compose.apps.yml -f docker-compose.prod.yml "$@"; }

echo "1/5 imagens MinIO/mc (do código-fonte; a primeira vez leva alguns minutos)"
docker image inspect "$HUB_MC_IMAGE" >/dev/null 2>&1 || docker build -q --target mc -t "$HUB_MC_IMAGE" infra/minio >/dev/null
echo "2/5 imagens do hub e dos apps"
dc build

echo "3/5 chave mestra do cofre"
if [ ! -s .secrets/master.key ]; then
  dc run --rm --no-deps -T --entrypoint sh app-hub-web -c \
    'umask 077; [ -s /data/master.key ] || python -c "import secrets; print(secrets.token_hex(32))" > /data/master.key; cat /data/master.key' \
    > .secrets/master.key
fi
chmod 600 .secrets/master.key 2>/dev/null || true

echo "4/5 primeiro administrador ($EMAIL)"
if [ ! -s .secrets/primeiro-acesso.txt ]; then
  dc run --rm --no-deps -T app-hub-web python -m hub.admin create-admin --email "$EMAIL" --out /data/primeiro-acesso.txt >/dev/null
  dc run --rm --no-deps -T --entrypoint sh app-hub-web -c 'cat /data/primeiro-acesso.txt && rm -f /data/primeiro-acesso.txt' \
    > .secrets/primeiro-acesso.txt
fi

echo "5/5 subindo a pilha ($P)"
dc up -d
# bucket dos anexos do app de exemplo fornecedores
if [ "${COMPOSE_PROFILES:-}" = demo-apps ]; then
  for _ in $(seq 60); do
    docker run --rm --network "${P}_default" -e "PW=$APP_HUB_OBJSTORE_PASSWORD" --entrypoint sh "$HUB_MC_IMAGE" -c \
      'mc alias set r http://app-hub-objstore:9000 hubadmin "$PW" >/dev/null && mc mb -p r/fornecedores >/dev/null' && break
    sleep 2
  done
fi
for _ in $(seq 120); do curl -sf "http://127.0.0.1:$HUB_PORT/healthz" >/dev/null && break; sleep 2; done
curl -sf "http://127.0.0.1:$HUB_PORT/healthz" || { docker logs --tail 40 "$P-web"; exit 1; }
echo
echo "Application Hub no ar: $HUB_PUBLIC_URL  (projeto $P, porta $HUB_PORT)"
echo "Senha temporária do primeiro acesso: platform-py/.secrets/primeiro-acesso.txt (apague depois de entrar)"
echo "Chave mestra do cofre: platform-py/.secrets/master.key — copie para fora do servidor."
