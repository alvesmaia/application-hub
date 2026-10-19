#!/usr/bin/env bash
# Fresh install of the Application Hub on a machine that only has Docker (Compose >= 2.24), git and bash.
#
#   git clone https://github.com/alvesmaia/application-hub.git application-hub && cd application-hub
#   bash platform-py/scripts/install.sh --admin-email voce@empresa.com
#   # or, without cloning first:  bash install.sh --clone [--ref main] [--dir ./application-hub] ...
#
# What it does (idempotent — running it again only starts what is missing and never changes existing secrets):
#   1. generates platform-py/.prod.env (0600) from prod.env.example with new secrets (Postgres/MinIO passwords,
#      internal app tokens) and this instance's options (project, port, network range, sample apps);
#   2. builds from source the MinIO/mc images (infra/minio/Dockerfile), the hub and the apps;
#   3. creates the vault master key in the data volume and keeps a copy in platform-py/.secrets/master.key
#      (move it off the server);
#   4. creates the first administrator; the temporary password goes to platform-py/.secrets/primeiro-acesso.txt (0600);
#   5. starts the stack and waits for /healthz.
#
# Options:
#   --admin-email E      e-mail of the first administrator (default admin@example.com)
#   --public-url URL     origin seen by the browser (default http://localhost:<port>; production: https://…)
#   --port N             hub port on the host (default 8090)          --bind IP   (default 127.0.0.1)
#   --project NAME       compose project / container prefix (default app-hub; must start with app-hub)
#   --subnet A.B         app network range A.B.<n>.0/24 (default 10.231)
#   --no-demo-apps       does not start the 4 sample apps (default: they start, for the first evaluation)
#   --clone              clones the repository first (--repo URL, --ref REF, --dir FOLDER)
#   --get-compose        downloads the official docker-compose (pinned version, sha256 checked) to ~/.local/bin — for
#                        RHEL/Rocky without root, where the distribution does not ship a compose provider
#   --config-only        only writes platform-py/.prod.env (no Docker): to review the answers before installing
#
# Advanced options (interactive install, "Revisar opções avançadas?"): each one shows the RECOMMENDED value in
# [brackets] and Enter keeps it — MinIO data folder, audit anchor folder and webhook, off-site backup copy
# (none / SharePoint / S3), sign-in with Microsoft Entra ID, admin network restriction, proxy/tunnel in front,
# telemetry, new-version notice, public developer docs and build CPUs. Secrets are read without echo and go to the
# vault or to .prod.env (0600), never to the command line. Without a terminal, SharePoint can still be set with
# SP_TENANT, SP_CLIENT_ID, SP_SITE, SP_FOLDER (default Backups/hub) and SP_SECRET_FILE; everything else: edit .prod.env.
# INSTALL_TTY=<file> reads the answers from a file instead of the terminal (tests).
#   --yes                asks nothing (defaults + command-line options); in a terminal without instance options
#                        of the instance the script asks for each one, with the default in [brackets]
set -euo pipefail
export MSYS_NO_PATHCONV=1

EMAIL=admin@example.com PUBLIC_URL="" PORT=8090 BIND=127.0.0.1 PROJECT=app-hub SUBNET=10.231 DEMO=1
CLONE=0 REPO=https://github.com/alvesmaia/application-hub.git REF=main DIR=./application-hub
GET_COMPOSE=0 YES=0 ASKED=0 PUBLIC_URL_IN='' CONFIG_ONLY=0
TTY=${INSTALL_TTY:-/dev/tty}
exec 4>&2  # questions: the terminal (opened below) or, with INSTALL_TTY, stderr
COMPOSE_VER=v2.29.7
COMPOSE_SHA=383ce6698cd5d5bbf958d2c8489ed75094e34a77d340404d9f32c4ae9e12baf0
while [ $# -gt 0 ]; do
  case "$1" in
    --admin-email) EMAIL=$2; shift; ASKED=1 ;;
    --public-url) PUBLIC_URL=$2; shift; ASKED=1 ;;
    --port) PORT=$2; shift; ASKED=1 ;;
    --bind) BIND=$2; shift; ASKED=1 ;;
    --project) PROJECT=$2; shift; ASKED=1 ;;
    --subnet) SUBNET=$2; shift; ASKED=1 ;;
    --no-demo-apps) DEMO=0; ASKED=1 ;;
    --clone) CLONE=1 ;;
    --repo) REPO=$2; shift ;;
    --ref) REF=$2; shift ;;
    --dir) DIR=$2; shift ;;
    --get-compose) GET_COMPOSE=1 ;;
    --config-only) CONFIG_ONLY=1 ;;
    -y | --yes) YES=1 ;;
    -h | --help) sed -n '2,41p' "$0"; exit 0 ;;
    *) echo "opção desconhecida: $1" >&2; exit 2 ;;
  esac
  shift
