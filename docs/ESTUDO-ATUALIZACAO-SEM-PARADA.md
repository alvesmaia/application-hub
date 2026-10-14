# Study: zero-downtime update with automatic rollback

> **Implemented on 2026-10-10** with one simplification: instead of two fixed colors and a switch through the Caddy API, a
> temporary copy (`<proj>-web-next`) serves traffic while `<proj>-web` is recreated; the edge chooses by health. The
> hub's name does not change (scripts and docs still apply). Usage: `docs/ATUALIZACAO.md`, "Zero downtime".

Situation as of 2026-10-10. Today, `dc up -d` recreates `app-hub-web` and the hub is down for 3 to 11 s. In that
interval the portal, the apps' gateway, the API/MCP and the SSE and WebSocket connections stop. Rolling back is
manual (`docs/ATUALIZACAO.md` §5).

## What already exists and helps

- **Hub replicas already work:** the `hub-leader` lease in the state database (`hub/ops/leader.py`) ensures that only
  one replica runs what must be unique (outbox, backup scheduler, sweeps). The files in `/data`
  use locking between processes (`hub/filelock.py`).
- **`/healthz` reports the version,** and the apps already do blue/green with a readiness probe (`/_hub/ready`).
- **Real-time reconnects on its own:** `data-hub-ws` and `data-hub-sse` come back with increasing back-off and polling in the
  meantime.

## Options

| | How it works | Downtime | Cost |
|---|---|---|---|
| A. Just restart faster | image ready beforehand, `up --no-deps` | 2–4 s | low, but does not solve it |
| **B. Blue/green behind a proxy** | fixed proxy on the hub's port; start the new color, test, and switch | **0 s** | medium |
| C. Orchestrator (Swarm/K8s) | native rolling update | 0 s | high; changes the whole installation |

**Recommendation: B.** It uses what already exists (replicas, leader, locks) and does not change how the product is installed.

## Design of option B

1. **Edge proxy** `app-hub-edge` (Caddy, official image), the only service on `HUB_PORT`. The containers
   `app-hub-web-blue` and `app-hub-web-green` stay only on the internal network. The proxy forwards HTTP, SSE and WebSocket
   without buffering and drains connections on the switch.
2. **`hub-update <version>` command** (in `platform-py/scripts/`), in steps that stop at the first error:
   1. makes the backup of §1 of `ATUALIZACAO.md` automatically and keeps the current image with the `rollback` tag;
   2. downloads or builds the new version and starts the inactive color;
   3. waits for `/healthz` with the new version and for `/readyz`. Today `/readyz` only answers "ready"; it will now check the
      state database, the applied migrations and the reading of `/data`. Then it runs quick tests (login, `/developers`, one `/apps/<id>`);
   4. switches the proxy's upstream through the Caddy administration API, without restarting;
   5. drains the old color (SIGTERM; uvicorn finishes the requests in progress) and keeps it stopped for the rollback.
3. **Automatic rollback:**
   - **Before the switch:** any failure removes the new color. Nothing changed for users.
   - **After the switch:** for 5 min, the command watches for 5xx errors and `/healthz`. If it gets worse, it points the
     proxy back to the old color, which still exists, in under 1 s.
   - **Data:** rolling back without a restore is only possible if the migrations are **additive** (expand now, remove
     columns and fields only in the following version). Each release declares `rollback_compat: <minimum-version>`. If the
     old version is not compatible, the command warns before starting and the rollback requires restoring the
     backup, with confirmation.
4. **New version notice:** optional (`HUB_UPDATE_CHECK=1`). The hub checks the repository's releases once
   a day and shows a notice in Administração › Saúde (Administration › Health) with the update command. Nothing updates on its own.

## Risks

- **Two versions running for seconds:** the old and the new color read the same `/data` and the same database. That is why
  the additive migration rule is mandatory, and a CI test starts the previous version on top of the new version's data.
- **Memory:** two copies of the hub for a few seconds (~+250 MB at peak).
- **Port and TLS:** the proxy becomes the owner of `HUB_PORT`. Those who use a tunnel or an external proxy change nothing,
  because the destination stays the same.

## Estimated effort

Proxy and compose: 1 day. `hub-update` with rollback: 2 days. A real `/readyz` and the additive migration rule with a compatibility test:
1–2 days. Version notice: half a day. Validation (chaos during the switch, load with SSE and
WebSocket open): 1 day.
