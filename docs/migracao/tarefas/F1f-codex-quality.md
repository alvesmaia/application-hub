Use ponytail (minimal code) and caveman (short report). Use ai-memory MCP if useful.

TASK F1f — quality gates + two core fixes (worktree = current dir, branch stack/python). Read docs/PLANO-MIGRACAO-PYTHON.md and docs/migracao/status/*.md first. Another agent is writing `platform/demo-apps-py/ativos/` — do not touch it.

1. Update uv: `uv self update` (current 0.8.17 is vulnerable: CVE-2025-54368, CVE-2025-13327). Record the new version.
2. ruff: enable the `S` rule group (flake8-bandit) in `platform-py/pyproject.toml` (and the same in platform/sdk-python and the Python apps contratos/coletor pyprojects); fix findings properly (no blanket ignores; per-line `# noqa: S...` only with a justification comment; tests may ignore S101).
3. Coverage: add `pytest-cov`; `check.sh` runs pytest with coverage; fail under 80% for the core packages (hub/auth, hub/vault, hub/state, hub/admin, hub/gateway); report totals in the status.
4. Vulnerabilities: add `uv audit` (requires the updated uv) to `check.sh` (lockfile of platform-py) and document it for the heavy end-of-day run (all Python projects).
5. Fix found by the parity harness (`node platform-py/parity/compare.mjs / /login`, read platform-py/parity/README.md; run with MSYS_NO_PATHCONV=1):
   - `/` returns 403 "Acesso negado" for admin on the Python hub — RBAC/route guard bug: admin must see Início (placeholder content is fine for now).
   - `/login` head differs from Rust: missing `<meta name="color-scheme" content="light">` and different `<meta>` order — make it identical.
   Re-run the harness: /login must PASS (0 structural, ≤0.5% pixels); `/` must return 200 (content diff expected until F2).
6. Update docs/PLANO-MIGRACAO-PYTHON.md "Decisões fixas" (ruff S rules, pytest-cov ≥80% core, uv audit).
Rules: check.sh green; throwaway Postgres `hubpy-testdb` as before (stop at end); don't touch the Rust worktree/containers; restart `hubpy-web` if deps change (`docker compose -f platform-py/docker-compose.yml up -d --build hubpy-web`) and confirm healthz + login. Commit prefix `feat(python): F1f`, trailer `Co-Authored-By: Codex <noreply@openai.com>`, push. Status: docs/migracao/status/F1f.md.
