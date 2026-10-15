"""Ticket approvals through the hub Inbox: the request (when opening) and the decision."""

from __future__ import annotations

import os
import re
from typing import Any

from hub_sdk import approvals as ap
from hub_sdk import base, outbox
from hub_sdk.app import HubSdk

from servicos.state import to_i64
from servicos.ui import url_enc


def approver_roles() -> list[str]:
    raw = os.environ.get("HUB_SERVICOS_APPROVER_ROLES", "Administrador")
    return [r.strip() for r in raw.split(",") if r.strip()]


async def request_approval(
    c: Any, ticket_id: int, protocol: str, title: str, requester_login: str, requester_name: str
) -> None:
    """Approval request for a newly opened ticket (in the opening transaction)."""
    await c.execute(
        "INSERT INTO ticket_approvals (ticket_id, requested_by, requested_by_name) VALUES ($1, $2, $3) "
        "ON CONFLICT DO NOTHING",
        ticket_id,
        requester_login,
        requester_name,
    )
    ev = outbox.approval_request(
        requester_login,
        f"sd-{ticket_id}",
        f"Aprovar chamado {protocol}",
        f"{requester_name} pediu aprovação: {title}",
        f"{base('servicos')}/requests?protocolo={url_enc(protocol)}",
        to_roles=approver_roles(),
        level="warning",
    )
    await outbox.write(c, ev)


async def _pending(c: Any, tid: int, approver: str) -> Any:
    """The approval row, locked; raises unless it is still pending and not the requester's own."""
    row = await c.fetchrow(
        "SELECT a.status, a.requested_by, t.protocol, t.title FROM ticket_approvals a "
        "JOIN tickets t ON t.id = a.ticket_id WHERE a.ticket_id = $1 FOR UPDATE OF a",
        tid,
    )
    if row is None:
        raise ap.NotFound
    if row["status"] != "pending":
        raise ap.AlreadyDecided(row["status"])
    if approver.lower() == row["requested_by"].lower():
        raise ap.Forbidden("quem pediu não aprova o próprio chamado")
    return row


async def _apply(c: Any, tid: int, d: ap.ApprovalDecision) -> None:
    ticket_status = "Em atendimento" if d.status == "approved" else "Fechado"
    await c.execute(
        "UPDATE ticket_approvals SET status = $2, decided_by = $3, decided_at = now(), comment = $4 "
        "WHERE ticket_id = $1",
        tid,
        d.status,
        d.approver.user,
        d.comment,
    )
    await c.execute(
        "UPDATE tickets SET status = $2::text, version = version + 1, updated_at = now(), "
        "resolved_at = CASE WHEN $2::text = 'Fechado' THEN now() ELSE resolved_at END WHERE id = $1",
        tid,
        ticket_status,
    )


async def _announce(c: Any, row: Any, d: ap.ApprovalDecision) -> None:
    """Audit + notification to the requester."""
    verb = "aprovado" if d.status == "approved" else "recusado"
    who = d.approver.display_name
    proto, title = row["protocol"], row["title"]
    await outbox.write(
        c,
        outbox.audit(
            who,
            f"Chamado {verb} pela Inbox",
            proto,
            f"{title} · {d.comment}" if d.comment else title,
        ),
    )
    await outbox.write(
        c,
        outbox.notification(
            who,
            f"Chamado {proto} {verb}",
            f"{who} {verb} o seu pedido: {title}",
            f"{base('servicos')}/requests?protocolo={url_enc(proto)}",
            to_users=[row["requested_by"]],
            level="success" if d.status == "approved" else "warning",
        ),
    )


def make_approvals(sdk: HubSdk) -> Any:
    """Decision coming from the hub Inbox (``POST /servicos/_hub/approvals/sd-<id>``): approved → "In
    progress"; rejected → "Closed"; audit + notification to the requester, in the same transaction."""

    async def decide(d: ap.ApprovalDecision) -> str:
        m = re.fullmatch(r"sd-([0-9]+)", d.approval_id)
        if m is None or (tid := to_i64(m.group(1))) is None:
            raise ap.NotFound
        if sdk.pool is None:
            raise RuntimeError("sem banco")
        async with sdk.pool.acquire() as c, c.transaction():
            row = await _pending(c, tid, d.approver.user)
            await _apply(c, tid, d)
            await _announce(c, row, d)
        return d.status

    return decide
