"""Modo de desenvolvimento: `HUB_DEV_MODE=1`, só via `scripts/dev.sh` e o overlay
`docker-compose.dev.yml` — nunca em produção.

- assets sempre `no-store` e lidos do disco (já é assim em `hub.app`); código com `uvicorn --reload`;
- recarga ao vivo opcional (`HUB_DEV_RELOAD=1`): SSE `GET /_dev/reload`, que avisa o navegador quando algo em
  `HUB_DEV_WATCH` muda (a rota **não existe** fora do modo dev com recarga; o shell põe
  `<meta name="hub-dev-reload">` e o `hub-ux.js` escuta);
- `startup_check` recusa subir com `HUB_DEV_MODE=1` junto de configurações de produção.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

_TRUE = ("1", "true", "yes", "sim", "on")


def enabled() -> bool:
    return os.environ.get("HUB_DEV_MODE", "").strip().lower() in _TRUE


def reload_enabled() -> bool:
    return enabled() and os.environ.get("HUB_DEV_RELOAD", "").strip().lower() in _TRUE


def production_markers(get: Callable[[str], str | None] = os.environ.get) -> list[str]:
    """Sinais de produção que tornam o modo dev proibido."""

    def val(k: str) -> str:
        return (get(k) or "").strip().lower()

    out: list[str] = []
    if val("HUB_DEPLOYMENT") == "production":
        out.append("HUB_DEPLOYMENT=production")
    if val("HUB_DEMO_USERS") in ("0", "false", "no", "off"):
        out.append("HUB_DEMO_USERS=0")
    if val("HUB_SSO_ENABLED") in _TRUE:
        out.append("HUB_SSO_ENABLED")
    out += [k for k in ("HUB_MASTER_KEY_FILE", "HUB_USER_SIG_KEY", "HUB_BOOTSTRAP_ADMIN_EMAIL") if val(k)]
    return out


def startup_check() -> None:
    if not enabled():
        return
    if m := production_markers():
        raise SystemExit(
            f"hub: RECUSADO — HUB_DEV_MODE=1 com configuração de produção ({', '.join(m)}). "
            "O modo de desenvolvimento serve assets sem cache e expõe /_dev/reload; nunca em produção."
        )
    print(f"hub: MODO DE DESENVOLVIMENTO (recarga ao vivo: {'ligada' if reload_enabled() else 'desligada'})")


def parse_watch(raw: str) -> list[tuple[Path, float]]:
    """`caminho[@atraso_ms]` separados por vírgula; o atraso dá tempo de o serviço reiniciar."""
    out: list[tuple[Path, float]] = []
    for item in (s.strip() for s in raw.split(",")):
        if not item:
            continue
        path, _, ms = item.rpartition("@")
        out.append((Path(path), int(ms) / 1000) if path and ms.isdigit() else (Path(item), 0.0))
    return out


def fingerprint(root: Path) -> int:
    """Impressão digital de uma árvore (nomes, tamanhos, datas): mudou = algo mudou (sem inotify no Docker)."""
    h, n = 0, 0
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__")
        for f in sorted(files):
            if f.startswith("."):
                continue
            n += 1
            if n > 20_000:
                return h
            p = Path(dirpath, f)
            with contextlib.suppress(OSError):
                st = p.stat()
                h = hash((h, str(p), st.st_size, st.st_mtime_ns))
    return h


class Reloader:
    def __init__(self) -> None:
        self.boot = str(int(time.time() * 1000))
        self.subs: set[asyncio.Queue[str]] = set()

    def notify(self) -> None:
        for q in self.subs:
            q.put_nowait("reload")

    async def stream(self) -> AsyncIterator[str]:
        q: asyncio.Queue[str] = asyncio.Queue()
        self.subs.add(q)
        try:
            yield f"event: hello\ndata: {self.boot}\n\n"
            while True:
                try:
                    await asyncio.wait_for(q.get(), 15)
                    yield "event: reload\ndata: 1\n\n"
                except TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            self.subs.discard(q)

    async def watch(self, watch: list[tuple[Path, float]], every: float = 0.7) -> None:
        last = [fingerprint(d) for d, _ in watch]
        while True:
            await asyncio.sleep(every)
            for i, (d, delay) in enumerate(watch):
                if (f := await asyncio.to_thread(fingerprint, d)) != last[i]:
                    last[i] = f
                    print(f"hub(dev): mudança em {d}")
                    asyncio.get_running_loop().call_later(delay + 0.15, self.notify)


def mount(app: FastAPI) -> Callable[[], None]:
    """Registra `/_dev/reload` (só com recarga ligada) e devolve `start()` do vigia (para o lifespan)."""
    if not reload_enabled():
        return lambda: None
    rl = Reloader()

    @app.get("/_dev/reload")
    async def reload_events() -> StreamingResponse:
        return StreamingResponse(
            rl.stream(), media_type="text/event-stream", headers={"cache-control": "no-store"}
        )

    def start() -> None:
        watch = parse_watch(os.environ.get("HUB_DEV_WATCH", ""))
        if watch:
            app.state.dev_watch = asyncio.ensure_future(rl.watch(watch))

    return start
