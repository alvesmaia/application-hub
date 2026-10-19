# shellcheck shell=bash
# Corporate network for install.sh: proxy, own CA and internal mirrors. Sourced by install.sh (needs ask/setv/yes_no).
# The values go to .prod.env (HUB_PROXY, HUB_NO_PROXY, HUB_CA_FILE, HUB_PYPI_INDEX, HUB_IMAGE_PREFIX, HUB_APK_MIRROR,
# HUB_GOPROXY); the builds get them as build args, only for the build commands.
NET=n NET_PROXY="" NET_NOPROXY="" CA_FILE="" PYPI="" REG_MIRROR="" APK="" GOPX=""
BUILD_ARG_NAMES="HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy EXTRA_CA_PEM IMAGE_PREFIX APK_MIRROR GOPROXY"

mask_url() { # hides the password of a URL (proxy with user:password@): never on the screen or in the log
  printf '%s' "$1" | sed -E 's#(://)[^/@]*@#\1***@#'
}

net_questions() { # interactive: starts from the proxy already in the environment
  local envproxy=${HTTPS_PROXY:-${https_proxy:-${HTTP_PROXY:-${http_proxy:-}}}}
  [ -n "$envproxy" ] && NET=s
  ask NET "Rede corporativa com proxy, CA própria ou espelhos internos? (s/n)" '^[snSN]$'
  yes_no "$NET" || return 0
  NET_PROXY=$envproxy NET_NOPROXY=${NO_PROXY:-${no_proxy:-}}
  ask NET_PROXY "  Proxy HTTP(S) para baixar imagens e pacotes (vazio = nenhum)" '^$|^https?://'
  ask NET_NOPROXY "  Destinos sem proxy (vírgula; ex.: .empresa.local)" '^$|^[a-zA-Z0-9.,:/*_-]+$'
  ask CA_FILE "  Arquivo PEM da CA corporativa (vazio = nenhuma)" '^$|^[^ ]+$'
  ask PYPI "  Espelho PyPI (URL terminada em /simple; vazio = pypi.org)" '^$|^https?://'
  ask REG_MIRROR "  Espelho de imagens Docker Hub (host; vazio = docker.io)" '^$|^[a-zA-Z0-9.:/_-]+$'
  ask APK "  Espelho Alpine (URL; vazio = dl-cdn.alpinelinux.org)" '^$|^https?://'
  ask GOPX "  Espelho Go (GOPROXY; vazio = proxy.golang.org)" '^$|^https?://'
}

net_validate() { # exit 2 with the offending value and the expected shape
  case "$NET_PROXY" in "" | http://* | https://*) ;; *)
    echo "--proxy: '$(mask_url "$NET_PROXY")' inválido (esperado http://host:porta)" >&2; exit 2 ;; esac
  [ -z "$CA_FILE" ] && return 0
  [ -r "$CA_FILE" ] || { echo "--ca-file: $CA_FILE não existe ou não pode ser lido" >&2; exit 2; }
  grep -q -- '-----BEGIN CERTIFICATE-----' "$CA_FILE" ||
    { echo "--ca-file: $CA_FILE não é um certificado PEM (esperado -----BEGIN CERTIFICATE-----)" >&2; exit 2; }
  CA_FILE=$(cd "$(dirname "$CA_FILE")" && pwd)/$(basename "$CA_FILE")
}

net_summary() { # one line for the consent screen (proxy password hidden)
  [ -n "$NET_PROXY$CA_FILE$PYPI$REG_MIRROR$APK$GOPX" ] || return 0
  local proxy=nenhum
  [ -z "$NET_PROXY" ] || proxy=$(mask_url "$NET_PROXY")
  echo "  • rede corporativa: proxy $proxy; CA ${CA_FILE:-nenhuma}; imagens ${REG_MIRROR:-docker.io}; PyPI ${PYPI:-pypi.org}"
  [ -z "$CA_FILE" ] || echo "  • confiar na CA $CA_FILE nos builds e nos contêineres do hub e dos apps"
}

