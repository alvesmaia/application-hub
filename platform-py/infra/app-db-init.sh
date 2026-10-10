#!/bin/sh
# Um banco por app no Postgres dos apps (como no compose Rust): o `hub_outbox`, as migrações do hub-sdk e os dados
# de um app nunca se misturam com os de outro. Só roda em volume novo; num volume existente crie à mão:
#   for d in ativos fornecedores servicos coletor; do docker exec app-hub-app-db createdb -U hub $d; done
set -e
for d in ativos fornecedores servicos coletor; do
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE $d OWNER $POSTGRES_USER"
done
