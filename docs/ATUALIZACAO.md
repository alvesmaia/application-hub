# Version update (Python hub)

Applies to installations made by `platform-py/scripts/install.sh` or by the checklist in `docs/IMPLANTACAO.md`.
Read the [`CHANGELOG.md`](../CHANGELOG.md) of the new version first (section "Attention" when there is a manual step).
Commands are run from `platform-py/`; abbreviate compose like this:

```bash
dc() { docker compose --env-file .prod.env -f docker-compose.yml -f docker-compose.apps.yml -f docker-compose.prod.yml "$@"; }
curl -s http://127.0.0.1:${HUB_PORT:-8090}/healthz     # {"status":"ok","version":"<current>"}
```

## Zero downtime (recommended)

The hub's port belongs to the edge (`<project>-edge`, Caddy), which only sends traffic to whoever answers `/healthz`. The
command below makes the backup, starts a copy with the new version, replaces the hub while the copy serves traffic, watches for
5 min and, if the new version fails, **goes back automatically** to the previous one:

```bash
git fetch --tags && git checkout v<version>      # or: git pull on main
python scripts/hub_update.py --build             # builds from the current code
python scripts/hub_update.py --image <image>    # or: ready-made image (GHCR)
python scripts/hub_update.py --rollback          # roll back later, by hand
```

Options: `--project` (HUB_PROJECT, default `app-hub`), `--watch <s>` (observation, default 300), `--no-backup`,
`--backup-dir` (default `~/backup-hub/<date-time>`). Open connections (SSE, WebSocket) drop during the switch and reconnect
on their own. The automatic rollback undoes the code, not the data: if the new version changed the data format, restore the
backup (§5). That is why the hub's migrations are additive only (the previous version keeps reading the data).

**First time with the edge** (installations prior to 0.3.0): the `dc up -d` in §3 creates the edge, which takes over the
`HUB_PORT` of the hub (a ~10 s stop, this time only). After that, use `hub_update.py`.

New version notice in Administração › Saúde (Administration › Health): `HUB_UPDATE_CHECK=1` in `.prod.env`.

The steps below are the manual path (with a short stop).

## 1. Backup (always)

```bash
P=$(grep -E '^HUB_PROJECT=' .prod.env | cut -d= -f2); P=${P:-app-hub}
mkdir -p ~/backup-hub && cd ~/backup-hub
docker exec "$P-db"     pg_dumpall -U hub > hub-db.sql
docker exec "$P-app-db" pg_dumpall -U hub > app-db.sql
docker run --rm -v "$P-data:/d:ro" -v "$PWD:/b" alpine tar czf /b/hub-data.tgz -C /d .
docker run --rm -v "$P-objstore-data:/d:ro" -v "$PWD:/b" alpine tar czf /b/objstore.tgz -C /d .
cd -
```

Also keep the vault master key (`/data/master.key` or `.secrets/master.key`) and `.prod.env` — without the
key, the encrypted secrets cannot be opened.

## 2. New code

```bash
git fetch --tags
git checkout v<version>          # or: git pull on main
diff <(grep -oE '^[A-Z_]+' prod.env.example | sort -u) <(grep -oE '^[A-Z_]+' .prod.env | sort -u)   # new variables?
```

## 3. Rebuild and start

```bash
dc build                        # hub and apps from source (or: -f docker-compose.ghcr.yml pull, HUB_VERSION=<version>)
dc up -d                        # recreates only what changed
curl -s http://127.0.0.1:${HUB_PORT:-8090}/healthz     # check the new version
```

Only the hub changed? `dc build app-hub-web && dc up -d --no-deps app-hub-web` (the apps stay up).

## 4. Migrations

- **Hub state database** (`app-hub-db`): the hub creates/updates its own tables at boot (no manual step).
- **File data** (`/data`: users, vault, administration): the format is migrated at boot; old versions are
  read and rewritten in the new format.
- **App databases** (`app-hub-app-db`): each app applies its own migrations when it starts (`hub_sdk`).
- Manual steps, when they exist, are in the version's CHANGELOG.

## 5. Check and roll back

Check login, `/apps/<id>`, Administração › Saúde (Administration › Health) and the logs (`docker logs <project>-web`). If something goes wrong:

```bash
git checkout v<previous-version> && dc build && dc up -d
# incompatible data? restore the step 1 backup (stop the stack, restore volumes and dumps, start again)
```
