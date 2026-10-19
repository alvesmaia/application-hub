# shellcheck shell=bash
# HTTPS at the edge for install.sh (--tls-cert/--tls-key): the company certificate is copied to .secrets/tls (0600)
# and the edge imports tls.caddy. Needed for Entra sign-in, which refuses http:// redirect addresses.
TLS_CERT="" TLS_KEY=""

tls_questions() { # interactive, only for an https:// URL
  case "${PUBLIC_URL:-}" in https://*) ;; *) return 0 ;; esac
  ask TLS_CERT "Certificado TLS do hub (PEM); vazio = HTTPS vem de um proxy/túnel na frente" '^$|^[^ ]+$'
  [ -n "$TLS_CERT" ] || return 0
  ask TLS_KEY "  Chave privada do certificado (PEM; o conteúdo não aparece)" '^[^ ]+$'
}

tls_validate() { # both or none; the key's content is never printed
  [ -n "$TLS_CERT$TLS_KEY" ] || return 0
  [ -n "$TLS_CERT" ] && [ -n "$TLS_KEY" ] || { echo "--tls-cert e --tls-key: informe os dois arquivos" >&2; exit 2; }
  [ -r "$TLS_CERT" ] && [ -r "$TLS_KEY" ] || { echo "--tls-cert/--tls-key: $TLS_CERT ou $TLS_KEY não pode ser lido" >&2; exit 2; }
  grep -q -- '-----BEGIN CERTIFICATE-----' "$TLS_CERT" ||
    { echo "--tls-cert: $TLS_CERT não é um certificado PEM (esperado -----BEGIN CERTIFICATE-----)" >&2; exit 2; }
  grep -Eq -- '-----BEGIN ([A-Z]+ )?PRIVATE KEY-----' "$TLS_KEY" ||
    { echo "--tls-key: $TLS_KEY não é uma chave PEM (esperado -----BEGIN … PRIVATE KEY-----)" >&2; exit 2; }
  if command -v openssl >/dev/null && ! openssl x509 -checkend 0 -noout -in "$TLS_CERT" >/dev/null 2>&1; then
    openssl x509 -noout -in "$TLS_CERT" >/dev/null 2>&1 &&
      { echo "--tls-cert: o certificado $TLS_CERT está vencido" >&2; exit 2; }
  fi
  TLS_CERT=$(cd "$(dirname "$TLS_CERT")" && pwd)/$(basename "$TLS_CERT")
  TLS_KEY=$(cd "$(dirname "$TLS_KEY")" && pwd)/$(basename "$TLS_KEY")
  : "${PUBLIC_URL:=https://localhost:$PORT}"
}

tls_summary() {
  if [ -n "$TLS_CERT" ]; then echo "  • servir HTTPS na porta $PORT com o certificado $TLS_CERT"; fi
}

tls_write() { # .secrets/tls (0600) + .prod.env (setv)
  [ -n "$TLS_CERT" ] || return 0
  (umask 077 && mkdir -p .secrets/tls && cp "$TLS_CERT" .secrets/tls/cert.pem && cp "$TLS_KEY" .secrets/tls/key.pem)
  chmod 600 .secrets/tls/cert.pem .secrets/tls/key.pem 2>/dev/null || true
  setv HUB_TLS_DIR ./.secrets/tls
  setv HUB_EDGE_TLS tls.caddy
}