done
ask() { # ask <variable> <question> [validator]; EOF or 3 errors = keeps the default
  local cur ans i=0
  eval "cur=\$$1"
  while [ "$i" -lt 3 ]; do
    i=$((i + 1))
    printf '%s [%s]: ' "$2" "$cur" >&4
    IFS= read -r ans <&3 || { eval "$1=\$cur"; echo >&4; return 0; }
    [ -z "$ans" ] && ans=$cur
    if [ -z "${3:-}" ] || echo "$ans" | grep -Eq "$3"; then eval "$1=\$ans"; return 0; fi
    echo "valor inválido" >&4
  done
  eval "$1=\$cur"
  echo "usando o padrão: $cur" >&4
}
secret() { # secret <variable> <question>: no echo (the answer never shows on the screen)
  printf '%s: ' "$2" >&4
  stty -echo < "$TTY" 2>/dev/null || true
  IFS= read -r "$1" <&3 || eval "$1=''"
  stty echo < "$TTY" 2>/dev/null || true
  echo >&4
}
yes_no() { case "$1" in s | S | y | Y) return 0 ;; *) return 1 ;; esac; }
ADV=n OBJ_DIR="" HEAD_DIR="" HEAD_URL="" HEAD_TOKEN="" OFF_KIND=nenhuma OFF_S3="" ENTRA=n
ENTRA_TENANT="" ENTRA_CLIENT="" ENTRA_DOMAINS="" ENTRA_ROLE=Colaborador ENTRA_LOCAL=admins ENTRA_SECRET=""
ADMIN_CIDRS="" PROXY="" PROXIES=172.16.0.0/12 TELEMETRY=s OTLP="" UPDATES=s PUBDOCS=s BUILD_CPUS=2
if [ "$YES" = 0 ] && [ "$ASKED" = 0 ] && { [ -n "${INSTALL_TTY:-}" ] || { [ -t 0 ] && [ -r /dev/tty ]; }; }; then
  exec 3< "$TTY"
  [ -n "${INSTALL_TTY:-}" ] || exec 4> /dev/tty
  echo "Instalação do Application Hub — o valor entre [colchetes] é o recomendado; Enter o mantém."
  ask EMAIL "E-mail do primeiro administrador" '^[^@ ]+@[^@ ]+\.[^@ ]+$'
  ask PORT "Porta do hub no host" '^[0-9]{2,5}$'
  ask BIND "Endereço de escuta (127.0.0.1 = só esta máquina)" '^[0-9.]+$|^::$'
  ask PUBLIC_URL_IN "URL vista pelo navegador (vazio = http://localhost:<porta>)" '^$|^https?://'
  [ -n "${PUBLIC_URL_IN:-}" ] && PUBLIC_URL=$PUBLIC_URL_IN
  ask PROJECT "Projeto/prefixo dos contêineres" '^app-hub(-[a-z0-9-]+)?$'
  ask SUBNET "Faixa das redes dos apps (A.B em A.B.<n>.0/24)" '^[0-9]+\.[0-9]+$'
  DEMO_IN=$([ "$DEMO" = 1 ] && echo s || echo n)
  ask DEMO_IN "Subir os 4 apps de exemplo? (s/n)" '^[snSN]$'
  case "$DEMO_IN" in s | S) DEMO=1 ;; *) DEMO=0 ;; esac
  echo
  ask ADV "Revisar opções avançadas? Enter mantém todas no recomendado (s/n)" '^[snSN]$'
  if yes_no "$ADV"; then
    echo "Opções avançadas — o valor entre [colchetes] é o recomendado; Enter o mantém."
    ask OBJ_DIR "Pasta do host para os arquivos (MinIO); vazio = volume Docker (recomendado)" '^$|^/.+'
    ask HEAD_DIR "Pasta da âncora da auditoria (outro disco/NFS); vazio = ./.secrets/audit-head" '^$|^/.+|^\./.+'
    ask HEAD_URL "Webhook da âncora da auditoria (https://…); vazio = sem webhook (recomendado)" '^$|^https://.+'
    [ -n "$HEAD_URL" ] && secret HEAD_TOKEN "  Token Bearer do webhook (vazio = nenhum)"
    ask OFF_KIND "Cópia externa dos backups: nenhuma | sharepoint | s3" '^(nenhuma|sharepoint|s3)$'
    case "$OFF_KIND" in
    sharepoint)
      SP_TENANT=${SP_TENANT:-} SP_CLIENT_ID=${SP_CLIENT_ID:-} SP_SITE=${SP_SITE:-} SP_FOLDER=${SP_FOLDER:-Backups/hub}
      ask SP_TENANT "  Tenant ID do Entra ID (GUID ou dominio.onmicrosoft.com)" '^[0-9a-fA-F-]{36}$|^[a-zA-Z0-9.-]+\.[a-z]+$'
      ask SP_CLIENT_ID "  Client ID do app registration (GUID)" '^[0-9a-fA-F-]{36}$'
      ask SP_SITE "  Site (https://<empresa>.sharepoint.com[/sites/<site>])" '^https://[a-zA-Z0-9.-]+\.sharepoint\.com(/.*)?$'
      ask SP_FOLDER "  Pasta dos backups no site" '^[^/].*$'
      secret SP_SECRET "  Segredo do cliente (não aparece na tela)"
      if [ -z "$SP_SECRET" ] || [ -z "$SP_TENANT" ] || [ -z "$SP_CLIENT_ID" ] || [ -z "$SP_SITE" ]; then
        echo "resposta vazia: SharePoint não será configurado (cadastre depois em Administração › Conexões)"; SP_TENANT=''
      fi
      ;;
    s3) secret OFF_S3 "  URL S3 com a chave (https://chave:segredo@host:porta/bucket[/prefixo]; não aparece na tela)" ;;
    esac
    ask ENTRA "Login com conta Microsoft (Entra ID)? (s/n)" '^[snSN]$'
    if yes_no "$ENTRA"; then
      echo "  Registre no app a Redirect URI: ${PUBLIC_URL:-http://localhost:$PORT}/auth/entra/callback"
      ask ENTRA_TENANT "  Tenant ID (GUID; organizations/common = várias empresas)" '^[0-9a-fA-F-]{36}$|^(organizations|common)$|^[a-zA-Z0-9.-]+\.[a-z]+$'
      ask ENTRA_CLIENT "  Client ID do app registration (GUID)" '^[0-9a-fA-F-]{36}$'
      ask ENTRA_DOMAINS "  Domínios de e-mail aceitos (vírgula; vazio = qualquer um do tenant)" '^$|^[a-zA-Z0-9.,-]+$'
      ask ENTRA_ROLE "  Papel de quem entra pela primeira vez" '^(Leitor|Colaborador|Operador|Administrador)$'
      ask ENTRA_LOCAL "  Login com e-mail e senha: admins (recomendado) | todos | nenhum" '^(admins|todos|nenhum)$'
      secret ENTRA_SECRET "  Segredo do cliente (não aparece na tela)"
      if [ -z "$ENTRA_SECRET" ] || [ -z "$ENTRA_TENANT" ] || [ -z "$ENTRA_CLIENT" ]; then
        echo "resposta vazia: login Microsoft não será configurado"; ENTRA=n
      fi
    fi
    ask ADMIN_CIDRS "Redes com acesso à Administração (CIDR, vírgula); vazio = sem restrição" '^$|^[0-9a-fA-F.:/,]+$'
    PROXY=$(case "${PUBLIC_URL:-}" in https://*) echo s ;; *) echo n ;; esac)
    ask PROXY "Há proxy/túnel com HTTPS na frente do hub? (s/n)" '^[snSN]$'
    yes_no "$PROXY" && ask PROXIES "  Rede do proxy confiável (CIDR)" '^[0-9a-fA-F.:/,]+$'
    ask TELEMETRY "Métricas e traces internos (s/n)" '^[snSN]$'
    yes_no "$TELEMETRY" && ask OTLP "  Coletor OTLP externo (http://host:4318); vazio = nenhum (recomendado)" '^$|^https?://.+'
    ask UPDATES "Avisar quando houver versão nova (s/n)" '^[snSN]$'
    ask PUBDOCS "Documentação de desenvolvedor aberta sem login (s/n)" '^[snSN]$'
    ask BUILD_CPUS "CPUs para construir apps enviados ao hub" '^[1-9][0-9]?$'
  fi
  echo
  echo "Resumo: admin=$EMAIL  porta=$PORT  bind=$BIND  url=${PUBLIC_URL:-http://localhost:$PORT}"
  echo "        projeto=$PROJECT  subnet=$SUBNET.<n>.0/24  apps de exemplo=$([ "$DEMO" = 1 ] && echo sim || echo não)"
  if yes_no "$ADV"; then
    echo "        arquivos=${OBJ_DIR:-volume Docker}  âncora=${HEAD_DIR:-./.secrets/audit-head}${HEAD_URL:+ + webhook}"
    echo "        cópia externa=$OFF_KIND${SP_TENANT:+ ($SP_SITE/$SP_FOLDER)}  login Microsoft=$(yes_no "$ENTRA" && echo sim || echo não)"
    echo "        Administração=${ADMIN_CIDRS:-sem restrição}  proxy=$PROXY  telemetria=$TELEMETRY  avisos=$UPDATES  docs públicas=$PUBDOCS"
  fi
  printf 'Instalar com estas opções? (s/n) [s]: ' >&4
  IFS= read -r a <&3 || a=s
  case "${a:-s}" in s | S) ;; *) echo "cancelado"; exit 1 ;; esac
