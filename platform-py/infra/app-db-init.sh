#!/bin/sh
# One database per app in the apps' Postgres: `hub_outbox`, the hub-sdk migrations and the data
# of one app never mix with another's. It only runs on a new volume; on an existing volume create them by hand:
#   for d in ativos fornecedores servicos coletor; do docker exec app-hub-app-db createdb -U hub $d; done
set -e
for d in ativos fornecedores servicos coletor; do
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE $d OWNER $POSTGRES_USER"
done
