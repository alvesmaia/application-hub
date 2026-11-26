#!/bin/sh
# One database per app in the apps' Postgres: `hub_outbox`, the hub-sdk migrations and the data
# of one app never mix with another's. `app_<id>`, like any app. It only runs on a new volume; on an existing volume the
# app-hub-db-roles job (hub/admin/demo_db.py) creates them (and renames the pre-0.8 `<id>` ones).
set -e
for d in inventory suppliers servicedesk collector automations; do
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE app_$d OWNER $POSTGRES_USER"
done
