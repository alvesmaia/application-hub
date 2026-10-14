# Fresh-install test — RHEL-like, rootless user (2026-10-10)

Simulated scenario: corporate server Rocky Linux 9.3 (RHEL clone) with systemd, Podman 5.8.2 from the distro
(`podman-docker` installed), service user `hubsvc` **without root and without sudo**, with linger. Source: clone of the
public repository `alvesmaia/application-hub` and `bash platform-py/scripts/install.sh` (the documented path).
Access only from localhost. After the deployment, a Haiku agent with minimal context (hub URL + developer token) tried to
create and publish a "Lista de compras" (shopping list) app.

| | Scenario A — 4 GB / 2 CPU | Scenario B — 8 GB / 2 CPU |
|---|---|---|
| User cgroup delegation | RHEL 9 default: `memory pids` | admin applied `Delegate=cpu cpuset io memory pids` |
| `install.sh` | 3 runs (2 failures, worked around) | 1 run (workarounds already applied), 5 min 37 s |
| Stack memory running (4 demo apps) | 2.1 GB | 1.9 GB |
| App build by the hub (`RUN`) | **fails** | ok |
| App publication by Haiku | **failed** (tmpfs `uid=`) | **failed** (tmpfs `uid=`) |
| Haiku: time / cost | 16 min / US$ 0.57 | 5.5 min / US$ 0.05 |

No app was published in either scenario: the final blocker is in the product with Podman (item P1).

## Round 2 — after the fixes (P1–P5)

Same two scenarios recreated from scratch, only with the steps from the guide (docs/IMPLANTACAO.md › Podman rootless), without editing
the compose file: `install.sh` green on the first run in both (~6.5 min, in parallel); in A it warned about the undelegated CPU.
Haiku published the app to staging **on the first attempt** in both scenarios (A: 3.8 min / US$ 0.03; B: 3.7 min /
US$ 0.03). Checked on the server: container `healthy`, `/scratch` of user 10001 writable, `/_hub/ready` 200,
page `/apps/lista-compras/itens` 200 through the hub; CPU unlimited in A (no delegation) and 0.5 CPU in B.

P6 (no compose on the RHEL base, worked around and documented), P7, P8 and P9 remain open. New: the doc served by the hub
cites the port from `HUB_PUBLIC_URL` (8090); behind a port NAT the reader must change it — this only affects this test.

## Round 3 — items P6–P9 and interactive installer

Third clean server (`rhel-c`, 4 GB / 2 CPU, no compose installed), `install.sh` with no options:
it asked for each value, showed the summary, asked for confirmation, offered to download `docker-compose` (sha256 checked)
and warned about the undelegated CPU. It was installed choosing **not** to start the sample apps — and that is when P10 appeared.

- **P10 (blocking, fixed) — hub did not start without the sample apps.** The initial load
  (`HUB_APP_ENV_FILE=app-env.prod.json`) has variables bound to the demo apps; with `apps.empty.json` the catalog
  does not have them and boot died with `AdminError: Aplicação desconhecida: ativos.` (Unknown application: ativos.). Now the load
  discards app bindings outside the catalog (an installation file, not user input); API validation stays strict.
  Checked: `/readyz` ready and `/login` 200 with `HUB_APPS_JSON=./apps.empty.json`.
- **P6 resolved:** `install.sh --get-compose` (or the question in interactive mode) downloads the official binary at a fixed
  version with sha256 checked into `~/.local/bin`, without root.
- **P7 resolved:** the hub reports the size and the limit (checked on a real hub: `O pacote tem 22.0 MB; o limite é
  20 MB…` — the package is 22.0 MB; the limit is 20 MB…), `hub deploy` passes on the reason (it read only `message`, the reason was in `error`) and `hub pack` refuses before sending, pointing to the largest files. Limit documented in the publishing guide.
- **P8 resolved:** the `§3 página`/`§3 fragmento` failure (§3 page / §3 fragment) shows the status and the last exception from `.hub/check-app.log`,
  with the tip to delete `.hub/*.db` when it is `no such column`/`no such table`.
