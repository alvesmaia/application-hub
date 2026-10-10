"""Onda 12 (2.9): sagas e envios em duas fases — mesma semântica do SDK Rust.

Sem Postgres: regra de próximo passo pelos vetores compartilhados
(vetores do Rust arquivado em ``stack/rust-topcoat``), estado/resumo e o esquema igual ao do Rust.
Com Postgres (``@db``): compensação em ordem inversa, retomada depois de queda (para a
frente e no meio da compensação) com efeito exatamente uma vez, nova tentativa e
compensação na última tentativa; envio em duas fases com transação desfeita e limpeza
(com um armazenamento em memória). MinIO real: ``test_wave12_uploads_minio.py``.
"""

from __future__ import annotations

import asyncio
import builtins
from pathlib import Path
from typing import Any

import pytest
from typing_extensions import override

from hub_sdk.jobs import JobFail, JobRetry, Jobs, JobSpec, run_claimed
from hub_sdk.saga import Saga, StepCtx, state_of, summary
from hub_sdk.shutdown import Shutdown
from hub_sdk.storage import Connection, Item, StorageError
from hub_sdk.uploads import Uploads, valid_name

PLATFORM = Path(__file__).resolve().parents[2]


def test_state_and_summary() -> None:
    cp = {"saga": {"v": 1, "state": "running", "steps": [{"name": "a", "state": "pending"}]}}
    assert state_of(cp, ["a"])["state"] == "running"
    with pytest.raises(JobFail):
        state_of(cp, ["b"])
    assert state_of(None, ["x", "y"])["steps"][1] == {"name": "y", "state": "pending"}
    assert summary(cp) == {"state": "running", "steps": [{"name": "a", "state": "pending"}], "reason": None}
    assert summary({"batch": 1}) is None


def test_file_names_are_single_safe_segments() -> None:
    for ok in ["contrato.pdf", "Relatório ação 2026.xlsx", "a_b-c.txt"]:
        assert valid_name(ok), ok
    for bad in ["", "../x", "a/b", ".env", "x ", "a?b"]:
        assert not valid_name(bad), bad


# ---- com Postgres -------------------------------------------------------------------

Log = list[str]


def spec(kind: str) -> JobSpec:
    return JobSpec(kind, max_attempts=3, lease=2, backoff_base=0.001, backoff_max=0.001)


def recorder(log: Log, fail: list[Exception] | None = None) -> tuple[Any, Any]:
    async def do(c: StepCtx) -> Any:
        log.append(f"do:{c.step}")
        if fail is not None and fail:
            raise fail.pop()
        return {"step": c.step, "key": c.key}

    async def undo(c: StepCtx) -> Any:
        log.append(f"undo:{c.step}")
        return None

    return do, undo


async def run_once(pool: Any, saga: Saga) -> Any:
    jobs = Jobs(pool)
    job = await jobs.claim([saga.spec])
    assert job is not None
    await run_claimed(jobs, saga, job, Shutdown())
    return await jobs.get(job.id)


async def runnable(pool: Any, job_id: str) -> None:
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE hub_jobs SET run_after = now() - interval '1 second', "
            "lease_until = now() - interval '1 second', attempt_token = NULL WHERE id = $1",
            job_id,
        )


@pytest.mark.db
async def test_completes_and_compensates_in_reverse(pool: Any) -> None:
    log: Log = []
    s = Saga(spec("p.ok"))
    for n in ("a", "b", "c"):
        s.step(n, *recorder(log))
    job, _ = await Jobs(pool).enqueue(s.spec, {"name": "Notebook"}, "ana")
    j = await run_once(pool, s)
    assert j.status == "done", j.error
    assert log == ["do:a", "do:b", "do:c"]
    assert j.checkpoint["saga"]["steps"][1]["output"]["key"] == f"{job.id}:b"
    assert j.result["outputs"]["c"]["step"] == "c"

    log2: Log = []
    s2 = Saga(spec("p.fail"))
    s2.step("a", *recorder(log2)).step("b", *recorder(log2))
    s2.step("c", *recorder(log2, [JobFail("destino recusou")])).step("d", *recorder(log2))
    await Jobs(pool).enqueue(s2.spec, {}, "ana")
    j = await run_once(pool, s2)
    assert j.status == "failed" and "saga compensada" in (j.error or "")
    assert log2 == ["do:a", "do:b", "do:c", "undo:c", "undo:b", "undo:a"]
    assert j.checkpoint["saga"]["state"] == "compensated"
    assert j.checkpoint["saga"]["steps"][3]["state"] == "pending"
    assert j.checkpoint["saga"]["reason"] == "c: destino recusou"


