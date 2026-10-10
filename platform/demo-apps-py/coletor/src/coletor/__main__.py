"""``python -m coletor [serve|worker|--health|openapi]``.

- ``serve`` (padrão): uvicorn em ``COLETOR_HOST`` (padrão 127.0.0.1; a imagem usa
  0.0.0.0) e ``PORT`` (padrão 8080), com ``UVICORN_WORKERS`` processos (padrão 1 — ver
  o dimensionamento em ``loadtest/RESULTADOS.md``) e 8 s de drenagem no SIGTERM. Com
  ``HUB_JOBS_MODE=process`` (padrão da imagem) inicia e supervisiona também o processo de
  tarefas (``worker``): religado com espera crescente se morrer (o web continua de pé) e recebe o SIGTERM **na hora**, em paralelo com a drenagem do
  web — com 1 processo web pelo gancho do lifespan; com ``UVICORN_WORKERS>1`` pelo tratador
  de sinal do processo pai (:class:`HubMultiprocess`). Total ≤ 9,5 s (``docker stop`` = 10 s).
  Se o processo de tarefas morrer por SIGKILL (OOM do cgroup), o próximo sobe com
  ``HUB_JOBS_KILLED_PID`` e marca ``failed`` a tarefa que estava na fase de memória.
- ``worker``: só o processo de tarefas duráveis.
- ``--health``: ``GET /coletor`` no próprio app (HEALTHCHECK da imagem, sem curl);
  sai 0 com 200.
- ``openapi``: OpenAPI da API v1 (o ``hub pack`` embute como openapi.json).
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


# Prazo total do desligamento, abaixo dos 10 s do `docker stop`.
STOP_BUDGET_S = 9.5
KILLED_PID_ENV = "HUB_JOBS_KILLED_PID"


def killed_by_sigkill(code: int | None) -> bool:
    """Saída por SIGKILL (Popen: -9; shell: 137) — o que o OOM killer do cgroup faz."""
    return code in (-9, 137)


class WorkerSupervisor:
    """Processo de tarefas filho do web: religado com espera crescente (1 → 30 s) se sair
    sem ser pedido; SIGTERM repassado na hora quando o web começa a desligar."""

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
        """Quanto ainda cabe esperar o processo de tarefas, contando desde o SIGTERM."""
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
    """Supervisor multiprocesso do uvicorn que repassa o SIGTERM/SIGINT ao processo de
    tarefas **no próprio tratador de sinal** (o do uvicorn só enfileira o sinal; com
    ``workers>1`` o processo pai não tem lifespan, então o gancho do SDK não dispararia)."""
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
        # Onda 11: OpenAPI da API v1 (o `hub pack` embute como openapi.json).
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
