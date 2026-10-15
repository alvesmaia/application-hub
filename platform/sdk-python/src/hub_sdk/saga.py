"""Sagas (onda 12, item 2.9).

A saga is a sequence of **steps** (action + compensation) run as a durable job
(:mod:`hub_sdk.jobs`): same queue, lease, ``attempt_token``, checkpoint and visibility in
Operations › Jobs.

- State in the job checkpoint (``{"saga": {...}}``), written with the attempt's fence
  after each step/compensation. Crash midway → another worker resumes: forward
  if ``running``, compensating if ``compensating``.
- Definitive failure (:class:`~hub_sdk.jobs.JobFail`) or transient on the last attempt →
  compensations in reverse order, from the failed step (which may have applied part) back to the
  first. Transient before that → retry of the same step.
- Idempotency: stable key ``<job>:<step>`` (``…:undo`` for the compensation); DB effects
  through :meth:`StepCtx.once` (exactly once). Compensations tolerate "nothing to
  desfazer".
- End: ``completed`` → job ``done``; ``compensated`` → ``failed`` with the reason;
  compensation with a definitive error → ``failed`` (manual intervention).

The next-step rule (:func:`next_action`) is covered by fixed test vectors.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, NoReturn

from hub_sdk import outbox
from hub_sdk.jobs import JobCtx, JobError, JobFail, JobRetry, JobSpec

StepFn = Callable[["StepCtx"], Awaitable[Any]]


def new_state(names: Sequence[str]) -> dict[str, Any]:
    return {"v": 1, "state": "running", "steps": [{"name": n, "state": "pending"} for n in names]}


def next_action(s: dict[str, Any]) -> str:
    """Next action from the saved state: ``run:i``, ``compensate:i``, ``complete``,
    ``compensated`` or ``failed``."""
    steps = s.get("steps") or []
    st = s.get("state")
    if st == "running":
        return _next_forward(steps)
    if st == "compensating":
        return _next_backward(steps)
    return {"completed": "complete", "compensated": "compensated"}.get(str(st), "failed")


def _next_forward(steps: list[dict[str, Any]]) -> str:
    for i, p in enumerate(steps):
        if p.get("state") != "done":
            return f"run:{i}"
    return "complete"


def _next_backward(steps: list[dict[str, Any]]) -> str:
    for i in range(len(steps) - 1, -1, -1):
        if steps[i].get("state") in ("done", "attempted", "compensating"):
            return f"compensate:{i}"
    return "compensated"


def state_of(checkpoint: Any, names: Sequence[str]) -> dict[str, Any]:
    """Saga state in the checkpoint (or a new one). Refuses a changed definition in progress."""
    cp = checkpoint
    if isinstance(cp, str):
        cp = json.loads(cp)
    s = cp.get("saga") if isinstance(cp, dict) else None
    if s is None:
        return new_state(names)
    saved = [p.get("name") for p in s.get("steps", [])]
    if saved != list(names):
        raise JobFail(
            f"a definição da saga mudou com ela em andamento (salvo: {saved}; agora: {list(names)})"
        )
    return dict(s)


def summary(checkpoint: Any) -> dict[str, Any] | None:
    """Summary for the hub: ``{state, steps: [{name, state}], reason}``."""
    cp = json.loads(checkpoint) if isinstance(checkpoint, str) else checkpoint
    s = cp.get("saga") if isinstance(cp, dict) else None
    if not isinstance(s, dict):
        return None
    return {
        "state": s.get("state"),
        "steps": [{"name": p.get("name"), "state": p.get("state")} for p in s.get("steps", [])],
        "reason": s.get("reason"),
    }


@dataclass
class StepCtx:
    """Context of a step."""

    saga_id: str
    step: str
    key: str
    input: Any
    outputs: dict[str, Any]
    output: Any
    reason: str | None
    user: str
    pool: Any

    async def once(self, fn: Callable[[Any], Awaitable[Any]]) -> Any:
        """DB effect **exactly once**: ``fn(conn)`` runs in a transaction that
        also writes ``hub_saga_once(key)``; on resume it returns the recorded output."""
        # Claims first, in the same transaction as the effect: a concurrent run of the
        # same step waits on the unique key until this one commits or rolls back.
        async with self.pool.acquire() as c:
            async with c.transaction():
                claimed = await c.fetchval(
                    "INSERT INTO hub_saga_once (key) VALUES ($1) ON CONFLICT (key) DO NOTHING RETURNING true",
                    self.key,
                )
                if claimed:
                    out = await fn(c)
                    await c.execute("UPDATE hub_saga_once SET output = $2 WHERE key = $1", self.key, out)
                    return out
            r = await c.fetchrow("SELECT output FROM hub_saga_once WHERE key = $1", self.key)
            return _loads(r["output"]) if r is not None else None


def _loads(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


@dataclass
class Step:
    name: str
    action: StepFn
    compensate: StepFn | None = None


@dataclass
class Saga:
    """Definition of a saga (one job type). Register it like any handler."""

    spec: JobSpec
    steps: list[Step] = field(default_factory=list)

    def step(self, name: str, action: StepFn, compensate: StepFn | None = None) -> Saga:
        self.steps.append(Step(name, action, compensate))
        return self

    def final_event(self, job: Any, status: str, detail: str) -> outbox.Event | None:
        return None

    async def _save(self, ctx: JobCtx, st: dict[str, Any]) -> None:
        done = sum(1 for p in st["steps"] if p.get("state") == "done")
        async with ctx.pool.acquire() as c, c.transaction():
            await ctx.save_checkpoint(c, {"saga": st}, done, len(st["steps"]), f"saga: {st['state']}")

    def _ctx(self, ctx: JobCtx, st: dict[str, Any], name: str, key: str) -> StepCtx:
        outs = {p["name"]: p["output"] for p in st["steps"] if p.get("output") is not None}
        return StepCtx(
            saga_id=ctx.job.id,
            step=name,
            key=key,
            input=_loads(ctx.job.payload),
            outputs=outs,
            output=None,
            reason=st.get("reason"),
            user=ctx.job.user,
            pool=ctx.pool,
        )

    async def run(self, ctx: JobCtx) -> Any:
        names = [s.name for s in self.steps]
        st = state_of(ctx.checkpoint, names)
        last = ctx.job.attempts >= ctx.job.max_attempts
        while True:
            ctx.raise_if_stopping()
            nxt = next_action(st)
            if nxt.startswith("run:"):
                await self._run_step(ctx, st, int(nxt[4:]), last)
            elif nxt.startswith("compensate:"):
                await self._compensate_step(ctx, st, int(nxt[11:]))
            else:
                return await self._finish(ctx, st, nxt)

    async def _finish(self, ctx: JobCtx, st: dict[str, Any], nxt: str) -> Any:
        """Terminal action: ``complete`` returns the outputs; anything else fails the job."""
        if nxt == "complete":
            await self._mark(ctx, st, "completed")
            return {
                "saga": "completed",
                "outputs": {p["name"]: p["output"] for p in st["steps"] if p.get("output") is not None},
            }
        if nxt == "compensated":
            await self._mark(ctx, st, "compensated")
            raise JobFail(f"saga compensada — {st.get('reason') or ''}")
        raise JobFail(f"saga com compensação pendente de intervenção — {st.get('reason') or ''}")

    async def _mark(self, ctx: JobCtx, st: dict[str, Any], state: str) -> None:
        if st["state"] != state:
            st["state"] = state
            await self._save(ctx, st)

    async def _run_step(self, ctx: JobCtx, st: dict[str, Any], i: int, last: bool) -> None:
        """Step ``i`` forward; a definitive failure (or a transient one on the last attempt) switches
        the saga to compensating instead of failing the attempt."""
        name = self.steps[i].name
        try:
            out = await self.steps[i].action(self._ctx(ctx, st, name, f"{ctx.job.id}:{name}"))
        except (JobRetry, JobFail) as e:
            if isinstance(e, JobRetry) and not last:
                raise JobRetry(f"{name}: {e}") from e
            await self._start_compensating(ctx, st, i, e)
            return
        except JobError:
            raise
        except Exception as e:  # unexpected error in a step = transient
            if not last:
                raise JobRetry(f"{name}: {e.__class__.__name__}: {e}") from e
            await self._start_compensating(ctx, st, i, e)
            return
        st["steps"][i]["state"] = "done"
        if out is not None:
            st["steps"][i]["output"] = out
        st["steps"][i].pop("error", None)
        await self._save(ctx, st)

    async def _start_compensating(self, ctx: JobCtx, st: dict[str, Any], i: int, e: Exception) -> None:
        name = self.steps[i].name
        st["steps"][i]["state"] = "attempted"
        st["steps"][i]["error"] = str(e)
        st["state"] = "compensating"
        st["reason"] = f"{name}: {e}"
        await self._save(ctx, st)

    async def _compensate_step(self, ctx: JobCtx, st: dict[str, Any], i: int) -> None:
        name = self.steps[i].name
        if st["steps"][i]["state"] != "compensating":
            st["steps"][i]["state"] = "compensating"
            await self._save(ctx, st)
        sc = self._undo_ctx(ctx, st, i)
        comp = self.steps[i].compensate
        try:
            if comp is not None:
                await comp(sc)
        except JobFail as e:
            await self._fail_compensation(ctx, st, i, e)
        except JobRetry as e:
            raise JobRetry(f"compensação de {name}: {e}") from e
        except JobError:
            raise
        except Exception as e:
            raise JobRetry(f"compensação de {name}: {e.__class__.__name__}: {e}") from e
        st["steps"][i]["state"] = "compensated"
        await self._save(ctx, st)

    def _undo_ctx(self, ctx: JobCtx, st: dict[str, Any], i: int) -> StepCtx:
        name = self.steps[i].name
        sc = self._ctx(ctx, st, name, f"{ctx.job.id}:{name}:undo")
        sc.output = st["steps"][i].get("output")
        return sc

    async def _fail_compensation(self, ctx: JobCtx, st: dict[str, Any], i: int, e: JobFail) -> NoReturn:
        """A definitive compensation error leaves the saga ``failed`` for manual intervention."""
        name = self.steps[i].name
        st["steps"][i]["state"] = "failed"
        st["steps"][i]["error"] = str(e)
        st["state"] = "failed"
        await self._save(ctx, st)
        raise JobFail(f"compensação de {name} falhou (intervenção manual): {e}") from e
