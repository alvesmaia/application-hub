# shellcheck shell=bash
# Read-only diagnosis for install.sh (--doctor, and step 0 of the install): network through the configured path
# (never around the proxy), TLS inspection, corporate CA, mirrors, a build inside a container, disk and port.
# Each failure says the cause and the option that fixes it. Needs net.sh (build_env, build_args, mask_url).
DOCTOR_FAILS=0

d_ok() { echo "  ✔ $*" >&4; }
d_warn() { echo "  ! $*" >&4; }
d_fail() { # d_fail <what failed> [what to do]
  echo "  ✘ $1" >&4
  if [ -n "${2:-}" ]; then echo "      → $2" >&4; fi
  DOCTOR_FAILS=$((DOCTOR_FAILS + 1))
}

doctor_conf() { # the values in effect: options of this run, else .prod.env; exported for build_env
  HUB_PROXY=${NET_PROXY:-${HUB_PROXY:-}} HUB_NO_PROXY=${NET_NOPROXY:-${HUB_NO_PROXY:-}}
  HUB_CA_FILE=${CA_FILE:-${HUB_CA_FILE:-}} HUB_PYPI_INDEX=${PYPI:-${HUB_PYPI_INDEX:-}}
  HUB_APK_MIRROR=${APK:-${HUB_APK_MIRROR:-}} HUB_GOPROXY=${GOPX:-${HUB_GOPROXY:-}}
  if [ -n "$REG_MIRROR" ]; then HUB_IMAGE_PREFIX=${REG_MIRROR%/}/library/; fi
  HUB_IMAGE_PREFIX=${HUB_IMAGE_PREFIX:-}
  D_BUNDLE=""
  [ -n "$HUB_CA_FILE" ] || return 0
  D_BUNDLE=$(mktemp) # curl --cacert replaces the system bundle: system + corporate CA
  cat /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/certs/ca-certificates.crt /etc/ssl/cert.pem > "$D_BUNDLE" 2>/dev/null || true
  cat "$HUB_CA_FILE" >> "$D_BUNDLE"
}

curl_args() { # the path the build will use: the proxy (except --no-proxy destinations) and the corporate CA
  D_ARGS=(--max-time 15)
  if [ -n "$HUB_PROXY" ]; then D_ARGS+=(--proxy "$HUB_PROXY" --noproxy "localhost,127.0.0.1${HUB_NO_PROXY:+,$HUB_NO_PROXY}"); fi
  if [ -n "$D_BUNDLE" ]; then D_ARGS+=(--cacert "$D_BUNDLE"); fi
}

probe() { # probe <url>: PROBE_RC (curl exit code) and PROBE_CODE (HTTP status)
  curl_args
  PROBE_RC=0
  PROBE_CODE=$(curl -sS -o /dev/null -w '%{http_code}' "${D_ARGS[@]}" "$1" 2>/dev/null) || PROBE_RC=$?
}

explain_failure() { # explain_failure <name> <url>: cause + option, from the curl exit code / HTTP status
  local envproxy=${HTTPS_PROXY:-${https_proxy:-${HTTP_PROXY:-${http_proxy:-}}}} issuer
  case "$PROBE_RC" in
    35 | 51 | 58 | 60)
      issuer=$(curl -sv -o /dev/null "${D_ARGS[@]}" "$2" 2>&1 | sed -n 's/.*issuer: //p' | head -1)
      if [ -n "$HUB_CA_FILE" ]; then
        d_fail "$1: certificado não confiável (emissor: ${issuer:-?})" "a CA de --ca-file ($HUB_CA_FILE) não valida este certificado: confira o arquivo com a TI"
      else
        d_fail "$1: certificado não confiável (emissor: ${issuer:-?}); o proxy deve inspecionar TLS" "informe a CA corporativa: --ca-file <arquivo.pem> (peça à TI a CA desse emissor)"
      fi ;;
    5 | 6 | 7 | 28)
      if [ -n "$HUB_PROXY" ]; then d_fail "$1: o proxy $(mask_url "$HUB_PROXY") não alcança $2" "confira o endereço do proxy ou se o destino está liberado"
      elif [ -n "$envproxy" ]; then d_fail "$1: sem saída direta para $2" "a rede usa proxy: --proxy $(mask_url "$envproxy")"
      else d_fail "$1: sem acesso a $2" "se a rede usa proxy: --proxy http://host:porta; ou um espelho interno"; fi ;;
    *) d_fail "$1: erro $PROBE_RC em $2" ;;
  esac
}

