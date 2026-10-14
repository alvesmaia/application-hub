# Publish to staging and production

## Staging

```bash
hub login --hub @HUB@
hub deploy --env staging --yes
```

The hub validates the package (manifest, contract, image), provisions what the manifest asks for in `requires` and deploys
blue/green. Open the app in staging from the hub's environment selector.

## Production

```bash
hub deploy --env production --yes
```

The version stays **waiting for approval** on the Deployments screen; a hub operator approves it.

## Variables and secrets

The values of `requires.variables` and `requires.secrets` come from `deploy.<env>.env` (or are asked for; secrets without echo). The
CLI only shows the names, never the values. Do not commit these files.

## Without access to the hub

```bash
hub pack --build-dir .
```

Generates the `.hubapp` for manual upload on the Deployments screen (same pipeline and provisioning).

The package can be up to **20 MB**. It carries the manifest, the `openapi.json`, the `ui/` folder and the build context
(`build/**`) — **not** the dependencies: the `Dockerfile` installs them during the build in the hub. If it exceeds the
limit, `hub pack` lists the largest files; the common case is a `.venv/` or a `vendor/` copied into
`build/` (add it to `.dockerignore`).

## Exit codes

`0` ok · `1` package rejected · `2` validation · `3` token refused · `4` hub unreachable.
