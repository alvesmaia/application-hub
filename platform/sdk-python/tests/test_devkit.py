"""Developer kit: portable SQL layer (SQLite) and local storage for `hub dev`."""

from pathlib import Path

import pytest
import sqlalchemy as sa

from hub_sdk import db, storage


def test_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("HUB_DB_URL", raising=False)
    assert db.database_url() == "sqlite:///.hub/dev.db"
    monkeypatch.setenv("HUB_DB_URL", "postgres://u:p@h/d")
    assert db.database_url() == "postgresql+psycopg://u:p@h/d"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/x")
    assert db.database_url() == "postgresql+psycopg://u@h/x"


def test_engine_and_alembic_upgrade(tmp_path: Path) -> None:
    mig = tmp_path / "migrations"
    (mig / "versions").mkdir(parents=True)
    (mig / "env.py").write_text(
        "from alembic import context\nimport sqlalchemy as sa\n"
        "cfg = context.config\n"
        "eng = sa.create_engine(cfg.get_main_option('sqlalchemy.url'))\n"
        "with eng.connect() as c:\n"
        "    context.configure(connection=c, target_metadata=None)\n"
        "    with context.begin_transaction():\n"
        "        context.run_migrations()\n"
    )
    (mig / "versions" / "0001.py").write_text(
        "from alembic import op\nimport sqlalchemy as sa\nrevision = '0001'\ndown_revision = None\n"
        "def upgrade():\n    op.create_table('t', sa.Column('id', sa.Integer, primary_key=True))\n"
    )
    url = f"sqlite:///{(tmp_path / 'sub' / 'a.db').as_posix()}"
    db.upgrade(mig, url)
    with db.engine(url).connect() as c:
        assert c.execute(sa.text("select count(*) from t")).scalar() == 0


async def test_local_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_STORAGE_LOCAL_DIR", str(tmp_path))
    c = storage.connection("docs")
    assert isinstance(c, storage.LocalConnection)
    c = c.for_user("u")
    it = await c.upload("a/b.txt", b"oi", "text/plain")
    assert (it.path, it.size, it.folder) == ("a/b.txt", 2, False)
    assert await (await c.download("a/b.txt")).read() == b"oi"
    assert [i.name for i in await c.list("a")] == ["b.txt"]
    assert (await c.meta("a/b.txt")).name == "b.txt"
    assert (await c.move("a/b.txt", "c.txt")).path == "c.txt"
    await c.delete("c.txt")
    assert await c.list("nada") == []
    for bad in (c.download("x"), c.meta("x"), c.move("x", "y")):
        with pytest.raises(storage.StorageError):
            await bad
    with pytest.raises(storage.StorageError):
        await c.upload("../x", b"", "text/plain")
