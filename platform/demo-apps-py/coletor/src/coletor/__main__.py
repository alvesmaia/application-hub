"""``python -m coletor [serve|worker|--health|openapi]``.

- ``serve`` (default): uvicorn on ``COLETOR_HOST`` (default 127.0.0.1; the image uses
  0.0.0.0) and ``PORT`` (default 8080), with ``UVICORN_WORKERS`` processes (default 1 — see
  the sizing in ``loadtest/RESULTADOS.md``) and 8 s of drain on SIGTERM. With
  ``HUB_JOBS_MODE=process`` (the image default) it also starts and supervises the jobs process
  jobs (``worker``): restarted with increasing backoff if it dies (the web keeps running) and receives SIGTERM **immediately**, in parallel with the drain of the
  with 1 web process via the lifespan hook; with ``UVICORN_WORKERS>1`` via the signal handler
  of the parent process (:class:`HubMultiprocess`). Total ≤ 9.5 s (``docker stop`` = 10 s).
  If the jobs process dies from SIGKILL (cgroup OOM), the next one starts with
  ``HUB_JOBS_KILLED_PID`` and marks as ``failed`` the job that was in the memory phase.
- ``worker``: only the durable jobs process.
- ``--health``: ``GET /coletor`` on the app itself (image HEALTHCHECK, no curl);
  exits 0 with 200.
- ``openapi``: OpenAPI of API v1 (``hub pack`` embeds it as openapi.json).
"""

from __future__ import annotations

import contextlib
import http.client
import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any


def health() -> int:
    port = int(os.environ.get("PORT", "8080"))
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        c.request("GET", "/coletor")
        ok = c.getresponse().status == 200
        c.close()
    except OSError:
        ok = False
    return 0 if ok else 1


# Total shutdown time, below the 10 s of `docker stop`.
STOP_BUDGET_S = 9.5
KILLED_PID_ENV = "HUB_JOBS_KILLED_PID"


def killed_by_sigkill(code: int | None) -> bool:
    """SIGKILL exit (Popen: -9; shell: 137) — what the cgroup OOM killer does."""
    return code in (-9, 137)


class WorkerSupervisor:
    """Child jobs process of the web: restarted with increasing backoff (1 → 30 s) if it exits
    without being asked; SIGTERM forwarded immediately when the web starts shutting down."""

    def __init__(self) -> None:
        import threading

        self.proc: subprocess.Popen[bytes] | None = None
        self.stopping = threading.Event()
        self.stop_at: float | None = None
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._loop, name="hub-jobs-supervisor", daemon=True)

    def start(self) -> None:
        from hub_sdk.shutdown import on_process_shutdown

        on_process_shutdown(self.stop)
        self.thread.start()

    def _spawn(self, killed_pid: int | None) -> subprocess.Popen[bytes]:
        env = dict(os.environ)
        env.pop(KILLED_PID_ENV, None)
        if killed_pid is not None:
            env[KILLED_PID_ENV] = str(killed_pid)
        return subprocess.Popen([sys.executable, "-m", "coletor", "worker"], env=env)

    def _loop(self) -> None:
        wait = 1.0
        killed: int | None = None
        while not self.stopping.is_set():
            with self.lock:
                if self.stopping.is_set():
                    return
                proc = self._spawn(killed)
                self.proc = proc
            started = time.monotonic()
            code = proc.wait()
            if self.stopping.is_set():
                return
            killed = proc.pid if killed_by_sigkill(code) else None
            if time.monotonic() - started > 60:
                wait = 1.0
            why = " (SIGKILL — provável falta de memória)" if killed is not None else ""
            print(f"coletor: processo de tarefas saiu ({code}){why}; religando em {wait:.0f} s", flush=True)
            self.stopping.wait(wait)
            wait = min(wait * 2, 30.0)

    def stop(self) -> None:
        if self.stop_at is None:
            self.stop_at = time.monotonic()
        self.stopping.set()
        with self.lock:
            p = self.proc
        if p is not None and p.poll() is None:
            with contextlib.suppress(OSError):
                p.send_signal(signal.SIGTERM)

    def remaining(self, budget: float = STOP_BUDGET_S) -> float:
        """How much longer to wait for the jobs process, counted from SIGTERM."""
        if self.stop_at is None:
            return budget
        return max(0.2, budget - (time.monotonic() - self.stop_at))

    def join(self, timeout: float | None = None) -> None:
        self.stop()
        with self.lock:
            p = self.proc
        if p is None:
            return
        try:
            p.wait(self.remaining() if timeout is None else timeout)
        except subprocess.TimeoutExpired:
            p.kill()


def hub_multiprocess_class() -> type:
    """Multiprocess uvicorn supervisor that forwards SIGTERM/SIGINT to the jobs process
    **inside its own signal handler** (uvicorn's only queues the signal; with
    ``workers>1`` the parent process has no lifespan, so the SDK hook would not fire)."""
    import signal as _signal

    from uvicorn.supervisors import Multiprocess

    class HubMultiprocess(Multiprocess):
        def __init__(self, config: Any, sockets: list[Any], on_stop: Callable[[], None] | None) -> None:
            super().__init__(config, sockets=sockets)
            self._on_stop = on_stop
            for sig in (_signal.SIGTERM, _signal.SIGINT):
                with contextlib.suppress(ValueError, OSError):
                    _signal.signal(sig, self._on_signal)

        def _on_signal(self, sig: int, _frame: Any) -> None:
            if self._on_stop is not None:
                self._on_stop()
            self.signal_queue.append(sig)

    return HubMultiprocess


def serve() -> int:
    import uvicorn

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    host = os.environ.get("COLETOR_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    workers = max(1, int(os.environ.get("UVICORN_WORKERS", "1")))
    mode = os.environ.get("HUB_JOBS_MODE", "inline").strip().lower()
    print(f"coletor: http://{host}:{port}/coletor ({workers} worker(s) web · tarefas: {mode})", flush=True)
    sup = WorkerSupervisor() if mode == "process" else None
    if sup is not None:
        sup.start()
    opts: dict[str, Any] = {
        "factory": True,
        "host": host,
        "port": port,
        "workers": workers,
        "timeout_graceful_shutdown": 8,
        "server_header": False,
        "access_log": False,
        "log_level": "warning",
        "lifespan": "on",
    }
    if workers > 1:
        config = uvicorn.Config("coletor.app:create_app", **opts)
        sock = config.bind_socket()
        hub_multiprocess_class()(config, [sock], sup.stop if sup is not None else None).run()
    else:
        uvicorn.run("coletor.app:create_app", **opts)
    if sup is not None:
        sup.join()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "serve"
    if cmd == "--health":
        return health()
    if cmd == "openapi":
        # Wave 11: OpenAPI of API v1 (`hub pack` embeds it as openapi.json).
        from coletor.app import create_app

        doc = json.dumps(create_app().state.hub_api_doc(), ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.buffer.write(doc.encode("utf-8") + b"\n")
        return 0
    if cmd == "serve":
        return serve()
    if cmd == "worker":
        import asyncio

        from coletor.app import run_worker

        logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
        asyncio.run(run_worker())
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
