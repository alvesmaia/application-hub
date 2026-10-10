"""Limite de operações pesadas simultâneas por app (importação, exportação, relatório).

Mesma regra do Rust: sem vaga, 429 **imediato** com ``Retry-After`` (o ``hub.js`` mostra
"em fila…" e tenta de novo sozinho). A vaga é liberada quando o *permit* é liberado —
num download em streaming, só no fim do corpo.

Observação (Python): o semáforo vale **por processo**. Com ``uvicorn --workers N`` o
limite efetivo do app é ``N × HUB_HEAVY_MAX`` — o dimensionamento recomendado
(``loadtest/RESULTADOS.md``, onda 7) é 1 worker por vCPU.
"""

from __future__ import annotations

import contextlib
import os
import threading
import weakref
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any

from starlette.responses import StreamingResponse
from typing_extensions import override

DEFAULT_MAX = 2


class Permit:
    """Vaga ocupada; ``release()`` (ou sair do ``with``) devolve. Liberar duas vezes é
    inócuo."""

    def __init__(self, heavy: Heavy) -> None:
        self._heavy = heavy
        self._released = False

    def release(self) -> None:
        with self._heavy._lock:
            if self._released:
                return
            self._released = True
            self._heavy._used -= 1

    def __enter__(self) -> Permit:
        return self

    def __exit__(
        self,
        et: type[BaseException] | None,
        e: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


class Heavy:
    def __init__(self, max_: int = DEFAULT_MAX) -> None:
        self.max = max(1, max_)
        self._used = 0
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> Heavy:
        try:
            n = int(os.environ.get("HUB_HEAVY_MAX", DEFAULT_MAX))
        except ValueError:
            n = DEFAULT_MAX
        return cls(n)

    def try_acquire(self) -> Permit | None:
        """Vaga sem esperar; ``None`` = limite atingido (responda 429)."""
        with self._lock:
            if self._used >= self.max:
                return None
            self._used += 1
        return Permit(self)

    @property
    def in_use(self) -> int:
        with self._lock:
            return self._used


class PermitStreamingResponse(StreamingResponse):
    """``StreamingResponse`` que **sempre** devolve a vaga de operação pesada:

    - no fim de ``__call__`` (corpo enviado, erro, cliente desconectado antes ou durante o
      envio, cancelamento pela desconexão) — e fecha o gerador, liberando a conexão do banco;
    - se a resposta nunca for enviada (ex.: exceção num middleware antes do envio), quando
      o objeto é coletado (``weakref.finalize``).

    Liberar mais de uma vez é inócuo (:meth:`Permit.release`)."""

    def __init__(self, content: AsyncIterator[bytes], permit: Permit, **kw: Any) -> None:
        super().__init__(content, **kw)
        self._gen = content
        self.permit = permit
        weakref.finalize(self, permit.release)

    @override
    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.permit.release()
            aclose = getattr(self._gen, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()
