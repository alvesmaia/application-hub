# F6d — conclusão técnica

- Conformidade HTTP aprovada: ativos 28/2 skipped; fornecedores 25/5; servicos 26/4; contratos 30; coletor 25/5.
- Corrigido `ATIVOS_HOST` no Dockerfile do Ativos. Apps alcançáveis pelas redes de compose.
- Samples/OpenAPI e registros hub atualizados para manifests Python; `sample-packages.sh` empacota os cinco usando `hub-<app>`.
- Extensão compose Python adiciona cinco `hubpy-app-<id>` (256 MiB cada), redes isoladas e DB de teste. `dev.sh up` carrega `apps.json` via `HUB_APPS_FILE` sem tocar compose/base protegidos.
- Compose base/extensão Python e compose/dev Rust validados; `cargo metadata` lista o hub e crates restantes, sem SDK/demo Rust.
- Removidos crate Rust `hub-sdk`, crate `demo-apps`, Dockerfile/scripts e integrações CI/build. Hub Rust preservado como referência.
- Login no hub Python em :8090; `/apps/{id}` respondeu HTTP 200 para os cinco.
- Ruff, ty e pytest/cov ≥80% de cada app já constavam aprovados no status anterior.
- Não alterei arquivos protegidos da F3a em `platform-py`. A captura por Edge/Chrome headless não foi gerada neste ambiente; sem screenshot anexado.

Commit e push pendentes.