check_url() { # check_url <name> <url>: 2xx, 3xx and 401 (a registry asking for a token) mean it is reachable
  probe "$2"
  if [ "$PROBE_RC" != 0 ]; then explain_failure "$1" "$2"; return 0; fi
  case "$PROBE_CODE" in
    2?? | 3?? | 401) d_ok "$1 ($2)" ;;
    403 | 407) d_fail "$1: o proxy recusou $2 (HTTP $PROBE_CODE)" "autenticação do proxy (http://usuário:senha@host:porta) ou liberação do destino" ;;
    *) d_fail "$1: $2 respondeu $PROBE_CODE" "confira o caminho do espelho/URL" ;;
  esac
}

check_network() {
  command -v curl >/dev/null || { d_fail "curl não encontrado" "instale o curl"; return 0; }
  check_url "PyPI" "${HUB_PYPI_INDEX:-https://pypi.org/simple/}"
  local reg=${HUB_IMAGE_PREFIX%%/*}
  check_url "imagens" "https://${reg:-registry-1.docker.io}/v2/"
  check_url "GitHub (código do MinIO)" "https://github.com/minio/minio"
  check_url "Alpine" "${HUB_APK_MIRROR:-https://dl-cdn.alpinelinux.org/alpine}"
  check_url "Go" "${HUB_GOPROXY:-https://proxy.golang.org}"
}

check_container() { # a tiny build with the same build args: proves the proxy and the CA reach inside the build
  if ! docker info >/dev/null 2>&1; then d_warn "engine indisponível: teste dentro de contêiner pulado"; return 0; fi
  local tag="$PROJECT-doctor:tmp" url=${HUB_PYPI_INDEX:-https://pypi.org/simple/} out rc=0
  # shellcheck disable=SC2046 # build_args: only names
  out=$( (build_env && printf '%s\n' "FROM ${HUB_IMAGE_PREFIX:-docker.io/library/}python:3.12-slim" 'ARG EXTRA_CA_PEM=""' \
    'RUN if [ -n "$EXTRA_CA_PEM" ]; then printf "%s\n" "$EXTRA_CA_PEM" >> /etc/ssl/certs/ca-certificates.crt; fi' \
    "RUN python -c \"import urllib.request as u; u.urlopen('$url', timeout=20)\"" |
    docker build -q --rm -t "$tag" $(build_args) - 2>&1) ) || rc=$?
  docker image rm -f "$tag" >/dev/null 2>&1 || true
  if [ "$rc" = 0 ]; then d_ok "build em contêiner alcança $url com o proxy e a CA"; return 0; fi
  d_fail "build em contêiner não alcança $url" "$(printf '%s' "$out" | tail -3 | tr '\n' ' ')"
}

check_disk() {
  local root free
  root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
  [ -n "$root" ] && [ -d "$root" ] || return 0
  free=$(df -Pk "$root" 2>/dev/null | awk 'NR == 2 { print int($4 / 1048576) }')
  [ -n "$free" ] || return 0
  if [ "$free" -lt 4 ]; then d_fail "store do engine ($root) com ${free} GB livres; as imagens do hub ocupam ~3 GB" "libere espaço ou use outro disco"
  elif [ "$free" -lt 10 ]; then d_warn "store do engine ($root) com ${free} GB livres (cabe; recomendado 10 GB)"
  else d_ok "disco do engine: ${free} GB livres em $root"; fi
}

port_in_use() { ss -ltn 2>/dev/null | awk '{ print $4 }' | grep -Eq "[:.]$1\$"; }

check_port() {
  command -v ss >/dev/null || { d_warn "ss não encontrado: porta $PORT não conferida"; return 0; }
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$PROJECT-edge"; then d_ok "porta $PORT: é deste hub"; return 0; fi
  if ! port_in_use "$PORT"; then d_ok "porta $PORT livre"; return 0; fi
  local p=$((PORT + 1))
  while port_in_use "$p"; do p=$((p + 1)); done
  d_fail "porta $PORT em uso" "use --port $p"
}

doctor() { # returns 1 when something failed (each failure already says what to do)
  echo "Diagnóstico (só de leitura: testa e não altera nada)" >&4
  doctor_conf
  check_network
  check_container
  check_disk
  check_port
  if [ -n "$D_BUNDLE" ]; then rm -f "$D_BUNDLE"; fi
  [ "$DOCTOR_FAILS" = 0 ] && { echo "  diagnóstico ok" >&4; return 0; }
  echo "  $DOCTOR_FAILS problema(s): corrija com as opções sugeridas" >&4
  return 1
}
