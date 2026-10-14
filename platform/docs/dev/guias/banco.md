# Database: SQLite and Postgres

The same code runs on both: **SQLite** in `.hub/dev.db` under `hub dev` and **Postgres** in the hub (database and role
of its own per app and environment, provisioned by `requires.database`).

```python
from hub_sdk import db

engine = db.engine()
db.upgrade("src/estoque/migrations")
```

`db.engine()` uses the hub URL (`HUB_DB_URL`) and, in dev, `.hub/dev.db`; `db.upgrade` applies the Alembic migrations.

## Rules

- Schema only through Alembic migration in `src/<pacote>/migrations/versions/` (applied at startup).
- SQLAlchemy Core with parameters; no SQL built from user text.
- Avoid features exclusive to one of the databases; if you need one, test on both.
- Another local database: `hub project create … --db postgresql://…` (goes to `.env` as `DATABASE_URL`).