fi
case "$PROJECT" in app-hub | app-hub-*) ;; *) echo "--project deve começar com app-hub (trava do hub)" >&2; exit 2 ;; esac
[ "$CONFIG_ONLY" = 1 ] || command -v docker >/dev/null || { echo "Docker não encontrado (Podman: instale podman-docker)" >&2; exit 1; }
get_compose() { # official binary in ~/.local/bin (no root); pinned version and sha256 checked
  local url=https://github.com/docker/compose/releases/download/$COMPOSE_VER/docker-compose-linux-x86_64
  local dest=$HOME/.local/bin/docker-compose tmp
  mkdir -p "$HOME/.local/bin"
  tmp=$(mktemp)
  echo "baixando docker-compose $COMPOSE_VER para $dest"
  curl -fsSL -o "$tmp" "$url" || { rm -f "$tmp"; echo "download falhou: $url" >&2; return 1; }
  if command -v sha256sum >/dev/null; then
    local got
    got=$(sha256sum "$tmp" | cut -d' ' -f1)
    [ "$got" = "$COMPOSE_SHA" ] || { rm -f "$tmp"; echo "sha256 do docker-compose não confere (esperado $COMPOSE_SHA, obtido $got)" >&2; return 1; }
  fi
  chmod +x "$tmp" && mv "$tmp" "$dest"
  case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH"; export PATH;
    echo "aviso: acrescente \$HOME/.local/bin ao PATH do seu shell (~/.bashrc)" >&2 ;; esac
}
if [ "$CONFIG_ONLY" = 0 ] && ! docker compose version >/dev/null 2>&1; then
  if [ "$GET_COMPOSE" = 0 ] && [ "$YES" = 0 ] && [ -t 0 ] && [ -r /dev/tty ]; then
    printf 'Docker Compose v2 não encontrado. Baixar o binário oficial %s em ~/.local/bin? (s/n) [s]: ' "$COMPOSE_VER" > /dev/tty
    IFS= read -r a < "$TTY" || a=s
    case "${a:-s}" in s | S) GET_COMPOSE=1 ;; esac
  fi
  if [ "$GET_COMPOSE" = 1 ]; then
    get_compose || exit 1
  else
    echo "Docker Compose v2 não encontrado. Sem root (ex.: RHEL com Podman): rode de novo com --get-compose," >&2
    echo "  ou baixe docker-compose-linux-x86_64 de github.com/docker/compose/releases para ~/.local/bin (chmod +x)." >&2
    exit 1
  fi
  docker compose version >/dev/null 2>&1 || { echo "o docker ainda não encontra o provedor de compose" >&2; exit 1; }
