# F1f — quality gates and core fixes

- Updated standalone `uv` to **0.12.23**; `platform-py/Dockerfile` uses the same version. `uv audit` passed for `platform-py` (40 packages) and SDK (48 packages), with no known vulnerabilities or adverse statuses.
- Ruff Bandit `S` is enabled across hub, SDK, Contratos and Coletor. Removed global `S101`/`S608` ignores; test-only `S101` and justified line-level suppressions remain. Ruff passes in all four projects.
- Added `pytest-cov`; `check.sh` runs `uv audit` and measures auth, vault, state, admin and gateway. Disposable Postgres run: Ruff, format, `ty` and audit passed; **57 passed**, coverage **84.27%** combined (auth 86%, vault 80%, state 82%, admin 81%, gateway 90%). `hubpy-testdb` stopped.
- Fixed app-scope RBAC so the home page is not treated as an app ID; empty `admin.json` role lists now use built-in roles. Added auth/admin/state regression coverage.
- Login head now matches Rust. Parity: `/login` PASS (0 structural diffs, 0% pixels); `/` returns 200, with expected F2 content differences (251 structural nodes, 3.29%/8.95% pixels).
- Rebuilt only `hubpy-web`; `/healthz` returned `{"status":"ok"}`. Rust worktree/containers and `platform/demo-apps-py/ativos/` untouched.
- Heavy audit: run `uv audit` in each Python project with a `uv.lock`; projects without a uv lock need one before they can use this command.
