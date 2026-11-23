# shellcheck shell=bash
# After the stack is up (install.sh): the sample bucket, checks from inside the hub, the final message and the
# command saved for the next server. Needs net.sh (mask_url).

make_bucket() { # bucket of the sample app suppliers; the hub works without it, so a failure only warns
  local err="" _
  # an internal call: the engine's proxy (Podman passes HTTP(S)_PROXY to every container) must not apply
  local mc=(docker run --rm --network "${P}_default" -e HTTP_PROXY= -e HTTPS_PROXY= -e http_proxy= -e https_proxy=)
  for _ in $(seq 60); do
    err=$("${mc[@]}" -e "PW=$APP_HUB_OBJSTORE_PASSWORD" --entrypoint sh "$HUB_MC_IMAGE" -c \
      'mc alias set r http://app-hub-objstore:9000 hubadmin "$PW" >/dev/null && mc mb -p r/suppliers >/dev/null' 2>&1) &&
      return 0
    sleep 2
  done
  echo "aviso: o bucket do app de exemplo suppliers não foi criado: $(printf '%s' "$err" | tail -1)" >&2
  echo "  crie à mão: ${mc[*]} -it --entrypoint sh $HUB_MC_IMAGE" >&2
  echo "    e nele: mc alias set r http://app-hub-objstore:9000 hubadmin <APP_HUB_OBJSTORE_PASSWORD do .prod.env> && mc mb -p r/suppliers" >&2
  return 0
}

final_check() { # final_check <name> <url> [hint]: the hub container reaches the URL (with its own proxy settings)
  if docker exec "$P-web" python -c "import urllib.request as u; u.urlopen('$2', timeout=5)" >/dev/null 2>&1; then
    echo "  ✔ o hub alcança $1" >&4
  else
    echo "  ✘ o hub não alcança $1 ($2)" >&4
    if [ -n "${3:-}" ]; then echo "      → $3" >&4; fi
  fi
}

final_checks() { # what broke on a corporate network: internal calls going to the proxy
  echo "Verificação final (de dentro do hub):" >&4
  final_check "MinIO" "http://app-hub-objstore:9000/minio/health/live" \
    "chamada interna indo para o proxy? confira --no-proxy (rode --doctor)"
  final_check "o próprio /readyz" "http://127.0.0.1:8090/readyz"
  return 0
}

final_message() {
  local dir=$PWD
  echo
  echo "Application Hub no ar: $HUB_PUBLIC_URL  (projeto $P, porta $HUB_PORT)"
  echo "Senha temporária do primeiro acesso:  cat $dir/.secrets/primeiro-acesso.txt  (apague depois de entrar)"
  echo "Chave mestra do cofre: $dir/.secrets/master.key — copie para fora do servidor."
  if [ -f "$dir/.secrets/hub-env.sh" ]; then echo "Comandos manuais no engine do hub:  source $dir/.secrets/hub-env.sh"; fi
  if [ "${HUB_SSO_PROVIDER:-}" = entra ]; then
    echo "Login Microsoft: registre no app registration a Redirect URI $HUB_PUBLIC_URL/auth/entra/callback"
  fi
  echo "Comando desta instalação (para outro servidor): $dir/.secrets/install-cmd.txt"
}

save_install_cmd() { # the options of this install as one command (proxy password hidden; no secret values, only files)
  local c="bash platform-py/scripts/install.sh --yes --admin-email $EMAIL --port $PORT --bind $BIND"
  c+=" --project $PROJECT --subnet $SUBNET${PUBLIC_URL:+ --public-url $PUBLIC_URL}"
  [ "$DEMO" = 1 ] || c+=" --no-demo-apps"
  [ -z "$NET_PROXY" ] || c+=" --proxy '$(mask_url "$NET_PROXY")'"
  [ -z "$NET_NOPROXY" ] || c+=" --no-proxy $NET_NOPROXY"
  [ -z "$CA_FILE" ] || c+=" --ca-file $CA_FILE"
  [ -z "$PYPI" ] || c+=" --pypi-index $PYPI"
  [ -z "$REG_MIRROR" ] || c+=" --registry-mirror $REG_MIRROR"
  [ -z "$APK" ] || c+=" --apk-mirror $APK"
  [ -z "$GOPX" ] || c+=" --goproxy $GOPX"
  [ -z "$DATA_DIR" ] || c+=" --data-dir $DATA_DIR"
  [ -z "$ENGINE_STORE" ] || c+=" --engine-store $ENGINE_STORE"
  [ -z "$TLS_CERT" ] || c+=" --tls-cert $TLS_CERT --tls-key $TLS_KEY"
  case "${ENTRA:-n}" in s | S) c+=" --entra-tenant $ENTRA_TENANT --entra-client-id $ENTRA_CLIENT --entra-role '$ENTRA_ROLE'"
    c+=" --local-login $ENTRA_LOCAL${ENTRA_DOMAINS:+ --entra-domains $ENTRA_DOMAINS}"
    c+=" --entra-client-secret-file ${ENTRA_SECRET_FILE:-<arquivo com o segredo>}" ;; esac
  case "${PROXY:-n}" in s | S) c+=" --trusted-proxy $PROXIES" ;; esac
  mkdir -p .secrets
  printf '%s\n' "# install.sh options of this instance; the proxy password, if any, was replaced by ***" "$c" > .secrets/install-cmd.txt
}
