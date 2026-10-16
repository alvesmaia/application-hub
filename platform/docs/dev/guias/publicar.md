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

## Without access to the hub: manual import

No token for the CLI? Generate the package and import it on the hub's Deployments screen. It runs the same pipeline
and provisioning as `hub deploy`.

```bash
hub pack --build-dir .
```

Whoever imports needs the **Edit platform settings** (Editar configurações da plataforma) permission in the hub. Step by step:

1. **Open the screen.** Operations › Deployments (Operações › Implantações), tab **Import package** (Importar pacote),
   or go straight to [@HUB@/admin/deploys?tab=import](@HUB@/admin/deploys?tab=import).
2. **Choose the `.hubapp`.** Drag the file onto the box or click it to pick one. Nothing is deployed yet: the hub only
   reads the package's `manifest.json` and shows a **Package preview** (Prévia do pacote):
   - application (name and id) and version;
   - status: *New app: first deployment in the hub*, *Updates from 1.2.0 to 1.3.0* (the version active in the chosen
     environment) or *First version in this environment*;
   - what the hub provisions (`requires.database`, `requires.storage`).

   An invalid manifest shows up right here (*Invalid package: …*) and the deploy button stays disabled. Fix the
   manifest, run `hub pack` again and pick the new file.
3. **Fill in the variables and secrets.** There is one field per name in `requires.variables` and `requires.secrets`.
   Leave a field **empty to keep the value already stored in the hub** (on the first deployment, an empty field means
   the app starts without it). Secrets are typed in password fields: the hub stores them in the vault, never shows them
   back, never returns them in any API and never writes the value to the logs or the audit trail.
4. **Choose the environment.** The button says what it will do:
   - **Deploy to staging** (Implantar em staging): validates and deploys right away (blue/green).
   - **Send for approval (production)** (Enviar para aprovação (produção)): validates and records the version as
     *Awaiting approval*; nothing changes in production yet.
5. **Follow the pipeline.** The **Validation pipeline** panel next to it lights up the 7 steps (Manifest, Signature,
   Security, Contract, Front-end, Smoke test, Approval) as the job advances. A failed step shows the reason and the
   package is rejected (it appears in History as *Rejected*). The page reloads at the end. The same job can be read at
   `GET /api/ops/jobs/{id}`.
6. **Staging.** Open the app in staging from the hub's environment selector, or from the version in **History**
   (Histórico) › **View in staging** (Ver em staging). When it is good, **Promote** (Promover) sends the same version
   to production, waiting for approval.
7. **Production.** In **History**, open the app, pick the version waiting for approval and **Approve** (Aprovar). A
   package with its own JavaScript also needs **Approve JavaScript** (Aprovar JavaScript) by another person first.
8. **Rollback.** In **History**, on the version active in production, **Rollback** goes back to the previous one; on an
   older version, **Go back to this version** (Voltar para esta versão). Both use the same blue/green path.

The deployment always uses the `manifest.json` **inside the package**. The **Validate a manifest.json** box in the
**Pipeline and CI** (Pipeline e CI) tab only checks a text you paste there; it does not change the package.

The package can be up to **20 MB**. It carries the manifest, the `openapi.json`, the `ui/` folder and the build context
(`build/**`) — **not** the dependencies: the `Dockerfile` installs them during the build in the hub. If it exceeds the
limit, `hub pack` lists the largest files; the common case is a `.venv/` or a `vendor/` copied into
`build/` (add it to `.dockerignore`).

## Exit codes

`0` ok · `1` package rejected · `2` validation · `3` token refused · `4` hub unreachable.
