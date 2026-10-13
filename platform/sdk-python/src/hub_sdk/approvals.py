"""Aprovações pela Inbox do hub (onda 9).

1. O app publica o pedido no outbox (:func:`hub_sdk.outbox.approval_request`).
2. O hub mostra o pedido a quem pode decidir (permissão ``aprovar_solicitacoes``, destino do
   pedido, app na lista do usuário; nunca quem pediu).
3. A decisão volta pela rede interna, com o token interno:
   ``POST /{app}/_hub/approvals/{approval_id}``, ``x-hub-user`` = quem decidiu e corpo
   ``{"decision": "approve"|"reject", "comment": "…", "event_id": "evt-…", "decided_at": ms}``.
4. O SDK valida token, id, decisão e a permissão do aprovador; a função do app aplica
   (idempotente) e devolve ``"approved"``/``"rejected"``. Já decidido → levante
   :class:`AlreadyDecided` (409 com o estado atual); desconhecido → :class:`NotFound` (404);
   regra de negócio → :class:`Forbidden` (403).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from hub_sdk.identity import Identity

APPROVE_PERMISSION = "aprovar_solicitacoes"
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def valid_id(s: str) -> bool:
    return bool(_ID.match(s))


@dataclass(frozen=True)
class ApprovalDecision:
    approval_id: str
    decision: str  # "approve" | "reject"
    comment: str
    event_id: str
    decided_at: int
    approver: Identity

    @property
    def status(self) -> str:
        return "approved" if self.decision == "approve" else "rejected"


class NotFound(Exception):
    pass


class AlreadyDecided(Exception):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class Forbidden(Exception):
    pass


ApprovalFn = Callable[[ApprovalDecision], Awaitable[str]]