fi
# Podman rootless (RHEL/Rocky/Fedora): the socket belongs to the user, not /var/run/docker.sock
ROOTLESS_SOCK=""
if [ "$CONFIG_ONLY" = 0 ] && docker --version 2>/dev/null | grep -qi podman && [ "$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null)" = true ]; then
  ROOTLESS_SOCK="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/podman/podman.sock"
  [ -S "$ROOTLESS_SOCK" ] || { echo "Podman rootless sem socket: rode  systemctl --user enable --now podman.socket" >&2; exit 1; }
  loginctl show-user "$(id -un)" -p Linger 2>/dev/null | grep -q yes ||
    echo "aviso: sem linger o hub cai no logout — peça à TI: loginctl enable-linger $(id -un)" >&2
  grep -qw cpu "/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers" 2>/dev/null ||
    echo "aviso: controlador cpu não delegado ao usuário; os limites de CPU dos apps não serão aplicados (TI: Delegate=cpu cpuset io memory pids em user@.service, docs/IMPLANTACAO.md)" >&2
fi

if [ "$CLONE" = 1 ]; then
  [ -d "$DIR/.git" ] || git clone --depth 1 --branch "$REF" "$REPO" "$DIR"
  cd "$DIR/platform-py"
else
  cd "$(dirname "$0")/.."
