"""Prontidão e vivacidade (onda 12, item 2.5 — docs/CONTRATO-APP.md §2).

====== ========================== ==============================================================
Método Rota                       Resposta
====== ========================== ==============================================================
GET    ``/{app}/_hub/health``     vivacidade: sempre 200 ``{"status": "ok"}``
GET    ``/{app}/_hub/ready``      prontidão: 200 ``{"status": "ready", "checks": {...}}`` ou
                                  503 ``{"status": "not_ready", "reason": "...", "checks": ...}``
====== ========================== ==============================================================

O hub só troca o tráfego do blue/green para um candidato cujo ``/_hub/ready`` respondeu 200;
sem a rota (404) a implantação é recusada. Sem token interno (não expõem dados; o gateway
recusa ``/_frag/{app}/_hub/*``); ``Cache-Control: no-store``. Mesmo formato do SDK Rust.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

#: Tempo máximo de cada verificação (s).
CHECK_TIMEOUT = 1.5

Check = Callable[[], Awaitable[None]]
"""Verificação: retorna normalmente = pronta; levanta ``NotReady``/exceção = não pronta."""


class NotReady(Exception):
    """Motivo legível de "não pronto"."""


class Readiness:
    def __init__(self) -> None:
        self._checks: list[tuple[str, Check]] = []

    def check(self, name: str, fn: Check) -> Readiness:
        self._checks.append((name, fn))
        return self

    def flag(self, name: str, reason: str, fn: Callable[[], bool]) -> Readiness:
        async def _c() -> None:
            if not fn():
                raise NotReady(reason)

        return self.check(name, _c)

    def __len__(self) -> int:
        return len(self._checks)

    async def evaluate(self) -> tuple[bool, dict[str, Any]]:
        checks: dict[str, str] = {}
        reason: str | None = None
        for name, fn in self._checks:
            try:
                await asyncio.wait_for(fn(), CHECK_TIMEOUT)
                checks[name] = "ok"
            except TimeoutError:
                msg = f"sem resposta em {int(CHECK_TIMEOUT * 1000)} ms"
                checks[name] = msg
                reason = reason or f"{name}: {msg}"
            except Exception as e:
                msg = (str(e) or type(e).__name__)[:200]
                checks[name] = msg
                reason = reason or f"{name}: {msg}"
        if reason is None:
            return True, {"status": "ready", "checks": checks}
        return False, {"status": "not_ready", "reason": reason, "checks": checks}


def router(app_id: str, readiness: Readiness | Callable[[], Readiness]) -> APIRouter:
    r = APIRouter(include_in_schema=False)
    base = f"/{app_id}/_hub"
    headers = {"Cache-Control": "no-store"}

    @r.get(f"{base}/ready")
    async def ready() -> Any:
        rd = readiness if isinstance(readiness, Readiness) else readiness()
        ok, body = await rd.evaluate()
        return JSONResponse(body, status_code=200 if ok else 503, headers=headers)

    @r.get(f"{base}/health")
    async def health() -> Any:
        return JSONResponse({"status": "ok"}, headers=headers)

    return r
