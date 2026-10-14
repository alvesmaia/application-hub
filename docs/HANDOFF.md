# Handoff — Application Hub (updated 08/10/2026)

## Where everything is
- **`main`** (and `stack/python`, the working branch): official Python stack. Hub in `platform-py/`; apps, SDK,
  shared front-end and tests in `platform/`; original static mockup at the root.
- **Read:** `AGENTS.md` (commands and guardrails), `docs/PENDENCIAS.md` (status and open items),
  `platform/docs/CONTRATO-APP.md`.

## Layout
- `platform-py/hub` — FastAPI hub (templates in `hub/web/templates`, i18n catalog in `hub/web/catalog.json`).
- `platform-py/cli` — `hub-cli` (wheel via `scripts/build-cli.sh`, embeds the shell assets).
- `platform/assets/shell` — shell CSS/JS (`hub*.js`, `tokens.css`, fonts, `vendor/` ZXing), served at `/assets/`.
- `platform/assets/web` — JS/CSS of the hub screens (Administration, Operations, Connections, UX).
- `platform/assets/i18n` — `catalog.json`/`allowlist.json` used by the JS tests.
- `platform/config` — app registry (`apps.json`, `apps.compose.json`), seeds `app-env.compose.json`, `hub.example.toml`.
- `platform/demo-apps-py/*`, `platform/sdk-python`, `platform/conformance`, `platform/js-tests`, `platform/tests-e2e`.

## How to run (in `platform-py/`)
- Check: `uv run hub-check --all` (mandatory before commit/push; the pre-push hook runs the full check).
- Stack: `docker compose -f docker-compose.yml -f docker-compose.apps.yml up -d --build` → `http://127.0.0.1:8090`
  (dev secrets in `.dev.env`, gitignored). Dev mode: `bash scripts/dev.sh up|down|status|logs|reload-on|reload-off`.
- Production: `docs/IMPLANTACAO.md` (overlay `docker-compose.prod.yml` + `.prod.env`).
- JS tests: `cd platform && node --test js-tests/`. E2E/ui-sweep: `cd platform/tests-e2e && npx playwright test`,
  `node ui-sweep/run.mjs --quick` (against :8090 or the temporary instance `scripts/tmp-instance.sh`).
- App conformance: `bash platform-py/scripts/conformance.sh`.
- Palette/theme: `platform/scripts/brand-palette.json` → `node platform/scripts/gen-dark-theme.mjs` (generates blocks of `platform/assets/shell/tokens.css`).

## Cautions
- The hub only manages `app-hub-*` containers; never touch containers of other projects or kill processes by image name.
- Public tunnel `apphub-devtunnel`: never stop/remove it without the owner's request.
- Secrets never in HTML/API/log/audit trail/git. One heavy run at a time.
