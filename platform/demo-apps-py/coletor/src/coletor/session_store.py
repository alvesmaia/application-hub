"""Test session (the queue of codes to read) of each user: the 24 codes, the shuffle, storage and
the screen context."""

from __future__ import annotations

import json
import os
import random
from typing import Any

from hub_sdk.locale import t

from coletor import barcodes, fila
from coletor.model import catalog, locations, orders
from coletor.reads import FORMAT_LABELS

N_SESSION_CODES = 24


def session_codes() -> list[fila.Code]:
    """The 24 codes of the session (the order is shuffled at each start/restart)."""
    items = catalog()
    locs = locations()
    ords = orders()
    out: list[tuple[str, str, str]] = []
    out += [("ean_13", it.code[:12], t("Item: {n}", n=it.name)) for it in items[:8]]
    out += [("code_39", loc.code, t("Local: {c} · {n}", c=loc.code, n=loc.name)) for loc in locs[:6]]
    out += [("qr_code", o.code, t("Pedido {c} · {s}", c=o.code, s=o.supplier)) for o in ords]
    out += [("code_128", it.code128, t("Item: {n}", n=it.name)) for it in items[3:6]]
    out += [
        ("ean_8", "9638507", t("Formato EAN-8")),
        ("upc_a", "03600029145", t("Formato UPC-A")),
        ("code_128", items[1].code128, t("Item: {n}", n=items[1].name)),
        ("data_matrix", items[2].code128, t("Item: {n}", n=items[2].name)),
    ]
    codes = []
    for fmt, value, label in out:
        full = barcodes.encode(fmt, value).text
        codes.append(fila.Code(fmt=fmt, value=full, expected=fila.match_key(full), label=label))
    return codes


_SEEDED: random.Random | None = None


def rng() -> random.Random:
    """Order shuffle: ``COLETOR_SHUFFLE_SEED`` fixes the process seed (tests: the
    shuffle sequence is reproducible and each start/restart shuffles again); without it,
    aleatório."""
    global _SEEDED
    seed = os.environ.get("COLETOR_SHUFFLE_SEED", "").strip()
    if seed.isdigit():
        if _SEEDED is None:
            _SEEDED = random.Random(int(seed))  # noqa: S311 — test order
        return _SEEDED
    return random.Random(random.SystemRandom().randrange(1 << 62))  # noqa: S311


async def load_session(c: Any, user: str, lock: bool = False) -> fila.Session | None:
    query = "SELECT state FROM col_sessions WHERE user_name = $1" + (" FOR UPDATE" if lock else "")  # noqa: S608 -- lock is a fixed SQL fragment; user data is bound
    raw = await c.fetchval(query, user)
    if raw is None:
        return None
    try:
        return fila.Session.from_json(json.loads(raw) if isinstance(raw, str) else raw)
    except (ValueError, TypeError, KeyError):
        return None


async def save_session(c: Any, user: str, s: fila.Session) -> None:
    await c.execute(
        "INSERT INTO col_sessions (user_name, state, updated_at) VALUES ($1, $2::jsonb, now()) "
        "ON CONFLICT (user_name) DO UPDATE SET state = $2::jsonb, updated_at = now()",
        user,
        json.dumps(s.to_json()),
    )


def session_view(s: fila.Session | None) -> dict[str, Any]:
    """Context of the session screens (sheet and phone)."""
    if s is None:
        return {"sess": None}
    cur = s.current()
    res = []
    for r in reversed(s.results[-40:]):
        res.append(r | {"fmt_label": FORMAT_LABELS.get(r.get("fmt", ""), r.get("fmt", ""))})
    sm = fila.summary(s)
    return {
        "sess": s,
        "cur": cur,
        "cur_fmt": FORMAT_LABELS.get(cur.fmt, cur.fmt) if cur else "",
        "shown": min(s.pos + 1, s.total),
        "results": res,
        "summary": sm,
        "avg_s": f"{sm.avg_ms / 1000:.1f}".replace(".", ","),
        "total_s": f"{sm.total_ms / 1000:.0f}",
        "pct": round(100 * min(s.pos, s.total) / s.total) if s.total else 0,
    }