fi
[ -f docker-compose.yml ] && [ -f prod.env.example ] || { echo "rode a partir de um clone do repositório" >&2; exit 1; }

: "${PUBLIC_URL:=http://localhost:$PORT}"
# Secure cookie only over HTTPS: over http:// (other than localhost) the browser drops it and the login loops
case "$PUBLIC_URL" in
  https://*) COOKIE=1 ;;
  http://localhost | http://localhost:* | http://127.0.0.1 | http://127.0.0.1:*) COOKIE=auto ;;
  *) COOKIE=auto
    echo "aviso: $PUBLIC_URL sem HTTPS — senha e sessão trafegam sem criptografia e o login Microsoft (Entra) exige HTTPS; use só para teste" >&2 ;;
esac
ENVF=.prod.env
rnd() { od -An -tx1 -N24 /dev/urandom | tr -d ' \n'; }
if [ ! -f "$ENVF" ]; then
  umask 077
  sed -e "s#^HUB_PUBLIC_URL=.*#HUB_PUBLIC_URL=$PUBLIC_URL#" -e "s#^HUB_AUDIT_HEAD_DIR=.*#HUB_AUDIT_HEAD_DIR=./.secrets/audit-head#" \
    -e "s#^HUB_COOKIE_SECURE=.*#HUB_COOKIE_SECURE=$COOKIE#" \
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
    [ "$BIND" != 127.0.0.1 ] && echo "HUB_BIND6=$BIND"
    echo "HUB_SUBNET=$SUBNET"
    echo "HUB_MINIO_IMAGE=$PROJECT-minio:0.10.0"
    echo "HUB_MC_IMAGE=$PROJECT-mc:0.10.0"
    if [ "$PORT" != 8090 ]; then # debug ports (127.0.0.1 only) outside the default instance's
      echo "HUB_APPDB_PORT=$((PORT + 10000))"
      echo "HUB_APP_PORT_ATIVOS=$((PORT + 1011))"
      echo "HUB_APP_PORT_FORNECEDORES=$((PORT + 1012))"
      echo "HUB_APP_PORT_SERVICOS=$((PORT + 1013))"
      echo "HUB_APP_PORT_COLETOR=$((PORT + 1015))"
    fi
    if [ "$DEMO" = 1 ]; then echo "COMPOSE_PROFILES=demo-apps"; else echo "HUB_APPS_JSON=./apps.empty.json"; fi
  } >> "$ENVF"
  if [ -n "$ROOTLESS_SOCK" ]; then # socket owner = the user = root inside the namespace
    { echo "HUB_CONTAINER_SOCKET_HOST=$ROOTLESS_SOCK"; echo "DOCKER_GID=0"; } >> "$ENVF"
  elif [ -n "${DOCKER_GID:-}" ]; then
    echo "DOCKER_GID=$DOCKER_GID" >> "$ENVF"
  elif [ "$(uname -s)" = Linux ]; then
    echo "DOCKER_GID=$(stat -c %g /var/run/docker.sock 2>/dev/null || echo 0)" >> "$ENVF"
  fi
  echo "segredos novos em platform-py/$ENVF (0600)"
else
  echo "usando platform-py/$ENVF existente (segredos mantidos)"