@pytest.mark.db
async def test_transient_retries_same_step_then_compensates_on_last_attempt(pool: Any) -> None:
    log: Log = []

    async def flaky(c: StepCtx) -> Any:
        log.append(f"do:{c.step}")
        raise JobRetry("503")

    async def undo(c: StepCtx) -> Any:
        log.append(f"undo:{c.step}")

    s = Saga(spec("p.retry")).step("a", *recorder(log)).step("b", flaky, undo)
    job, _ = await Jobs(pool).enqueue(s.spec, {}, "ana")
    assert (await run_once(pool, s)).status == "queued"
    await runnable(pool, job.id)
    assert (await run_once(pool, s)).status == "queued"
    await runnable(pool, job.id)
    j = await run_once(pool, s)
    assert j.status == "failed", j.error
    assert log == ["do:a", "do:b", "do:b", "do:b", "undo:b", "undo:a"]


@pytest.mark.db
async def test_crash_mid_saga_resumes_forward_exactly_once(pool: Any) -> None:
    async with pool.acquire() as c:
        await c.execute("CREATE TABLE assets (id bigserial PRIMARY KEY, name text NOT NULL)")
    calls: Log = []
    hang = asyncio.Event()

    async def create(c: StepCtx) -> Any:
        calls.append("criar")

        async def ins(conn: Any) -> Any:
            aid = await conn.fetchval("INSERT INTO assets (name) VALUES ($1) RETURNING id", c.input["name"])
            return {"asset": aid}

        return await c.once(ins)

    async def slow(c: StepCtx) -> Any:
        if not hang.is_set():
            await asyncio.sleep(3600)
        return "ok"

    s = Saga(spec("p.crash")).step("criar", create).step("anexar", slow)
    job, _ = await Jobs(pool).enqueue(s.spec, {"name": "Notebook"}, "ana")
    jobs = Jobs(pool)
    claimed = await jobs.claim([s.spec])
    assert claimed is not None
    t = asyncio.create_task(run_claimed(jobs, s, claimed, Shutdown()))
    for _ in range(100):
        j = await jobs.get(job.id)
        if j and j.checkpoint and j.checkpoint["saga"]["steps"][0]["state"] == "done":
            break
        await asyncio.sleep(0.05)
    t.cancel()  # o processo "cai"
    with pytest.raises((asyncio.CancelledError, Exception)):
        await t
    await runnable(pool, job.id)
    hang.set()
    j = await run_once(pool, s)
    assert j.status == "done", j.error
    assert calls == ["criar"], "o passo 1 não roda de novo"
    async with pool.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM assets") == 1


@pytest.mark.db
async def test_crash_during_compensation_resumes_compensating(pool: Any) -> None:
    log: Log = []
    hang = asyncio.Event()

    async def do_b(c: StepCtx) -> Any:
        log.append("do:b")

    async def undo_b(c: StepCtx) -> Any:
        log.append("undo:b")
        if not hang.is_set():
            await asyncio.sleep(3600)

    s = Saga(spec("p.comp")).step("a", *recorder(log)).step("b", do_b, undo_b)
    s.step("c", *recorder(log, [JobFail("x")]))
    job, _ = await Jobs(pool).enqueue(s.spec, {}, "ana")
    jobs = Jobs(pool)
    claimed = await jobs.claim([s.spec])
    assert claimed is not None
    t = asyncio.create_task(run_claimed(jobs, s, claimed, Shutdown()))
    for _ in range(100):
        j = await jobs.get(job.id)
        if j and j.checkpoint and j.checkpoint["saga"]["steps"][1]["state"] == "compensating":
            break
        await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises((asyncio.CancelledError, Exception)):
        await t
    await runnable(pool, job.id)
    hang.set()
    j = await run_once(pool, s)
    assert j.status == "failed" and j.checkpoint["saga"]["state"] == "compensated"
    assert log == ["do:a", "do:b", "do:c", "undo:c", "undo:b", "undo:b", "undo:a"]


class MemStore(Connection):
    """Armazenamento em memória (mesma interface das conexões)."""

    def __init__(self) -> None:
        super().__init__("mem", "s3")
        self.files: dict[str, bytes] = {}

    @override
    async def upload(self, path: str, data: Any, content_type: str, length: int | None = None) -> Item:
        self.files[path] = bytes(data)
        return Item(path=path, name=path.rsplit("/", 1)[-1], size=len(self.files[path]))

    @override
    async def meta(self, path: str) -> Item:
        if path not in self.files:
            raise StorageError(404, "não encontrado")
        return Item(path=path, name=path.rsplit("/", 1)[-1], size=len(self.files[path]))

    @override
    async def delete(self, path: str) -> None:
        if self.files.pop(path, None) is None:
            raise StorageError(404, "não encontrado")

    @override
    async def move(self, src: str, dst: str) -> Item:
        self.files[dst] = self.files.pop(src)
        return await self.meta(dst)

    @override
    async def list(self, prefix: str = "") -> builtins.list[Item]:
        return [Item(path=p, name=p.rsplit("/", 1)[-1]) for p in self.files if p.startswith(prefix + "/")]


