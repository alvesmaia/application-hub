# F-cli — CLI `hub` em Python

- Pacote `platform-py/cli/` (uv, `hub_cli`, argparse + httpx; atalho `platform-py/cli/hub`).
- `hub pack`: mesmo formato do Rust (manifest, runtime.json, openapi.json, ui/**, checksums.txt); validado por `hub.ops.pipeline.read_zip/_checksums`. Zip determinístico (mtime fixo).
- `hub mcp`: ponte stdio↔HTTP completa (token por arquivo/env/flag, HTTPS ou loopback, sessão, SSE, cabeçalhos 2026-07-28, código 3 se MCP desligado).
- `hub dev`: apenas stub (exit 2) — depende do shell/gateway; fora do escopo.
- Rust nunca teve deploy/logs/status/tokens: não portados (a API existe).
- ruff (S), ty, pytest 9 testes, cobertura 99%; smoke contra hubpy :8090 (token inválido → erro JSON-RPC; com `HUBPY_TOKEN` faz initialize+tools/list).
- Scripts atualizados: `sample-packages.sh`, `ci-publish.sh`, `ci-local.sh` (etapa ci-publish). `dev-app.sh`/`release.sh` ainda citam hub-cli Rust (dev/binário de release).
