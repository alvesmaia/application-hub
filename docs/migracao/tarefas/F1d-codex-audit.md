Use ponytail (minimal code) and caveman (short report). Use ai-memory MCP for context if useful.

TASK F1d — port the audit chain and admin store core to Python (worktree = current dir, branch stack/python). Read `docs/PLANO-MIGRACAO-PYTHON.md` and `docs/migracao/status/{F0,F1a,F1b,F1c}.md` first. Reuse hub/state, hub/vault, hub/auth.

Scope (`platform/crates/hub-admin`):
1. `hub/admin/chain.py`: hash-chained audit log, SAME on-disk/segment format, hash algorithm, sequence numbers, anchor/head anti-rollback (HUB_AUDIT_HEAD_URL / HUB_AUDIT_HEAD_FILE publication), verification, concurrent-writer safety with hub/state locks (multi-replica). Golden test: a chain written by the Rust hub (generate with the Rust code/tests, or use an existing fixture) verifies in Python, and a Python-appended entry verifies with the Rust verifier (`cargo test -p hub-admin` helper or `scripts/audit-verify-offsite.sh` logic).
2. `hub/admin/store.py`: admin.json store (env vars/variables with secrets in vault, roles/settings parts not covered by F1c, health config), same format + file coherence; log bus (`logs.rs`, WITHOUT the removed seed/simulator) and outbox ingestion core (`outbox.rs`: apps' events → audit/inbox, idempotent, acked).
3. Wire: every auth event from F1c (login/logout/token create/revoke) is audited; audit IP via trusted proxy.
Tests: port Rust hub-admin unit tests for chain/store/outbox + golden compatibility tests. Rules: `platform-py/scripts/check.sh` passes; throwaway Postgres as before (`hubpy-testdb`, stop at end); never touch the Rust hub dir/containers; commit prefix `feat(python): F1d`, trailer `Co-Authored-By: Codex <noreply@openai.com>`, push. Status: `docs/migracao/status/F1d.md`.
