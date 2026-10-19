# Installing on a corporate network

For servers where the internet goes through a mandatory proxy (often one that inspects TLS), where packages and
images come from internal mirrors (Artifactory, Nexus), and where the user has no root and sometimes no systemd user
session. Everything below is done once, at install time; after that developers publish with `hub deploy` and nobody
needs to log in to the server.

## 1. Diagnose first

```bash
bash platform-py/scripts/install.sh --doctor [the network options below]
```

Read-only: it changes nothing. It tests PyPI, the image registry, GitHub, Alpine and Go **through the configured path
only** (with a proxy it never tries to go around it, which a firewall would log as a bypass attempt), detects a proxy
that inspects TLS and shows the certificate issuer, checks the corporate CA and the mirrors, runs a tiny build with the
same settings to prove they reach inside a container, and checks the disks, the port and other projects on the same
engine. Each failure names the cause and the option that fixes it, for example:

```
  ✘ PyPI: certificado não confiável (emissor: CN=Corp Web Gateway); o proxy deve inspecionar TLS
      → informe a CA corporativa: --ca-file <arquivo.pem> (peça à TI a CA desse emissor)
```

The install runs the same diagnosis as step 0 and stops on a failure (`--skip-doctor` to go on anyway).

## 2. Network options

| Option | What it does |
|---|---|
| `--proxy http://[user:pass@]host:port` | downloads during the builds (hub, apps, MinIO) and the hub's external calls (Entra, SharePoint, S3, webhooks, update check) |
| `--no-proxy .empresa.local,10.0.0.0/8` | destinations reached directly (mirrors, internal services) |
| `--ca-file ca.pem` | corporate CA (the TLS-inspecting proxy): trusted in every build, in the hub and in the app containers |
| `--pypi-index https://repo.empresa.local/api/pypi/pypi/simple` | PyPI mirror (pip and uv) |
| `--registry-mirror docker.repo.empresa.local` | Docker Hub mirror: images become `HOST/library/…` |
| `--apk-mirror https://repo.empresa.local/alpinelinux` | Alpine mirror (MinIO build) |
| `--goproxy https://repo.empresa.local/api/go/go` | Go module mirror (MinIO build) |

The interactive install asks the same ("Rede corporativa com proxy, CA própria ou espelhos internos?"), starting from
the proxy already in the shell's environment. The values go to `.prod.env` (`HUB_PROXY`, `HUB_NO_PROXY`,
`HUB_CA_FILE`/`HUB_CA_DIR`, `HUB_PYPI_INDEX`, `HUB_IMAGE_PREFIX`, `HUB_APK_MIRROR`, `HUB_GOPROXY`); the CA is copied
to `.secrets/ca/ca.pem`. A proxy password is never shown on the screen, in the build logs or in the saved command.

How the hub uses them, with no step per app:

- **Internal calls never use the proxy**: the gateway to the apps, MinIO and the hub's own services go direct, whatever
  the app's name. Only external destinations use the proxy (hosts without a dot, private addresses and `--no-proxy`
  matches go direct). The `HTTP_PROXY` that docker injects into every container is ignored by the hub's clients.
- **Apps sent by developers**: their build gets the proxy, the CA and the mirrors as build args (the `hub-cli` template
  Dockerfile declares them); their containers get the proxy, with the hub's service names in `NO_PROXY`, and the CA
  bundle (`SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`). An app's own values win.
- **Updates**: `hub_update.py --build` reads the same settings from `.prod.env`.

## 3. Rootless Podman

- **With a systemd user session**: the install enables `podman.socket` and `podman-restart.service`, so the hub comes back
  after a reboot (`--autostart none` to skip).
- **Without systemd** (or `--no-systemd`): the hub runs its own API socket (cgroupfs, healthchecks without systemd timers)
  and an `@reboot` line in the user's crontab runs `scripts/hub-start.sh`. Manual commands on that engine:
  `source platform-py/.secrets/hub-env.sh`.
- **systemd present but the user session missing** (no `$XDG_RUNTIME_DIR/bus`, even with linger): the container networks
  cannot start their DNS. The install stops before changing anything and prints what to ask the administrator:
  `sudo loginctl enable-linger <user>` and `sudo systemctl restart user@<uid>.service` — or a dedicated user for the hub.

## 4. Disk

The interactive install lists the disks the user can write to with their free space and recommends the one with more
space when the engine's disk is tight.

- `--data-dir /data/app-hub`: the hub and app databases, the hub data and the MinIO files go to host folders there
  (`:Z` under SELinux). The images stay in the engine.
- `--engine-store /data/app-hub-engine` (rootless Podman only): also the images, in a store of the hub's own, apart from
  the user's default Podman and from other projects (own socket, cron start, `hub-env.sh`).

Removing a data folder of a rootless install: the files belong to the user's sub-UIDs, so use
`podman unshare rm -rf <folder>`.

## 5. HTTPS and sign-in with Microsoft

Entra refuses `http://` redirect addresses (except localhost), so SSO needs HTTPS:

```bash
bash platform-py/scripts/install.sh --tls-cert hub.crt --tls-key hub.key --public-url https://hub.empresa.local:8443 …
```

The edge serves HTTPS on the hub port with the company certificate (copied to `.secrets/tls`, 0600). Register
`<public URL>/auth/entra/callback` in the app registration. Over plain `http://` (tests only) the install sets the
session cookie to `auto` and warns that passwords and sessions travel unencrypted.

## 6. Consent and the saved command

Before acting, the install lists what it will do (containers with the project prefix, engine socket access, downloads
and from where, proxy and CA, start after reboot, disk, port, secrets) and asks `Continuar? (s/N)`; `--yes` counts as
that consent. At the end, `.secrets/install-cmd.txt` holds this install as one command (proxy password masked) for the
next server.

## Example

```bash
bash platform-py/scripts/install.sh --yes \
  --admin-email ops@empresa.local --bind 0.0.0.0 --port 8090 \
  --public-url https://hub.empresa.local:8090 --tls-cert /etc/pki/hub.crt --tls-key /etc/pki/hub.key \
  --proxy http://proxy.empresa.local:8080 --no-proxy .empresa.local --ca-file /etc/pki/ca-empresa.pem \
  --pypi-index https://repo.empresa.local/api/pypi/pypi/simple --registry-mirror docker.repo.empresa.local \
  --apk-mirror https://repo.empresa.local/alpinelinux --goproxy https://repo.empresa.local/api/go/go \
  --data-dir /data/app-hub
```
