# Contribuindo

- **Branches:** `main` é a stack oficial (Python: hub em `platform-py/`, apps/SDK em `platform/`); `stack/python` é a
  branch de trabalho.
- **Checagem:** em `platform-py/`, `uv run hub-check --all` verde antes de abrir o PR (o hook pre-push roda a completa).
  Mudou UI: ui-sweep (`platform/tests-e2e`, `node ui-sweep/run.mjs --quick`).
- **Commits:** Conventional Commits com escopo, ex. `feat(hub): …`, `fix(coletor): …`, `docs: …`.
- Texto novo voltado ao usuário entra no catálogo de i18n nos 2 idiomas (pt-BR, en) (`platform-py/hub/web/catalog.json`).
- Vulnerabilidades: veja [SECURITY.md](SECURITY.md), não abra issue pública.
- Licença ainda não definida: ao contribuir, você concorda que a contribuição poderá ser licenciada
  sob a licença aberta que vier a ser escolhida.

## Contributing (English)

`main` is the official Python stack (`platform-py/` hub, `platform/` apps and SDK). Run `uv run hub-check --all` in `platform-py/` before opening a PR. Use Conventional Commits
with a scope. Report vulnerabilities privately (see [SECURITY.md](SECURITY.md)). The license is not yet defined;
contributions may be relicensed under the open license eventually chosen.
