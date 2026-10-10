"""Cache de sessão por usuário em memória, limitado (LRU): guarda os últimos filtros e os
relatórios já calculados da sessão, para medir quanto de RAM cada usuário ativo custa; ao passar
da capacidade, a menos usada recentemente é descartada.

``HUB_SESSION_CAPACITY`` (padrão 2000 sessões) e ``HUB_SESSION_REPORTS`` (relatórios guardados
por sessão, padrão 3).
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

from servicos.report import Report

SESSION_BYTES = 88


@dataclass
class Session:
    created: float = field(default_factory=time.monotonic)
    hits: int = 0
    last_filters: str = ""
    # (chave dos parâmetros, resultado), do mais recente para o mais antigo.
    reports: list[tuple[str, Report]] = field(default_factory=list)

    def approx_bytes(self) -> int:
        return (
            SESSION_BYTES + len(self.last_filters) + sum(len(k) + r.approx_bytes() for k, r in self.reports)
        )


@dataclass(frozen=True)
class Stats:
    sessions: int
    capacity: int
    approx_bytes: int


class Sessions:
    def __init__(self, capacity: int = 2000, per_session: int = 3) -> None:
        self.capacity = max(1, capacity)
        self.per_session = max(1, per_session)
        self._lru: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> Sessions:
        def get(k: str, d: int) -> int:
            try:
                return int(os.environ.get(k, d))
            except ValueError:
                return d

        return cls(get("HUB_SESSION_CAPACITY", 2000), get("HUB_SESSION_REPORTS", 3))

    def _get(self, key: str) -> Session:
        s = self._lru.get(key)
        if s is None:
            s = self._lru[key] = Session()
            while len(self._lru) > self.capacity:
                self._lru.popitem(last=False)
        else:
            self._lru.move_to_end(key)
        return s

    def touch(self, key: str, f: Callable[[Session], None]) -> None:
        """Cria/atualiza a sessão e aplica ``f``."""
        with self._lock:
            s = self._get(key)
            s.hits += 1
            f(s)

    def get_report(self, key: str, params: str) -> Report | None:
        with self._lock:
            s = self._lru.get(key)
            if s is None:
                return None
            self._lru.move_to_end(key)
            s.hits += 1
            return next((r for k, r in s.reports if k == params), None)

    def put_report(self, key: str, params: str, r: Report) -> None:
        def add(s: Session) -> None:
            s.reports = [(k, x) for k, x in s.reports if k != params]
            s.reports.insert(0, (params, r))
            del s.reports[self.per_session :]

        self.touch(key, add)

    def stats(self) -> Stats:
        with self._lock:
            return Stats(
                len(self._lru), self.capacity, sum(len(k) + s.approx_bytes() for k, s in self._lru.items())
            )
