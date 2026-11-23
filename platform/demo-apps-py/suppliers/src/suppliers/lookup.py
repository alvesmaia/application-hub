"""Client for the external CNPJ/score API with a timeout per attempt, retries with
exponential backoff + jitter (transient failures only) and a circuit breaker."""

from __future__ import annotations

import asyncio
import http.client
import json
import random
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from hub_sdk.db import app_env

ATTEMPTS = 3
OPEN_AFTER = 5
OPEN_FOR = 30.0


def digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


@dataclass(frozen=True)
class CnpjInfo:
    cnpj: str
    razao_social: str
    situacao: str
    score: int
    risco: str
    consultado_em: str

    def to_json(self) -> dict[str, Any]:
        return {
            "cnpj": self.cnpj,
            "razao_social": self.razao_social,
            "situacao": self.situacao,
            "score": self.score,
            "risco": self.risco,
            "consultado_em": self.consultado_em,
        }


@dataclass(frozen=True)
class Outcome:
    """``info`` filled = success; otherwise contingency (``reason`` + last good result)."""

    attempts: int
    info: CnpjInfo | None = None
    ms: int = 0
    reason: str = ""
    cached: tuple[CnpjInfo, int] | None = None


class _Fail(Exception):
    def __init__(self, retry: bool, msg: str) -> None:
        super().__init__(msg)
        self.retry = retry


class Lookup:
    def __init__(self, base: str | None, timeout: float = 1.5, backoff: float = 0.2) -> None:
        self.base = (base.rstrip("/") or None) if base is not None else None
        self.timeout = timeout
        self.backoff = backoff
        self.failures = 0
        self.open_until: float | None = None
        self._last_good: dict[str, tuple[CnpjInfo, float]] = {}

    @classmethod
    def from_env(cls, app_id: str) -> Lookup:
        try:
            ms = int(app_env(app_id, "HUB_EXTAPI_TIMEOUT_MS") or 1500)
        except ValueError:
            ms = 1500
        return cls(app_env(app_id, "HUB_EXTAPI_URL"), ms / 1000)

    def last_good(self, cnpj: str) -> tuple[CnpjInfo, int] | None:
        hit = self._last_good.get(digits(cnpj))
        return (hit[0], int(time.monotonic() - hit[1])) if hit else None

    def open_for(self) -> int:
        """Seconds left with the circuit breaker open (0 = closed)."""
        return int(max(0.0, self.open_until - time.monotonic())) if self.open_until is not None else 0

    def _fetch(self, url: str) -> tuple[int, bytes]:
        """(HTTP status, body); network failures become retryable :class:`_Fail`."""
        u = urlsplit(url)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        try:
            conn = cls(u.netloc, timeout=self.timeout)
            try:
                conn.request("GET", u.path)
                r = conn.getresponse()
                raw = r.read()
            finally:
                conn.close()
        except TimeoutError as e:
            raise _Fail(True, f"sem resposta em {int(self.timeout * 1000)} ms") from e
        except ConnectionError as e:
            raise _Fail(True, "falha de conexão") from e
        except OSError as e:
            raise _Fail(True, str(e)) from e
        return r.status, raw

    def _get(self, url: str) -> CnpjInfo:
        status, raw = self._fetch(url)
        if status >= 500:
            raise _Fail(True, f"serviço respondeu {status}")
        if status >= 300:
            raise _Fail(False, f"consulta recusada ({status})")
        try:
            d = json.loads(raw)
            return CnpjInfo(
                str(d["cnpj"]),
                str(d["razao_social"]),
                str(d["situacao"]),
                int(d["score"]),
                str(d["risco"]),
                str(d["consultado_em"]),
            )
        except (ValueError, KeyError, TypeError) as e:
            raise _Fail(False, f"resposta inválida: {e}") from e

    async def _attempt(self, url: str) -> CnpjInfo:
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._get, url), self.timeout + 0.5)
        except TimeoutError as e:
            raise _Fail(True, f"sem resposta em {int(self.timeout * 1000)} ms") from e

    def _circuit_refusal(self, d: str) -> Outcome | None:
        """Contingency while the breaker is open; once it expires, half-opens it (this lookup is the test)."""
        if self.open_until is None:
            return None
        left = self.open_until - time.monotonic()
        if left > 0:
            return Outcome(
                0,
                reason=f"Serviço de consulta instável: novas consultas suspensas por mais {max(1, int(left))} s "
                f"após {OPEN_AFTER} falhas seguidas.",
                cached=self.last_good(d),
            )
        self.open_until = None  # half-open: this lookup is the test
        self.failures = OPEN_AFTER - 1
        return None

    async def _with_retries(self, url: str) -> tuple[CnpjInfo | None, int, str]:
        """(info | ``None``, attempts made, last failure)."""
        last, attempts = "", 0
        for n in range(ATTEMPTS):
            attempts = n + 1
            try:
                return await self._attempt(url), attempts, last
            except _Fail as e:
                last = str(e)
                if not e.retry:
                    break
                if n + 1 < ATTEMPTS:
                    await asyncio.sleep(self.backoff * 2**n + random.random() * 0.1)  # noqa: S311
        return None, attempts, last

    async def lookup(self, cnpj: str) -> Outcome:
        d = digits(cnpj)
        if self.base is None:
            return Outcome(
                0, reason="Serviço de consulta não configurado (HUB_EXTAPI_URL).", cached=self.last_good(d)
            )
        if (refused := self._circuit_refusal(d)) is not None:
            return refused
        t0 = time.monotonic()
        info, attempts, last = await self._with_retries(f"{self.base}/cnpj/{d}")
        if info is not None:
            self.failures = 0
            self._last_good[d] = (info, time.monotonic())
            return Outcome(attempts, info=info, ms=int((time.monotonic() - t0) * 1000))
        self.failures += 1
        if self.failures >= OPEN_AFTER:
            self.open_until = time.monotonic() + OPEN_FOR
        return Outcome(
            attempts,
            reason=f"A consulta falhou após {attempts} tentativa(s): {last}.",
            cached=self.last_good(d),
        )
