# shellcheck shell=bash
# Read access to the apps' Postgres from other machines (dashboards, SQL clients): the question, the flags and what goes
# to .prod.env. The hub then writes the database's network rules and TLS (hub/admin/dbaccess_net.py); the accounts
# are made in Administration › Users › Database access.
DBPUB=n DBPUB_PORT=5432 DBPUB_CIDRS="" DBPUB_HOST=""

dbpub_questions() {
  ask DBPUB "Acesso de leitura ao banco por outras máquinas (dashboards, clientes SQL)? (s/n)" '^[snSN]$'
  yes_no "$DBPUB" || return 0
  ask DBPUB_PORT "  Porta do banco no host" '^[0-9]{2,5}$'
  ask DBPUB_CIDRS "  Redes que podem conectar (CIDR, vírgula); vazio = qualquer origem que alcance a porta (rede corporativa/VPN)" '^$|^[0-9a-fA-F.:/,]+$'
  ask DBPUB_HOST "  Nome ou IP que as pessoas usam para conectar (vazio = o da URL do hub)" '^$|^[a-zA-Z0-9.:-]+$'
}

# A rerun keeps .prod.env without questions; this option came later, so an install that never answered is asked once.
dbpub_rerun_question() {
  [ "$YES" = 0 ] && [ "${FLAGS_GIVEN:-0}" = 0 ] || return 0
  grep -q '^HUB_DB_PUBLIC=' "$1" && return 0
  [ -n "${INSTALL_TTY:-}" ] || { [ -t 0 ] && [ -r /dev/tty ]; } || return 0
  [ "$TTY_OPEN" = 1 ] || { exec 3< "$TTY"; TTY_OPEN=1; [ -n "${INSTALL_TTY:-}" ] || exec 4> /dev/tty; }
  echo "Opção nova desta versão:"
  dbpub_questions
  yes_no "$DBPUB" || DBPUB=off # answered "no": recorded, never asked again
}

dbpub_validate() {
  yes_no "$DBPUB" || return 0
  echo "$DBPUB_PORT" | grep -Eq '^[0-9]{2,5}$' || opts_fail "--db-public-port: '$DBPUB_PORT' inválido (ex.: 5432)"
  [ -n "$DBPUB_CIDRS" ] ||
    echo "aviso: sem --db-allowed-cidrs, o banco aceita contas de leitura de qualquer origem que alcance a porta (TLS e senha continuam obrigatórios); use só se a rede corporativa/VPN já limita o acesso" >&2
  echo "$DBPUB_CIDRS" | grep -Eq '^$|^[0-9a-fA-F.:/,]+$' ||
    opts_fail "--db-allowed-cidrs: '$DBPUB_CIDRS' inválido (CIDR separados por vírgula, ex.: 10.0.0.0/8,192.168.1.0/24)"
  echo "$DBPUB_HOST" | grep -Eq '^$|^[a-zA-Z0-9.:-]+$' || opts_fail "--db-public-host: '$DBPUB_HOST' inválido (nome ou IP)"
}

dbpub_write() { # setv: on (port on every interface + the hub's rules), off (back to the loopback debug port), or untouched
  if yes_no "$DBPUB"; then
    setv HUB_DB_PUBLIC 1
    setv HUB_DB_PUBLIC_PORT "$DBPUB_PORT"
    setv HUB_DB_PUBLIC_BIND 0.0.0.0
    setv HUB_DB_ALLOWED_CIDRS "$DBPUB_CIDRS"
    if [ -n "$DBPUB_HOST" ]; then setv HUB_DB_PUBLIC_HOST "$DBPUB_HOST"; fi
  elif [ "$DBPUB" = off ]; then
    setv HUB_DB_PUBLIC 0
    sed -i '/^HUB_DB_PUBLIC_BIND=/d;/^HUB_DB_PUBLIC_PORT=/d' "$ENVF"
  fi
  return 0
}
