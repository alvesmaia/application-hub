# Contributing

- **Branches:** `main` is the official stack (Python: hub in `platform-py/`, apps/SDK in `platform/`); `stack/python` is the working branch.
- **Checks:** in `platform-py/`, `uv run hub-check --all` must be green before opening a PR (the pre-push hook runs the full check). If you changed the UI: ui-sweep (`platform/tests-e2e`, `node ui-sweep/run.mjs --quick`).
- **Commits:** Conventional Commits with a scope, e.g. `feat(hub): …`, `fix(coletor): …`, `docs: …`.
- New user-facing text goes into the i18n catalog in both languages (pt-BR, en) (`platform-py/hub/web/catalog.json`).
- Vulnerabilities: see [SECURITY.md](SECURITY.md), do not open a public issue.
- License not yet defined: by contributing, you agree that your contribution may be relicensed under the open license that is eventually chosen.

## Contributing (English)

`main` is the official Python stack (`platform-py/` hub, `platform/` apps and SDK). Run `uv run hub-check --all` in `platform-py/` before opening a PR. Use Conventional Commits
with a scope. Report vulnerabilities privately (see [SECURITY.md](SECURITY.md)). The license is not yet defined;
contributions may be relicensed under the open license eventually chosen.
