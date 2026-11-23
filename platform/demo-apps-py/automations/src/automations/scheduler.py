"""In-process scheduler: every ``TICK_S`` each web process fires the due triggers
(:func:`automations.triggers.fire_due` — exactly once across processes and replicas) and wakes
the jobs worker. Started and stopped with the app's lifespan."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime

import asyncpg
from fastapi import FastAPI
from hub_sdk.app import HubSdk

from automations.triggers import fire_due

log = logging.getLogger("automations")
TICK_S = 30.0


async def tick(sdk: HubSdk) -> list[int]:
    if not sdk.db_ready:
        return []
    ids = await fire_due(sdk.pool, datetime.now(UTC))
    if ids:
        sdk.wake()
    return ids


async def loop(sdk: HubSdk, every: float = TICK_S) -> None:
    while not sdk.shutdown.triggered:
        try:
            await tick(sdk)
        except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as e:
            log.warning("automations: agendador sem banco: %s", e)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(sdk.shutdown.wait(), every)


def lifespan(sdk: HubSdk) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """The SDK lifespan plus the scheduler loop."""

    @contextlib.asynccontextmanager
    async def run(app: FastAPI) -> AsyncGenerator[None]:
        async with sdk.lifespan(app):
            task = asyncio.create_task(loop(sdk))
            try:
                yield
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    return run
