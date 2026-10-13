"""Testes com Postgres real (``HUB_DB_TEST_URL``): tarefas duráveis, outbox e
idempotência."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from typing_extensions import override

from hub_sdk import internal, outbox
from hub_sdk.heavy import Heavy
from hub_sdk.idempotency import (
    HEADER,
    REPLAY_HEADER,
    USER_HEADER,
    Idempotency,
    IdempotencyMiddleware,
    idem_ctx,
)
from hub_sdk.jobs import (
    Job,
    JobCancelled,
    JobCtx,
    JobFail,
    JobLostLease,
    Jobs,
    JobShutdown,
    JobSpec,
    Worker,
    run_claimed,
)
from hub_sdk.schema import cleanup
from hub_sdk.shutdown import Shutdown

pytestmark = pytest.mark.db


class Recorder:
    """Handler de teste: processa ``n`` lotes gravando ``items`` + checkpoint na mesma
    transação; comportamento configurável."""

    def __init__(self, spec: JobSpec, batches: int = 3, fail_first: int = 0, fatal: bool = False) -> None:
        self.spec = spec
        self.batches = batches
        self.fail_first = fail_first
        self.fatal = fatal
        self.runs = 0
        self.gate: asyncio.Event | None = None

    async def run(self, ctx: JobCtx) -> Any:
        self.runs += 1
        if self.fatal:
            raise JobFail("entrada inválida")
        if self.runs <= self.fail_first:
            raise RuntimeError("falha transitória")
        start = (ctx.checkpoint or {}).get("batch", 0)
        for b in range(start, self.batches):
            ctx.raise_if_stopping()
            if self.gate is not None:
                await self.gate.wait()
            async with ctx.pool.acquire() as c, c.transaction():
                await c.execute("INSERT INTO items (name) VALUES ($1)", f"{ctx.job.id}:{b}")
                await ctx.save_checkpoint(c, {"batch": b + 1}, b + 1, self.batches)
        return {"batches": self.batches}

    def final_event(self, job: Job, status: str, detail: str) -> outbox.Event | None:
        return outbox.audit(job.user, f"Tarefa {status}", job.id, detail)


async def count(pool: Any, sql: str, *a: Any) -> int:
    async with pool.acquire() as c:
        return int(await c.fetchval(sql, *a))


async def test_outbox_is_transactional_and_ack_idempotent(pool: Any) -> None:
    async with pool.acquire() as c:
        with pytest.raises(RuntimeError):
            async with c.transaction():
                await outbox.write(c, outbox.audit("ana", "desfeito"))
                raise RuntimeError("rollback")
        async with c.transaction():
            eid = await outbox.write(c, outbox.audit("ana", "Criado", "X-1", "detalhe"))
    ob = outbox.Outbox.postgres(pool)
    p = await ob.pending(0, 10)
    assert [(e.action, e.target, e.user) for e in p] == [("Criado", "X-1", "ana")]
    assert p[0].event_id == eid and p[0].at > 0
    assert await ob.ack([eid]) == 1
    assert await ob.ack([eid]) == 0
    assert await ob.pending(0, 10) == []
    async with pool.acquire() as c:
        await c.execute("UPDATE hub_outbox SET acked_at = now() - interval '8 days'")
        await c.execute(
            "INSERT INTO hub_idempotency (route, key, request_hash, state, expires_at) "
            "VALUES ('r','k','h','done', now() - interval '1 hour')"
        )
    assert await cleanup(pool) == (1, 1)


async def test_enqueue_idempotent_and_claim_skip_locked(pool: Any) -> None:
    jobs = Jobs(pool)
    spec = JobSpec("t.a")
    j1, created = await jobs.enqueue(spec, {"n": 1}, "ana", "chave-1")
    j2, created2 = await jobs.enqueue(spec, {"n": 2}, "ana", "chave-1")
    assert created and not created2 and j1.id == j2.id
    await jobs.enqueue(spec, {"n": 3}, "ana")
    a, b, c = await asyncio.gather(jobs.claim([spec]), Jobs(pool).claim([spec]), Jobs(pool).claim([spec]))
    got = [x for x in (a, b, c) if x is not None]
    assert len(got) == 2 and len({x.id for x in got}) == 2
    assert all(x.status == "running" and x.attempts == 1 and x.attempt_token for x in got)
    assert "attempt_token" not in got[0].to_json()


async def test_worker_runs_to_done_with_final_event_once(pool: Any) -> None:
    spec = JobSpec("t.ok", lease=3)
    h = Recorder(spec)
    jobs = Jobs(pool)
    job, _ = await jobs.enqueue(spec, {}, "ana")
    sd = Shutdown()
    w = Worker(jobs, [h], Heavy(2), sd, poll=0.1)
    t = w.spawn()
    for _ in range(100):
        j = await jobs.get(job.id)
        if j and j.status == "done":
            break
        await asyncio.sleep(0.05)
    sd.trigger()
    await asyncio.wait_for(t, 5)
    j = await jobs.get(job.id)
    assert j is not None and j.status == "done" and j.result == {"batches": 3} and j.percent == 100
    assert await count(pool, "SELECT count(*) FROM items") == 3
    assert await count(pool, "SELECT count(*) FROM hub_outbox WHERE payload->>'action' = 'Tarefa done'") == 1


async def test_transient_failure_backs_off_then_dead(pool: Any) -> None:
    spec = JobSpec("t.retry", max_attempts=2, backoff_base=0.2, backoff_max=0.4, lease=3)
    h = Recorder(spec, fail_first=10)
    jobs = Jobs(pool)
    job, _ = await jobs.enqueue(spec, {}, "ana")
    claimed = await jobs.claim([spec])
    assert claimed is not None
    await run_claimed(jobs, h, claimed, Shutdown())
    j = await jobs.get(job.id)
    assert j is not None and j.status == "queued" and "falha transitória" in (j.error or "")
    assert j.run_after > j.updated_at - 1000
    await asyncio.sleep(0.5)
    claimed = await jobs.claim([spec])
    assert claimed is not None and claimed.attempts == 2
    await run_claimed(jobs, h, claimed, Shutdown())
    j = await jobs.get(job.id)
    assert j is not None and j.status == "dead"
    assert await count(pool, "SELECT count(*) FROM hub_outbox WHERE payload->>'action' = 'Tarefa dead'") == 1
    # reprocessar: volta à fila com tentativas zeradas
    r = await jobs.retry(job.id)
    assert r.status == "queued" and r.attempts == 0
    with pytest.raises(ValueError):
        await jobs.retry(job.id)


async def test_fatal_failure_is_failed_without_retries(pool: Any) -> None:
    spec = JobSpec("t.fatal", max_attempts=5, lease=3)
    jobs = Jobs(pool)
    job, _ = await jobs.enqueue(spec, {}, "ana")
    claimed = await jobs.claim([spec])
    assert claimed is not None
    await run_claimed(jobs, Recorder(spec, fatal=True), claimed, Shutdown())
    j = await jobs.get(job.id)
    assert j is not None and j.status == "failed" and j.error == "entrada inválida"


async def test_fence_lost_lease_and_resume_from_checkpoint(pool: Any) -> None:
    spec = JobSpec("t.fence", lease=1)
    jobs = Jobs(pool)
    job, _ = await jobs.enqueue(spec, {}, "ana")
    first = await jobs.claim([spec])
    assert first is not None
    ctx = JobCtx(first, jobs, spec, Shutdown())
    async with pool.acquire() as c, c.transaction():
        await c.execute("INSERT INTO items (name) VALUES ('lote0')")
        await ctx.save_checkpoint(c, {"batch": 1}, 1, 3)
    # O processo "morre": o lease vence e outro worker assume com token novo.
    await asyncio.sleep(1.3)
    second = await Jobs(pool).claim([spec])
    assert second is not None and second.id == job.id and second.attempt_token != first.attempt_token
    assert second.checkpoint == {"batch": 1} and second.error == "retomada após lease vencido"
    # O antigo não grava mais nada: o lote dele é descartado junto com o checkpoint.
    with pytest.raises(JobLostLease):
        async with pool.acquire() as c, c.transaction():
            await c.execute("INSERT INTO items (name) VALUES ('zumbi')")
            await ctx.save_checkpoint(c, {"batch": 2}, 2, 3)
    assert await count(pool, "SELECT count(*) FROM items WHERE name = 'zumbi'") == 0
    h = Recorder(spec)
    await run_claimed(jobs, h, second, Shutdown())
    j = await jobs.get(job.id)
    assert j is not None and j.status == "done" and j.attempts == 2
    # lote 0 do primeiro + lotes 1 e 2 do segundo: nenhum duplicado
    assert await count(pool, "SELECT count(*) FROM items") == 3


async def test_cancel_queued_running_and_reap(pool: Any) -> None:
    spec = JobSpec("t.cancel", lease=1)
    jobs = Jobs(pool)
    q, _ = await jobs.enqueue(spec, {}, "ana")
    c = await jobs.cancel(q.id)
    assert c.status == "cancelled"
    with pytest.raises(ValueError):
        await jobs.cancel(q.id)
    with pytest.raises(LookupError):
        await jobs.cancel("job-inexistente")
    r, _ = await jobs.enqueue(spec, {}, "ana")
    running = await jobs.claim([spec])
    assert running is not None and running.id == r.id
    h = Recorder(spec, batches=50)
    h.gate = asyncio.Event()
    task = asyncio.create_task(run_claimed(jobs, h, running, Shutdown()))
    await asyncio.sleep(0.1)
    asked = await jobs.cancel(r.id)
    assert asked.status == "running" and asked.cancel_requested
    h.gate.set()
    await asyncio.wait_for(task, 10)
    j = await jobs.get(r.id)
    assert j is not None and j.status == "cancelled"
    # running com lease vencido e sem tentativas → dead no reap
    d, _ = await jobs.enqueue(JobSpec("t.cancel", max_attempts=1, lease=1), {}, "ana")
    assert await jobs.claim([spec]) is not None
    await asyncio.sleep(1.2)
    reaped = await jobs.reap(["t.cancel"])
    assert [x.id for x in reaped] == [d.id] and reaped[0].status == "dead"


async def test_shutdown_returns_lease_without_spending_attempt(pool: Any) -> None:
    spec = JobSpec("t.sd", lease=3)
    jobs = Jobs(pool)
    job, _ = await jobs.enqueue(spec, {}, "ana")
    claimed = await jobs.claim([spec])
    assert claimed is not None
    sd = Shutdown()
    h = Recorder(spec, batches=10)
    h.gate = asyncio.Event()
    task = asyncio.create_task(run_claimed(jobs, h, claimed, sd))
    await asyncio.sleep(0.05)
    sd.trigger()
    h.gate.set()
    await asyncio.wait_for(task, 10)
    j = await jobs.get(job.id)
    assert j is not None and j.status == "queued" and j.attempts == 0
    assert isinstance(JobShutdown("x"), Exception) and isinstance(JobCancelled("x"), Exception)


def app_with(pool: Any, calls: list[str], mark: bool = True) -> FastAPI:
    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware, idem=Idempotency(pool, wait=0.3))

    @app.post("/criar")
    async def criar(request: Request) -> Any:
        body = (await request.body()).decode()
        if body == "lento":
            await asyncio.sleep(1.0)
        ctx = idem_ctx(request)
        async with pool.acquire() as c, c.transaction():
            await c.execute("INSERT INTO items (name) VALUES ($1)", body)
            if ctx and mark:
                await ctx.mark_committed(c)
        calls.append(body)
        return PlainTextResponse(f"ok {body}", status_code=201)

    return app


async def test_idempotency_postgres_replay_mismatch_parallel(pool: Any, db_url: str) -> None:
    calls: list[str] = []
    from hub_sdk.db import create_pool

    idem_pool = await create_pool(db_url, 4)
    try:
        app = app_with(idem_pool, calls)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            h = {HEADER: "pg-chave-0001", USER_HEADER: "ana"}
            r1 = await c.post("/criar", content=b"a", headers=h)
            r2 = await c.post("/criar", content=b"a", headers=h)
            assert (r1.status_code, r2.status_code) == (201, 201)
            assert r2.headers[REPLAY_HEADER] == "true" and r2.text == "ok a"
            assert (await c.post("/criar", content=b"b", headers=h)).status_code == 422
            assert (
                await c.post("/criar", content=b"a", headers={**h, USER_HEADER: "bia"})
            ).status_code == 201
            hp = {HEADER: "pg-paralela-01"}
            a, b = await asyncio.gather(
                c.post("/criar", content=b"lento", headers=hp), c.post("/criar", content=b"lento", headers=hp)
            )
            assert sorted([a.status_code, b.status_code]) == [201, 409]
        assert calls == ["a", "a", "lento"]
        # Queda depois de efetivar e antes de guardar a resposta: registro "committed"
        # sem dono → a repetição não executa de novo ("Operação já concluída").
        async with idem_pool.acquire() as conn:
            await conn.execute(
                "UPDATE hub_idempotency SET state = 'committed', status = NULL, body = NULL "
                "WHERE key = 'pg-chave-0001' AND route LIKE 'ana%'"
            )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/criar", content=b"a", headers=h)
            assert r.status_code == 200 and "Operação já concluída" in r.text
        # Queda antes de efetivar: "pending" sem dono → executa de novo.
        async with idem_pool.acquire() as conn:
            await conn.execute(
                "UPDATE hub_idempotency SET state = 'pending' WHERE key = 'pg-chave-0001' AND route LIKE 'ana%'"
            )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/criar", content=b"a", headers=h)
            assert r.status_code == 201 and REPLAY_HEADER not in r.headers
        assert calls.count("a") == 3
    finally:
        await idem_pool.close()


async def test_internal_jobs_endpoints_with_postgres(pool: Any) -> None:
    jobs = Jobs(pool)
    spec = JobSpec("t.int", max_attempts=1)
    job, _ = await jobs.enqueue(spec, {}, "ana")
    woke: list[int] = []
    app = FastAPI()
    app.include_router(
        internal.router(
            "demo", "0123456789abcdef0123", outbox.Outbox.postgres(pool), jobs, lambda: woke.append(1)
        )
    )
    h = {internal.TOKEN_HEADER: "0123456789abcdef0123"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/demo/_hub/jobs?limit=9999", headers=h)
        assert r.json()["enabled"] is True and r.json()["jobs"][0]["id"] == job.id
        assert "attempt_token" not in r.json()["jobs"][0]
        assert (await c.post(f"/demo/_hub/jobs/{job.id}/retry", headers=h)).status_code == 409
        r = await c.post(f"/demo/_hub/jobs/{job.id}/cancel", headers=h)
        assert r.status_code == 200 and r.json()["job"]["status"] == "cancelled"
        assert (await c.post(f"/demo/_hub/jobs/{job.id}/retry", headers=h)).json()["job"][
            "status"
        ] == "queued"
        assert (await c.post("/demo/_hub/jobs/nao-existe/retry", headers=h)).status_code == 404
    assert woke == [1, 1]


async def test_worker_process_mode_runs_jobs_outside_the_web_and_drains(db_url: str) -> None:
    """HUB_JOBS_MODE=process: o web não roda tarefas; o processo de tarefas as executa e
    encerra no desligamento."""
    from hub_sdk.app import HubSdk

    spec = JobSpec("t.proc", lease=3)
    sql = "CREATE TABLE IF NOT EXISTS items (id serial PRIMARY KEY, name text)"
    web = HubSdk("demo", db_url, app_sql=sql, handlers=[Recorder(spec)], jobs_mode="process")
    app = FastAPI(lifespan=web.lifespan)
    async with app.router.lifespan_context(app):
        assert web.worker is None, "o web não assume tarefas"
        for _ in range(100):
            if web.db_ready:
                break
            await asyncio.sleep(0.05)
        assert web.jobs is not None
        job, _ = await web.jobs.enqueue(spec, {}, "ana")
        proc = HubSdk("demo", db_url, handlers=[Recorder(spec)], jobs_mode="process")
        task = asyncio.create_task(proc.run_worker_process())
        j = None
        for _ in range(200):
            j = await web.jobs.get(job.id)
            if j and j.status == "done":
                break
            await asyncio.sleep(0.05)
        assert j is not None and j.status == "done"
        proc.shutdown.trigger()
        await asyncio.wait_for(task, 10)


class Finalizing(Recorder):
    """Recorder que registra as chamadas de ``after_final`` (e pode falhar no evento)."""

    def __init__(self, spec: JobSpec, **kw: Any) -> None:
        super().__init__(spec, **kw)
        self.finals: list[tuple[str, str]] = []
        self.explode = False

    @override
    def final_event(self, job: Job, status: str, detail: str) -> outbox.Event | None:
        if self.explode:
            raise RuntimeError("evento final falhou")
        return super().final_event(job, status, detail)

    def after_final(self, job: Job, status: str) -> None:
        self.finals.append((job.id, status))


async def test_reap_writes_the_final_event_in_the_same_transaction(pool: Any) -> None:
    spec = JobSpec("t.reap", max_attempts=1, lease=1)
    h = Finalizing(spec)
    jobs = Jobs(pool, handlers=[h])
    d, _ = await jobs.enqueue(spec, {}, "ana")
    assert await jobs.claim([spec]) is not None
    await asyncio.sleep(1.2)
    # o evento final falha → a transação inteira é desfeita: a tarefa continua running
    h.explode = True
    with pytest.raises(RuntimeError):
        await jobs.reap([spec.kind])
    j = await jobs.get(d.id)
    assert j is not None and j.status == "running"
    assert await count(pool, "SELECT count(*) FROM hub_outbox") == 0
    # agora sim: estado + evento juntos
    h.explode = False
    reaped = await jobs.reap([spec.kind])
    assert [x.status for x in reaped] == ["dead"]
    assert await count(pool, "SELECT count(*) FROM hub_outbox WHERE payload->>'action' = 'Tarefa dead'") == 1
    # o worker chama after_final para as reapadas
    w = Worker(jobs, [h], Heavy(1), Shutdown(), poll=0.05)
    e, _ = await jobs.enqueue(spec, {}, "ana")
    assert await jobs.claim([spec]) is not None
    await asyncio.sleep(1.2)
    t = w.spawn()
    for _ in range(100):
        if (e.id, "dead") in h.finals:
            break
        await asyncio.sleep(0.05)
    w.shutdown.trigger()
    await asyncio.wait_for(t, 5)
    assert (e.id, "dead") in h.finals


async def test_after_final_on_done_and_immediate_cancel(pool: Any) -> None:
    spec = JobSpec("t.af", lease=3)
    h = Finalizing(spec, batches=1)
    jobs = Jobs(pool, handlers=[h])
    a, _ = await jobs.enqueue(spec, {}, "ana")
    claimed = await jobs.claim([spec])
    assert claimed is not None
    await run_claimed(jobs, h, claimed, Shutdown())
    b, _ = await jobs.enqueue(spec, {}, "ana")
    await jobs.cancel(b.id)
    assert h.finals == [(a.id, "done"), (b.id, "cancelled")]


async def test_killed_in_memory_phase_fails_without_retry(pool: Any, tmp_path: Any) -> None:
    spec = JobSpec("t.oom", max_attempts=5, lease=30)
    h = Finalizing(spec)
    dead_proc = Jobs(pool, owner="4242-abcdef01", handlers=[h], marker_dir=tmp_path)
    await dead_proc.enqueue(spec, {}, "ana")
    await dead_proc.enqueue(spec, {}, "ana")
    c1 = await dead_proc.claim([spec])
    c2 = await dead_proc.claim([spec])
    assert c1 is not None and c2 is not None
    # marca de fase de memória: a do job1 é do processo morto (4242); a do job2 é de outro
    # processo (ex.: vida anterior do container) → só apagada, o job2 retoma normalmente.
    ctx = JobCtx(c1, dead_proc, spec, Shutdown())
    with ctx.memory_phase():
        assert len(list(tmp_path.glob("*.mem"))) == 1
    assert list(tmp_path.glob("*.mem")) == []
    (tmp_path / f"{c1.id}.4242.mem").write_text("x")
    (tmp_path / f"{c2.id}.999.mem").write_text("x")
    fresh = Jobs(pool, handlers=[h], marker_dir=tmp_path)
    ids = fresh.take_markers(4242)
    assert ids == [c1.id] and list(tmp_path.glob("*.mem")) == []
    failed = await fresh.fail_killed(ids, "4242-", "memória esgotada")
    assert [f.id for f in failed] == [c1.id]
    f1, f2 = await fresh.get(c1.id), await fresh.get(c2.id)
    assert f1 is not None and f1.status == "failed" and f1.error == "memória esgotada" and f1.attempts == 1
    assert f2 is not None and f2.status == "running"
    assert h.finals == [(c1.id, "failed")]
    assert (
        await count(pool, "SELECT count(*) FROM hub_outbox WHERE payload->>'action' = 'Tarefa failed'") == 1
    )
    # lease de outro processo (retomado por outro worker): nada muda
    assert (await fresh.fail_killed([c2.id], "1111-", "x")) == []
