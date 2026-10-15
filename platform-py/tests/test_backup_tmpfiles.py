"""What the backups run in the shared app-db container.

- Scheduled backups run the apps at the same time: each `psql -f` file must have its own name, or one app runs another
  app's SQL (HUB-WRONG-DB) or finds it already removed (No such file).
- The dump carries no privileges: production's grants (`ALTER DEFAULT PRIVILEGES FOR ROLE <prod role>`) made a clone to
  staging fail with "permission denied to change default privileges" when the admin is not a superuser."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from hub.ops import backups as backups_mod
from hub.ops.backups import Backups


class FakeSecrets:
    def secret(self, name: str, rotate: bool = False) -> str:
        return "Fake0Secret0Password0" + name.rsplit(":", 1)[-1]


class RecordingExec:
    """Stands in for `docker exec`: records the files each call copies in and the file `psql -f` reads."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    async def __call__(
        self, engine: Any, container: str, cmd: list[str], files: dict[str, bytes] | None = None, **kw: Any
    ) -> tuple[int, bytes]:
        path = next(iter(files or {""}))
        await asyncio.sleep(0)  # let the other backup interleave, as the scheduler does
        self.calls.append((cmd, path))
        return 0, b""


def backups(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Backups, RecordingExec]:
    rec = RecordingExec()
    monkeypatch.setattr(backups_mod, "exec_in", rec)
    store: Any = FakeSecrets()
    unused: Any = None  # engine, object store and app env are not reached here
    return Backups(store, unused, unused, tmp_path, unused), rec


def test_dump_leaves_out_privileges(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    b, rec = backups(monkeypatch, tmp_path)
    asyncio.run(b._dump_db("ativos", lambda pct, msg: None))
    dump = next(cmd for cmd, _ in rec.calls if cmd[0] == "pg_dump")
    assert "--no-privileges" in dump and "--no-owner" in dump


def test_concurrent_backup_roles_use_distinct_sql_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    b, rec = backups(monkeypatch, tmp_path)

    async def both() -> None:
        await asyncio.gather(b.ensure_pg_role("ativos"), b.ensure_pg_role("servicos"))

    asyncio.run(both())
    paths = [p for _, p in rec.calls]
    assert len(set(paths)) == 2, paths
    for cmd, path in rec.calls:
        assert cmd[-1] == path and path.endswith(".sql")
