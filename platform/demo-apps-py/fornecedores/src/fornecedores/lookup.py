"""Cliente da API externa de CNPJ/score com timeout por tentativa, novas tentativas com
backoff exponencial + jitter (só falhas transitórias) e disjuntor."""

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
    """``info`` preenchido = sucesso; senão contingência (``reason`` + último resultado bom)."""

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
        """Segundos restantes com o disjuntor aberto (0 = fechado)."""
        return int(max(0.0, self.open_until - time.monotonic())) if self.open_until is not None else 0

    def _get(self, url: str) -> CnpjInfo:
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
        if r.status >= 500:
            raise _Fail(True, f"serviço respondeu {r.status}")
        if r.status >= 300:
            raise _Fail(False, f"consulta recusada ({r.status})")
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

    async def lookup(self, cnpj: str) -> Outcome:
        d = digits(cnpj)
        if self.base is None:
            return Outcome(
                0, reason="Serviço de consulta não configurado (HUB_EXTAPI_URL).", cached=self.last_good(d)
            )
        if self.open_until is not None:
            left = self.open_until - time.monotonic()
            if left > 0:
                return Outcome(
                    0,
                    reason=f"Serviço de consulta instável: novas consultas suspensas por mais {max(1, int(left))} s "
                    f"após {OPEN_AFTER} falhas seguidas.",
                    cached=self.last_good(d),
                )
            self.open_until = None  # meio-aberto: esta consulta é o teste
            self.failures = OPEN_AFTER - 1
        url = f"{self.base}/cnpj/{d}"
        t0 = time.monotonic()
        last, attempts = "", 0
        for n in range(ATTEMPTS):
            attempts = n + 1
            try:
                info = await self._attempt(url)
            except _Fail as e:
                last = str(e)
                if not e.retry:
                    break
                if n + 1 < ATTEMPTS:
                    await asyncio.sleep(self.backoff * 2**n + random.random() * 0.1)  # noqa: S311
            else:
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
