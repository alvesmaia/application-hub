"""Runs a package in a subprocess (:mod:`automations.sandbox`): fresh directory under /scratch with the
package extracted, params on stdin, result on stdout, stderr kept (64 KB) as the run log,
environment STRIPPED (no ``HUB_*`` variable or secret), timeout and cancellation kill the process."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from automations.package_format import extract

LOG_CAP = 64 * 1024
RESULT_CAP = 4 * 1024 * 1024
POLL_S = 0.25
# Only what a Python process needs to start (SYSTEMROOT: Windows) plus the public base URLs
# the sample packages read. Never HUB_DB_URL, HUB_INTERNAL_TOKEN or anything else of the app.
PR_SET_DUMPABLE = 4
KEEP_ENV = ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")
PASS_ENV = ("AUTOMATIONS_VIACEP_URL", "AUTOMATIONS_BRASILAPI_URL")

StopCheck = Callable[[], Awaitable[BaseException | None]]


@dataclass(frozen=True)
class Outcome:
    output: dict[str, Any] | None
    error: str | None
    log: str


def protect_parent() -> None:
    """Linux: the app process becomes non-dumpable, so ``/proc/<app pid>/environ`` (HUB_DB_URL,
    HUB_INTERNAL_TOKEN…) is no longer readable by the package subprocess, which runs as the same
    user. A no-op elsewhere."""
    if sys.platform == "linux":
        import ctypes

        ctypes.CDLL(None).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)


def child_env(workdir: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in (*KEEP_ENV, *PASS_ENV) if os.environ.get(k, "") != ""}
    home = str(workdir)
    return env | {"HOME": home, "TMPDIR": home, "TEMP": home, "TMP": home}


async def execute(
    content: bytes, entrypoint: str, params: dict[str, Any], timeout_s: float, scratch: Path, stop: StopCheck
) -> Outcome:
    """Runs and cleans up; raises what ``stop`` returns (cancel, shutdown) after killing the process."""
    protect_parent()
    workdir = Path(tempfile.mkdtemp(prefix="run-", dir=scratch))
    try:
        extract(content, workdir)
        return await _run(workdir, entrypoint, json.dumps(params).encode(), timeout_s, stop)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def _run(workdir: Path, entrypoint: str, stdin: bytes, timeout_s: float, stop: StopCheck) -> Outcome:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-B", "-m", "automations.sandbox", entrypoint,
        cwd=workdir, env=child_env(workdir),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    io = asyncio.gather(_feed(proc, stdin), _read(proc.stdout, RESULT_CAP), _read(proc.stderr, LOG_CAP))
    try:
        reason = await _watch(io, timeout_s, stop)
    finally:
        _kill(proc)
        await proc.wait()
        _, out, err = await io  # the pipes close with the process
    if isinstance(reason, BaseException):
        raise reason
    log = err.decode("utf-8", "replace")
    if reason == "timeout":
        return Outcome(None, f"tempo limite de {int(timeout_s)} s excedido", log)
    return outcome_of(proc.returncode, out, log)


async def _watch(io: asyncio.Future[Any], timeout_s: float, stop: StopCheck) -> BaseException | str | None:
    """Waits for the process I/O; ``"timeout"`` or the stop reason when it must be killed."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not io.done():
        if loop.time() >= deadline:
            return "timeout"
        if (reason := await stop()) is not None:
            return reason
        await asyncio.wait({io}, timeout=POLL_S)
    return None


def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


async def _feed(proc: asyncio.subprocess.Process, data: bytes) -> None:
    assert proc.stdin is not None
    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
        proc.stdin.write(data)
        await proc.stdin.drain()
        proc.stdin.close()


async def _read(stream: asyncio.StreamReader | None, cap: int) -> bytes:
    """Reads everything (so the child never blocks on a full pipe) but keeps only ``cap`` bytes."""
    kept = bytearray()
    while stream is not None and (chunk := await stream.read(65536)):
        kept.extend(chunk[: max(0, cap - len(kept))])
    return bytes(kept)


def outcome_of(code: int | None, out: bytes, log: str) -> Outcome:
    lines = out.decode("utf-8", "replace").strip().splitlines()
    try:
        doc = json.loads(lines[-1]) if lines else None
    except ValueError:
        doc = None
    if code == 0 and isinstance(doc, dict) and isinstance(doc.get("ok"), dict):
        return Outcome(doc["ok"], None, log)
    if isinstance(doc, dict) and doc.get("error"):
        return Outcome(None, str(doc["error"])[:500], log)
    if code == 0:
        return Outcome(None, "o pacote não devolveu um resultado JSON válido", log)
    return Outcome(None, f"o pacote terminou com o código {code}", log)
