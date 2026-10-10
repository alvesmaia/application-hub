# F1e — tooling and typed models

- Replaced mypy with Astral `ty`; `[tool.ty.rules] all = "error"`, Python 3.12. `check.sh`: Ruff lint, format check, ty, pytest.
- Side-by-side baseline: mypy strict and default ty both found 0 issues on `hub/`. Ty's all-error pass found 6 stricter findings (FastAPI/asyncpg dynamic values, one override/source typing); fixed all. No critical mypy-only finding.
- Added Pydantic v2 models for auth records, keyring, admin document, apps/manifest and audit entries. Validate loaded documents and login/token/admin/audit boundaries; serializers keep existing dictionaries and field order. The audit fixture digest is pinned and unchanged.
- `pydantic-settings` loads `HUB_CONFIG_FILE` TOML with `HUB_*` environment overrides. Plan decisions updated.
- `bash scripts/check.sh` with disposable `hubpy-testdb`: Ruff, format, ty passed; **51 tests passed**. Container stopped.

F1d remains partial per its status: Rust archive/publication and cross-language audit verification are open.
