"""Inbox approvals of the hub (wave 9).

1. The app publishes the request to the outbox (:func:`hub_sdk.outbox.approval_request`).
2. The hub shows the request to whoever can decide (permission ``aprovar_solicitacoes``, target of the
   request, app in the user's list; never the requester).
3. The decision comes back over the internal network, with the internal token:
   ``POST /{app}/_hub/approvals/{approval_id}``, ``x-hub-user`` = quem decidiu e corpo
   ``{"decision": "approve"|"reject", "comment": "…", "event_id": "evt-…", "decided_at": ms}``.
4. The SDK validates token, id, decision and the approver's permission; the app's function applies
   (idempotently) and returns ``"approved"``/``"rejected"``. Already decided → raise
   :class:`AlreadyDecided` (409 with the current state); unknown → :class:`NotFound` (404);
   business rule → :class:`Forbidden` (403).
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