- **P9 resolved:** section «Testar sem navegador» (Test without a browser) in the screens guide, with the headers and the idempotency key.

New installer feature: interactive mode (defaults in square brackets, summary, confirmation); `--yes` or any
instance option keeps the silent mode for CI.
## Difficulties — product (round 1)

- **P1 (blocking) — tmpfs `/scratch` with `uid=10001`.** The runtime creates the app container with
  `--tmpfs /scratch:uid=10001,gid=10001`; Podman refuses: `container create: unknown mount option "uid=10001":
  invalid mount option`. No app starts with Podman. Path: detect Podman (`/version` → "Podman Engine") and use
  `tmpfs-mode`/`U`, or an anonymous volume with `chown` in the image.
- **P2 (blocking without admin) — build with `cpushares`/`cpusetcpus`.** `build_stream` passes CPU limits; without the `cpu`/`cpuset`
  controller delegated (RHEL 9 rootless default) **every `RUN` fails** with
  `while running runtime: exit status 1` (reproduced: `podman build --cpu-shares 512` fails, `--memory` passes).
  Path: document `Delegate=` as a prerequisite and/or repeat the build without CPU limits when the daemon is
  rootless without delegation, with a clear message.
- **P3 — fixed socket `/var/run/docker.sock` in the compose file.** Rootless does not have that socket:
  `statfs /var/run/docker.sock: permission denied` in step 3 of `install.sh`. Workaround used: edit the compose file to
  `${XDG_RUNTIME_DIR}/podman/podman.sock`. Path: variable `HUB_CONTAINER_SOCKET_HOST` (default `/var/run/docker.sock`)
  and `install.sh` detecting rootless Podman.
- **P4 — fixed `[::1]` port.** With `--bind 0.0.0.0` Podman's `rootlessport` already occupies IPv6:
  `rootlessport listen tcp [::1]:8090: bind: address already in use`. Workaround: remove the `"[::1]:…"` line.
  Path: only publish `[::1]` when `HUB_BIND=127.0.0.1`.
- **P5 — `install.sh` points to the private repository** (`--clone` uses `application-hub.git`); it should be the public one.
- **P6 — `install.sh` requires `docker compose`.** RHEL/Rocky does not ship a compose provider; `podman-compose` comes from
  EPEL (needs root). Rootless workaround: the official `docker-compose` binary in `~/.local/bin` (`podman compose` uses it).
  Document a "Podman rootless (RHEL)" section in IMPLANTACAO.md.
- **P7 — package above ~20 MB refused with `400` without a reason.** The limit is not documented and the message does not say
  what it is. With vendored dependencies, the Haiku of scenario A got past this by replacing `uvicorn[standard]` with `uvicorn`.
- **P8 — `hub check` fails conformance "§3 fragmento com o workspace" (§3 fragment with the workspace) without a cause.** The real cause (an old
  `.hub/*.db` database after editing migration 0001: `no such column`) only shows in `.hub/check-app.log`. Show
  the last exception from the log or point to the file.
- **P9 — fragment routes hard to test with `curl`** (405 on `/apps/…`, 403 without headers, 400 due to a short idempotency
  key). The messages are good; an example `curl` is missing from the guide.

## Difficulties — environment (document for the client's IT)

- RHEL 9 rootless delegates only `memory pids` → ask the admin for
  `/etc/systemd/system/user@.service.d/delegate.conf` with `[Service]\nDelegate=cpu cpuset io memory pids` (see P2).
- `loginctl enable-linger <user>` (without it the hub goes down on logout).
- The first start compiles MinIO/mc from source: ~5 min with 2 CPUs, CPU at 200%.
- `rockylinux:9` image: `curl-minimal` conflicts with `curl` in `dnf install` (do not install `curl`).

## Test artifacts

Outside the repository (session scratchpad): logs `rhel/a-install*.log`, `rhel/b-install.log`, Haiku reports
`haiku-rhel-a/RELATORIO.md` and `haiku-rhel-b/RELATORIO.md`. Scenario A was removed; B (`rhel-b`, port
127.0.0.1:28090) was left running for review.