fi
setv() { # setv KEY VALUE: replaces or appends in .prod.env (VALUE may contain any character)
  local tmp
  tmp=$(mktemp)
  grep -v "^$1=" "$ENVF" > "$tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$tmp"
  cat "$tmp" > "$ENVF"
  rm -f "$tmp"
}
if yes_no "$ADV"; then
  [ -n "$OBJ_DIR" ] && { mkdir -p "$OBJ_DIR" 2>/dev/null || true; setv HUB_OBJSTORE_DIR "$OBJ_DIR"; }
  [ -n "$HEAD_DIR" ] && { mkdir -p "$HEAD_DIR" 2>/dev/null || true; setv HUB_AUDIT_HEAD_DIR "$HEAD_DIR"; }
  [ -n "$HEAD_URL" ] && setv HUB_AUDIT_HEAD_URL "$HEAD_URL"
  [ -n "$HEAD_TOKEN" ] && setv HUB_AUDIT_HEAD_TOKEN "$HEAD_TOKEN"
  [ -n "$OFF_S3" ] && setv HUB_BACKUP_OFFSITE_URL "$OFF_S3"
  if yes_no "$ENTRA"; then
    setv HUB_SSO_PROVIDER entra
    setv HUB_ENTRA_TENANT_ID "$ENTRA_TENANT"
    setv HUB_ENTRA_CLIENT_ID "$ENTRA_CLIENT"
    [ -n "$ENTRA_DOMAINS" ] && setv HUB_ENTRA_ALLOWED_DOMAINS "$ENTRA_DOMAINS"
    setv HUB_ENTRA_DEFAULT_ROLE "$ENTRA_ROLE"
    setv HUB_LOCAL_LOGIN "$(case "$ENTRA_LOCAL" in todos) echo 1 ;; nenhum) echo 0 ;; *) echo admins ;; esac)"
  fi
  [ -n "$ADMIN_CIDRS" ] && setv HUB_ADMIN_ALLOWED_CIDRS "$ADMIN_CIDRS"
  if yes_no "$PROXY"; then setv HUB_TRUST_PROXY_HEADERS 1; setv HUB_TRUSTED_PROXIES "$PROXIES"; fi
  yes_no "$TELEMETRY" || setv HUB_TELEMETRY 0
  [ -n "$OTLP" ] && setv OTEL_EXPORTER_OTLP_ENDPOINT "$OTLP"
  yes_no "$UPDATES" || setv HUB_UPDATE_CHECK 0
  yes_no "$PUBDOCS" || setv HUB_PUBLIC_DOCS 0
  [ "$BUILD_CPUS" != 2 ] && setv HUB_BUILD_CPUS "$BUILD_CPUS"
  HEAD_TOKEN='' OFF_S3=''
fi
if [ "$CONFIG_ONLY" = 1 ]; then
  echo "platform-py/$ENVF pronto (--config-only: nada foi instalado)"
  exit 0
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

# SharePoint for the off-site copy of the backups (optional): interactive answers or SP_* + SP_SECRET_FILE
if [ -z "${SP_SECRET:-}" ] && [ -n "${SP_SECRET_FILE:-}" ] && [ -s "$SP_SECRET_FILE" ]; then
  IFS= read -r SP_SECRET < "$SP_SECRET_FILE"
fi
if [ -n "${SP_TENANT:-}" ] && [ -n "${SP_CLIENT_ID:-}" ] && [ -n "${SP_SITE:-}" ] && [ -n "${SP_SECRET:-}" ]; then
  echo "4b/5 conexão SharePoint para a cópia externa dos backups"
  printf '%s\n' "$SP_SECRET" | dc run --rm --no-deps -T app-hub-web python -m hub.admin add-sharepoint \
    --name backups-sharepoint --tenant "$SP_TENANT" --client-id "$SP_CLIENT_ID" --site "$SP_SITE"
  SP_SECRET=''
  setv HUB_BACKUP_OFFSITE_URL "sharepoint://backups-sharepoint/${SP_FOLDER:-Backups/hub}"
fi

if [ -n "${ENTRA_SECRET:-}" ]; then
  echo "4c/5 segredo do login Microsoft no cofre"
  printf '%s\n' "$ENTRA_SECRET" | dc run --rm --no-deps -T app-hub-web python -m hub.admin set-entra-secret >/dev/null
  ENTRA_SECRET=''
fi

echo "5/5 subindo a pilha ($P)"
dc up -d
# bucket for the attachments of the sample app fornecedores
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
