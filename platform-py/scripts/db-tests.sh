#!/usr/bin/env bash
# SQL broker tests against real PostgreSQL, MySQL and SQL Server, in disposable containers
# (app-hub-dbtest-*, only on 127.0.0.1, removed at the end). The SQL Server image is ~1,5 GB.
#   bash platform-py/scripts/db-tests.sh            (from the repository root)
set -euo pipefail
cd "$(dirname "$0")/.."
PW="Hb$(python -c 'import secrets; print(secrets.token_hex(10))')!"
P=app-hub-dbtest
cleanup() { docker rm -f "$P-pg" "$P-mysql" "$P-mssql" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
docker run -d --rm --name "$P-pg" -e POSTGRES_PASSWORD="$PW" -p 127.0.0.1:15432:5432 postgres:17 >/dev/null
docker run -d --rm --name "$P-mysql" -e MYSQL_ROOT_PASSWORD="$PW" -p 127.0.0.1:13306:3306 mysql:8.4 >/dev/null
docker run -d --rm --name "$P-mssql" -e ACCEPT_EULA=Y -e MSSQL_PID=Developer -e MSSQL_SA_PASSWORD="$PW" \
  -p 127.0.0.1:11433:1433 mcr.microsoft.com/mssql/server:2022-latest >/dev/null
# $3 = how many times the line appears (Postgres starts a temporary server before the final one)
wait_log() { for _ in $(seq 120); do [ "$(docker logs "$1" 2>&1 | grep -c "$2")" -ge "${3:-1}" ] && return 0; sleep 2; done; echo "$1 não subiu" >&2; exit 1; }
wait_log "$P-pg" "database system is ready to accept connections" 2
wait_log "$P-mysql" "port: 3306  MySQL Community Server"
wait_log "$P-mssql" "SQL Server is now ready for client connections"
sleep 3
PY=.venv/Scripts/python.exe; [ -x "$PY" ] || PY=.venv/bin/python
HUB_DB_TEST_URL="postgresql://postgres:$PW@127.0.0.1:15432/postgres" \
HUB_MYSQL_TEST_URL="mysql://root:$PW@127.0.0.1:13306/mysql" \
HUB_MSSQL_TEST_URL="sqlserver://sa:$PW@127.0.0.1:11433/master" \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_sql_connections.py -k real -rs
