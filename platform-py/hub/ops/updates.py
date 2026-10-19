"""New version notice (optional, `HUB_UPDATE_CHECK=1`): one query per day to the latest release of the repository
(`HUB_UPDATE_REPO`, default the product's public repository) and a notice in Administration › Health. Nothing is updated
on its own: the switch is the administrator's (`scripts/hub_update.py`, docs/ATUALIZACAO.md)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from typing import Any

import httpx

from hub import __version__
from hub.httpclients import external_client

REPO = "alvesmaia/application-hub"
EVERY = 24 * 3600


def parse(v: str) -> tuple[int, int, int] | None:
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", v.strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def newer(latest: str, current: str) -> bool:
    """Only stable versions (no suffix) count; pre-releases or odd formats never trigger a notice."""
    a, b = parse(latest), parse(current)
    return a is not None and b is not None and a > b


class UpdateCheck:
    def __init__(self, repo: str | None = None) -> None:
        self.repo = repo or os.getenv("HUB_UPDATE_REPO", REPO)
        self.latest: dict[str, str] | None = None
        self.task: asyncio.Task[None] | None = None

    async def check(self, client: httpx.AsyncClient | None = None) -> None:
        own = client is None
        c = client or external_client(timeout=10)
        try:
            r = await c.get(f"https://api.github.com/repos/{self.repo}/releases/latest",
                            headers={"Accept": "application/vnd.github+json"})  # fmt: skip
            if r.status_code == 200:
                d = r.json()
                tag, url = str(d.get("tag_name", "")), str(d.get("html_url", ""))
                ok = parse(tag) is not None and url.startswith("https://github.com/")
                self.latest = {"version": tag.lstrip("v"), "url": url} if ok else None
        except (httpx.HTTPError, ValueError):
            return  # no network: tries again tomorrow
        finally:
            if own:
                await c.aclose()

    def notice(self) -> dict[str, str] | None:
        """{version, url, current} when there is a stable version newer than the running one."""
        if self.latest and newer(self.latest["version"], __version__):
            return {**self.latest, "current": __version__}
        return None

    async def _loop(self) -> None:
        while True:
            await self.check()
            await asyncio.sleep(EVERY)

    def start(self) -> None:
        self.task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task


def enabled() -> bool:
    return os.getenv("HUB_UPDATE_CHECK") == "1"


def notice(state: Any) -> dict[str, str] | None:
    chk = getattr(state, "update_check", None)
    return chk.notice() if isinstance(chk, UpdateCheck) else None
