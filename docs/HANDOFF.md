# Handoff — Application Hub (atualizado em 08/10/2026)

## Onde está cada coisa
- **`main`** (e `stack/python`, branch de trabalho): stack oficial em Python. Hub em `platform-py/`; apps, SDK,
  front-end compartilhado e testes em `platform/`; mockup estático original na raiz.
- **`stack/rust-topcoat`**: hub Rust + Topcoat **arquivado** (referência histórica). Docs da era Rust:
  `docs/migracao/historico-rust/` (HANDOFF, PENDENCIAS, IMPLANTACAO, README da plataforma).
- **Leia:** `AGENTS.md` (comandos e guardrails), `docs/PENDENCIAS.md` (status e itens em aberto),
  `docs/PLANO-MIGRACAO-PYTHON.md`, `docs/migracao/status/*.md`, `platform/docs/CONTRATO-APP.md`.

## Layout
- `platform-py/hub` — hub FastAPI (templates em `hub/web/templates`, catálogo i18n em `hub/web/catalog.json`).
- `platform-py/cli` — `hub-cli` (wheel por `scripts/build-cli.sh`, embute os assets do shell).
- `platform/assets/shell` — CSS/JS do shell (`hub*.js`, `tokens.css`, fontes, `vendor/` ZXing), servido em `/assets/`.
- `platform/assets/web` — JS/CSS das telas do hub (Administração, Operações, Conexões, UX).
- `platform/assets/i18n` — `catalog.json`/`allowlist.json` usados pelos testes JS.
- `platform/config` — registro de apps (`apps.json`, `apps.compose.json`), sementes `app-env.compose.json`, `hub.example.toml`.
- `platform/demo-apps-py/*`, `platform/sdk-python`, `platform/conformance`, `platform/js-tests`, `platform/tests-e2e`.

## Como rodar (em `platform-py/`)
- Checagem: `uv run hub-check --all` (obrigatória antes de commit/push; o pre-push roda a completa).
- Pilha: `docker compose -f docker-compose.yml -f docker-compose.apps.yml up -d --build` → `http://127.0.0.1:8090`
  (segredos de dev em `.dev.env`, gitignored). Modo dev: `bash scripts/dev.sh up|down|status|logs|reload-on|reload-off`.
- Produção: `docs/IMPLANTACAO.md` (overlay `docker-compose.prod.yml` + `.prod.env`).
- Testes JS: `cd platform && node --test js-tests/`. E2E/ui-sweep: `cd platform/tests-e2e && npx playwright test`,
  `node ui-sweep/run.mjs --quick` (contra a :8090 ou a instância temporária `scripts/tmp-instance.sh`).
- Conformidade dos apps: `bash platform-py/scripts/conformance.sh`.
- Paleta/tema: `platform/scripts/brand-palette.json` → `node platform/scripts/gen-dark-theme.mjs` (gera blocos de `platform/assets/shell/tokens.css`).

## Cuidados
- O hub só gerencia contêineres `app-hub-*`; nunca tocar em contêineres de outros projetos nem encerrar processos por nome de imagem.
- Túnel público `apphub-devtunnel`: nunca parar/remover sem pedido do dono.
- Segredos nunca em HTML/API/log/auditoria/git. Uma execução pesada por vez.