@pytest.mark.db
async def test_two_phase_upload_commit_rollback_and_cleanup(pool: Any) -> None:
    store = MemStore()
    up = Uploads(pool, store, ttl=120)
    s = await up.begin("contrato.pdf", "ana")
    await up.put(s, b"%PDF ok", "application/pdf")
    assert "2026/contrato.pdf" not in store.files
    async with pool.acquire() as c, c.transaction():
        done = await up.commit(c, s.id, "2026/contrato.pdf", "ana")
    assert done.promote_job.startswith("job-")
    assert await up.location(s.id) == s.staging_path
    await up.promote(s.id)
    await up.promote(s.id)
    assert await up.location(s.id) == "2026/contrato.pdf"
    assert store.files["2026/contrato.pdf"] == b"%PDF ok"

    # Transação desfeita: continua pendente e sem promoção.
    s2 = await up.begin("rascunho.pdf", "ana")
    await up.put(s2, b"x", "application/pdf")

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with pool.acquire() as c, c.transaction():
            await up.commit(c, s2.id, "2026/rascunho.pdf", "ana")
            raise Boom
    assert await up.location(s2.id) is None
    async with pool.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM hub_jobs WHERE idempotency_key = $1", s2.id) == 0

    # Queda entre as fases + órfão sem registro.
    s3 = await up.begin("perdido.pdf", "ana")
    await up.put(s3, b"yy", "application/pdf")
    store.files["_staging/upl-semregistro/x.bin"] = b"z"
    rep = await up.cleanup()
    assert (rep.expired, rep.orphans) == (0, 1)
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE hub_uploads SET expires_at = now() - interval '1 second' WHERE id = ANY($1)",
            [s2.id, s3.id],
        )
    rep = await up.cleanup()
    assert rep.expired == 2
    assert s2.staging_path not in store.files and s3.staging_path not in store.files
    with pytest.raises(ValueError):
        async with pool.acquire() as c:
            await up.commit(c, s3.id, "2026/perdido.pdf", "ana")
    assert (await up.cleanup()) == type(rep)()
    assert "2026/contrato.pdf" in store.files


@pytest.mark.db
async def test_review_fixes_ownership_swap_cancel_and_concurrent_once(pool: Any) -> None:
    """Revisão de segurança (M1–M4, L1–L3): dono do envio, troca após confirmar, destino
    existente, compensação × promoção e ``once`` com execuções simultâneas."""
    store = MemStore()
    up = Uploads(pool, store, ttl=120)
    s = await up.begin("a.txt", "ana")
    await up.put(s, b"benigno", "text/plain")
    async with pool.acquire() as c:
        with pytest.raises(ValueError, match="outro usuário"):
            await up.commit(c, s.id, "2026/a.txt", "mallory")
        for bad in ["/_staging/x/y", "//_STAGING/x", "_staging/upl-1/a"]:
            with pytest.raises(ValueError):
                await up.commit(c, s.id, bad, "ana")
        await up.commit(c, s.id, "2026/a.txt", "ana")
    await up.put(s, b"malicioso!!", "text/plain")  # troca depois da confirmação
    with pytest.raises(JobFail, match="mudou"):
        await up.promote(s.id)
    assert "2026/a.txt" not in store.files

    store.files["2026/existe.txt"] = b"antigo"
    s2 = await up.begin("b.txt", "ana")
    await up.put(s2, b"novo", "text/plain")
    async with pool.acquire() as c:
        await up.commit(c, s2.id, "2026/existe.txt", "ana")
    with pytest.raises(JobFail, match="já existe"):
        await up.promote(s2.id)
    assert store.files["2026/existe.txt"] == b"antigo"

    s3 = await up.begin("c.txt", "ana")
    await up.put(s3, b"doc", "text/plain")
    async with pool.acquire() as c:
        await up.commit(c, s3.id, "2026/c.txt", "ana")
    await up.promote(s3.id)
    await up.cancel(s3.id)
    await up.cancel(s3.id)
    assert "2026/c.txt" not in store.files and await up.location(s3.id) is None
    with pytest.raises(JobFail):
        await up.promote(s3.id)

    s5 = await up.begin("queda.txt", "ana")
    await up.put(s5, b"doc", "text/plain")
    async with pool.acquire() as c:
        await up.commit(c, s5.id, "2026/queda.txt", "ana")
    await store.move(s5.staging_path, "2026/queda.txt")  # promoção caiu antes de marcar
    await up.cancel(s5.id)
    assert "2026/queda.txt" not in store.files

    big = Uploads(pool, store, ttl=120, max_size=3)
    s4 = await big.begin("d.txt", "ana")
    await big.put(s4, b"12345", "text/plain")
    async with pool.acquire() as c:
        with pytest.raises(ValueError, match="grande"):
            await big.commit(c, s4.id, "2026/d.txt", "ana")

    async with pool.acquire() as c:
        await c.execute("CREATE TABLE dup (id serial PRIMARY KEY)")

    async def effect(conn: Any) -> Any:
        await conn.execute("INSERT INTO dup DEFAULT VALUES")
        await asyncio.sleep(0.3)
        return {"ok": 1}

    def ctx() -> StepCtx:
        return StepCtx("saga-x", "criar", "saga-x:criar", None, {}, None, None, "ana", pool)

    a, b = await asyncio.gather(ctx().once(effect), ctx().once(effect))
    assert a == b == {"ok": 1}
    async with pool.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM dup") == 1
