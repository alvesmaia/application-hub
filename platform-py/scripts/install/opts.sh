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

opts_fail() { echo "$1" >&2; exit 2; }

entra_validate() { # --entra-*: the three together, each in the expected shape; the secret is read from its file
  if [ -n "$ENTRA_SECRET_FILE" ]; then
    [ -s "$ENTRA_SECRET_FILE" ] && [ -r "$ENTRA_SECRET_FILE" ] ||
      opts_fail "--entra-client-secret-file: $ENTRA_SECRET_FILE não existe, está vazio ou não pode ser lido"
    IFS= read -r ENTRA_SECRET < "$ENTRA_SECRET_FILE" || true
    ENTRA_SECRET=${ENTRA_SECRET%$'\r'} # a copy made on Windows
  fi
  [ -n "$ENTRA_TENANT" ] && [ -n "$ENTRA_CLIENT" ] && [ -n "$ENTRA_SECRET" ] ||
    opts_fail "login Microsoft: informe juntos --entra-tenant, --entra-client-id e --entra-client-secret-file"
  echo "$ENTRA_TENANT" | grep -Eq '^[0-9a-fA-F-]{36}$|^(organizations|common)$|^[a-zA-Z0-9.-]+\.[a-z]+$' ||
    opts_fail "--entra-tenant: '$ENTRA_TENANT' inválido (esperado o GUID do tenant, organizations ou common)"
  echo "$ENTRA_CLIENT" | grep -Eq '^[0-9a-fA-F-]{36}$' ||
    opts_fail "--entra-client-id: '$ENTRA_CLIENT' inválido (esperado o GUID do app registration)"
  echo "$ENTRA_DOMAINS" | grep -Eq '^$|^[a-zA-Z0-9.,-]+$' || opts_fail "--entra-domains: '$ENTRA_DOMAINS' inválido (ex.: empresa.com,filial.com)"
  # Colaborador: the "User" of installs seeded before the three default roles
  case "$ENTRA_ROLE" in User | "Power User" | Administrador | Colaborador) ;; *)
    opts_fail "--entra-role: '$ENTRA_ROLE' inválido (User, Power User ou Administrador)" ;; esac
  case "$ENTRA_LOCAL" in admins | todos | nenhum) ;; *) opts_fail "--local-login: '$ENTRA_LOCAL' inválido (admins, todos ou nenhum)" ;; esac
  if [ "$CONFIG_ONLY" = 1 ]; then echo "aviso: --config-only não grava o segredo do Entra no cofre; a instalação grava (mesmas opções)" >&2; fi
}

opts_validate() { # exit 2 with the offending value and the expected shape
  if yes_no "$ENTRA"; then entra_validate
  elif [ "$ENTRA_LOCAL" != admins ]; then
    opts_fail "--local-login $ENTRA_LOCAL só com o login Microsoft (--entra-tenant …): sem ele ninguém entraria"
  fi
  if yes_no "$PROXY"; then
    echo "$PROXIES" | grep -Eq '^[0-9a-fA-F.:/,]+$' || opts_fail "--trusted-proxy: '$PROXIES' inválido (esperado CIDR, ex.: 127.0.0.1/32)"
  fi
  return 0
}

opts_write() { # .prod.env (setv): interactive answers or the flags above
  if yes_no "$ENTRA"; then
    setv HUB_SSO_PROVIDER entra
    setv HUB_ENTRA_TENANT_ID "$ENTRA_TENANT"
    setv HUB_ENTRA_CLIENT_ID "$ENTRA_CLIENT"
    if [ -n "$ENTRA_DOMAINS" ]; then setv HUB_ENTRA_ALLOWED_DOMAINS "$ENTRA_DOMAINS"; fi
    case "$ENTRA_ROLE" in *" "*) setv HUB_ENTRA_DEFAULT_ROLE "'$ENTRA_ROLE'" ;; *) setv HUB_ENTRA_DEFAULT_ROLE "$ENTRA_ROLE" ;; esac
    setv HUB_LOCAL_LOGIN "$(case "$ENTRA_LOCAL" in todos) echo 1 ;; nenhum) echo 0 ;; *) echo admins ;; esac)"
  fi
  if yes_no "$PROXY"; then setv HUB_TRUST_PROXY_HEADERS 1; setv HUB_TRUSTED_PROXIES "$PROXIES"; fi
  return 0
}
