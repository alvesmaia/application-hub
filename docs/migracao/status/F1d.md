# F1d — Admin core (partial)

Added `platform-py/hub/admin/chain.py` and `store.py`:

- Rust-shaped JSONL entry/hash and `head.json` signing; protected vault anchor checks.
- `admin.json` read/write preserving unknown Rust fields; sealed sensitive environment values; shared file locks through `hub/state`.
- Auth success/logout/token create/revoke audit hooks with trusted-proxy client IP.
- Bounded in-memory log bus and durable outbox pending-ack state.

Checks: `platform-py/scripts/check.sh` passed against disposable `hubpy-testdb`: Ruff, format, mypy strict, 49 tests passed. `hubpy-testdb` stopped. Rust source and Rust hub containers were not modified.

**Not complete / do not treat as F1d-ready:** Python chain does not yet support Rust archives/manifest, checkpoints, interrupted archive recovery, retention, signed external publication (`HUB_AUDIT_HEAD_URL`/`HUB_AUDIT_HEAD_FILE`), or full Rust verifier parity. The chain test uses Rust-shaped records but is not a Rust-generated golden/round-trip test. Admin store ports basic file/secrets handling only; settings/roles/health CRUD and app outbox fetch → audit/inbox → HTTP ack are not wired. Log filtering/stream API is also absent. Keep Rust hub authoritative until these gaps and the cross-language tests are closed.