net_write() { # .prod.env (setv); the CA is copied next to the secrets so the install does not depend on the source path
  if [ -n "$NET_PROXY" ]; then setv HUB_PROXY "$NET_PROXY"; fi
  if [ -n "$NET_NOPROXY" ]; then setv HUB_NO_PROXY "$NET_NOPROXY"; fi
  if [ -n "$CA_FILE" ]; then
    mkdir -p .secrets/ca && cp "$CA_FILE" .secrets/ca/ca.pem
    setv HUB_CA_FILE ./.secrets/ca/ca.pem
    setv HUB_CA_DIR ./.secrets/ca # mounted at /config/ca in the hub (external calls trust it)
  fi
  if [ -n "$PYPI" ]; then setv HUB_PYPI_INDEX "$PYPI"; fi
  if [ -n "$REG_MIRROR" ]; then setv HUB_IMAGE_PREFIX "${REG_MIRROR%/}/library/"; fi
  if [ -n "$APK" ]; then setv HUB_APK_MIRROR "$APK"; fi
  if [ -n "$GOPX" ]; then setv HUB_GOPROXY "$GOPX"; fi
}

ca_bundle() { # ca_bundle <out>: system bundle + the corporate CA (curl --cacert replaces the system one)
  cat /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/certs/ca-certificates.crt /etc/ssl/cert.pem > "$1" 2>/dev/null || true
  cat "$2" >> "$1"
}

host_curl_args() { # the install's own downloads (compose binary): the same proxy and CA as the builds
  HOST_CURL=()
  if [ -n "$NET_PROXY" ]; then HOST_CURL+=(--proxy "$NET_PROXY"); fi
  if [ -n "$CA_FILE" ]; then
    HOST_BUNDLE=$(mktemp)
    ca_bundle "$HOST_BUNDLE" "$CA_FILE"
    HOST_CURL+=(--cacert "$HOST_BUNDLE")
  fi
}

build_env() { # exports the build args from .prod.env; call it in a subshell so the runtime commands do not inherit them
  local np="localhost,127.0.0.1${HUB_NO_PROXY:+,$HUB_NO_PROXY}"
  # Podman (buildah) records build args in the image history: an authenticated proxy stays out of them
  if [ -n "${HUB_PROXY:-}" ] && docker --version 2>/dev/null | grep -qi podman && [ "$HUB_PROXY" != "$(mask_url "$HUB_PROXY")" ]; then
    echo "aviso: proxy com senha + Podman: os builds não recebem o proxy (a senha ficaria no histórico da imagem); use espelhos internos (--pypi-index, --registry-mirror) ou um proxy sem senha liberado para este servidor" >&2
  elif [ -n "${HUB_PROXY:-}" ]; then
    export HTTP_PROXY=$HUB_PROXY HTTPS_PROXY=$HUB_PROXY http_proxy=$HUB_PROXY https_proxy=$HUB_PROXY
    export NO_PROXY=$np no_proxy=$np
  fi
  if [ -n "${HUB_CA_FILE:-}" ]; then
    EXTRA_CA_PEM=$(cat "$HUB_CA_FILE"); export EXTRA_CA_PEM
    # the engine's own pulls (podman is Go: SSL_CERT_FILE) trust the corporate CA too
    mkdir -p .secrets/ca && ca_bundle .secrets/ca/bundle.pem "$HUB_CA_FILE" && export SSL_CERT_FILE=$PWD/.secrets/ca/bundle.pem
  fi
  if [ -n "${HUB_PYPI_INDEX:-}" ]; then export PIP_INDEX_URL=$HUB_PYPI_INDEX UV_DEFAULT_INDEX=$HUB_PYPI_INDEX; fi
  if [ -n "${HUB_IMAGE_PREFIX:-}" ]; then export IMAGE_PREFIX=$HUB_IMAGE_PREFIX; fi
  if [ -n "${HUB_APK_MIRROR:-}" ]; then export APK_MIRROR=$HUB_APK_MIRROR; fi
  if [ -n "${HUB_GOPROXY:-}" ]; then export GOPROXY=$HUB_GOPROXY; fi
}

build_args() { # `--build-arg NAME` for each one set: docker build reads the value from the environment
  local v
  for v in $BUILD_ARG_NAMES; do
    if [ -n "${!v:-}" ]; then printf -- '--build-arg %s ' "$v"; fi
  done
}
