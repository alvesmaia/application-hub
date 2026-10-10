# Banco: SQLite e Postgres

O mesmo código roda nos dois: **SQLite** em `.hub/dev.db` no `hub dev` e **Postgres** no hub (banco e papel
próprios por app e ambiente, provisionados por `requires.database`).

```python
from hub_sdk import db

engine = db.engine()
db.upgrade("src/estoque/migrations")
```

`db.engine()` usa a URL do hub (`HUB_DB_URL`) e, no dev, `.hub/dev.db`; `db.upgrade` aplica as migrações Alembic.

## Regras

- Esquema só por migração Alembic em `src/<pacote>/migrations/versions/` (aplicadas na subida).
- SQLAlchemy Core com parâmetros; nada de SQL montado com texto do usuário.
- Evite recursos exclusivos de um dos bancos; se precisar, teste nos dois.
- Outro banco local: `hub project create … --db postgresql://…` (vai para `.env` como `DATABASE_URL`).
