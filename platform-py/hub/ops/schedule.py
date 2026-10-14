"""Backup schedules: labels in Portuguese UI text ("Diário 02:00", "A cada 6 h", "Manual") or a 5-field cron."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hub.ops.model import OpsError

_DOW = ("dom", "seg", "ter", "qua", "qui", "sex", "sáb")
_FIELD = re.compile(r"^(\*|\d+(-\d+)?)(/\d+)?(,(\*|\d+(-\d+)?)(/\d+)?)*$")


def _hm(x: str) -> tuple[int, int]:
    try:
        h, m = (int(p) for p in x.strip().split(":"))
    except ValueError as e:
        raise OpsError(f"horário inválido: {x}") from e
    if not (0 <= h < 24 and 0 <= m < 60):
        raise OpsError(f"horário inválido: {x}")
    return h, m


def _weekly(low: str) -> str:
    parts = low[7:].split()
    day = parts[0] if parts else ""
    dow = next(
        (i for i, d in enumerate(_DOW) if day.startswith(d) or (d == "sáb" and day.startswith("sab"))),
        None,
    )
    if dow is None:
        raise OpsError(f"dia da semana inválido: {day}")
    h, m = _hm(parts[1] if len(parts) > 1 else "")
    return f"{m} {h} * * {dow}"


def _every(t: str, low: str) -> str:
    mt = re.match(r"^(\d+)\s*(h|min)", low[6:].strip())
    if not mt:
        raise OpsError(f"intervalo inválido: {t}")
    n = int(mt.group(1))
    if mt.group(2) == "h" and 1 <= n <= 23:
        return f"0 */{n} * * *"
    if mt.group(2) == "min" and 1 <= n <= 59:
        return f"*/{n} * * * *"
    raise OpsError(f"intervalo inválido: {t}")


def _cron(t: str) -> str:
    fields = t.split()
    bounds = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
    if len(fields) != 5 or not all(
        _FIELD.match(f) and all(lo <= int(n) <= hi for n in re.findall(r"(?<!/)\d+", f))
        for f, (lo, hi) in zip(fields, bounds, strict=True)
    ):
        raise OpsError(f'Agenda inválida: {t!r} (cron de 5 campos ou "Diário 02:00", "A cada 6 h", "Manual")')
    return t


def normalize(s: str) -> str | None:
    """5-field cron, or `None` for manual execution."""
    t = s.strip()
    low = t.lower()
    if low in ("", "manual", "desligado"):
        return None
    for p in ("diário", "diario"):
        if low.startswith(p):
            h, m = _hm(low[len(p) :])
            return f"{m} {h} * * *"
    if low.startswith("semanal"):
        return _weekly(low)
    if low.startswith("a cada"):
        return _every(t, low)
    return _cron(t)


def _field(f: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in f.split(","):
        rng, _, step = part.partition("/")
        a, b = (lo, hi) if rng == "*" else ((int(rng.split("-")[0]), int(rng.split("-")[1])) if "-" in rng else (int(rng), hi if step else int(rng)))  # fmt: skip
        out |= set(range(a, b + 1, int(step or 1)))
    return out


class Cron:
    """5-field cron (minute hour day-of-month month day-of-week; 0 and 7 = Sunday); day-of-month and day-of-week: OR."""

    def __init__(self, expr: str) -> None:
        f = expr.split()
        if len(f) != 5:
            raise OpsError(f"cron precisa de 5 campos: {expr}")
        self.min, self.hour, self.dom = _field(f[0], 0, 59), _field(f[1], 0, 23), _field(f[2], 1, 31)
        self.mon, self.dow = _field(f[3], 1, 12), {d % 7 for d in _field(f[4], 0, 7)}
        self.dom_any, self.dow_any = f[2] == "*", f[4] == "*"

    def _day_ok(self, d: datetime) -> bool:
        dom, dow = d.day in self.dom, (d.weekday() + 1) % 7 in self.dow
        return dom or dow if not (self.dom_any or self.dow_any) else (dom if self.dow_any else dow)

    def next_after(self, after_ms: int, tz: tzinfo) -> int | None:
        """Next occurrence strictly after `after_ms` (epoch ms), in the `tz` time zone."""
        t = datetime.fromtimestamp(after_ms // 1000, tz).replace(
            second=0, microsecond=0, tzinfo=None
        ) + timedelta(minutes=1)
        limit = t + timedelta(days=400)
        while t < limit:
            if t.month not in self.mon:
                t = (t.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0)
            elif not self._day_ok(t):
                t = (t + timedelta(days=1)).replace(hour=0, minute=0)
            elif t.hour not in self.hour:
                t = (t + timedelta(hours=1)).replace(minute=0)
            elif t.minute not in self.min:
                t += timedelta(minutes=1)
            else:
                ms = int(t.replace(tzinfo=tz).timestamp() * 1000)
                if ms > after_ms:
                    return ms
                t += timedelta(minutes=1)
        return None


def timezone_of(name: str = "") -> tzinfo:
    try:
        return ZoneInfo(name or os.getenv("HUB_BACKUP_TZ", "") or "America/Sao_Paulo")
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def jitter_ms(app: str, env: str, slot: int, max_s: int) -> int:
    """Stable delay per (app, env, slot), from 0 to `max_s` s, so the backups do not all start together."""
    if max_s <= 0:
        return 0
    h = int.from_bytes(hashlib.sha256(f"{app}|{env}|{slot}".encode()).digest()[:8], "big")
    return h % (max_s * 1000 + 1)


def plan(st: dict[str, Any] | None, cron: Cron, tz: tzinfo, now: int, grace_ms: int, missed: str,
         jitter: Callable[[int], int]) -> tuple[dict[str, Any], str, int, bool]:  # fmt: skip
    """(new state, "wait"|"run"|"skip", slot, missed): decides what to do with an app×environment's schedule."""

    def fresh(frm: int, prev: dict[str, Any] | None) -> dict[str, Any]:
        nxt = cron.next_after(frm, tz) or 0
        return {**(prev or {}), "next_due": nxt, "run_at": nxt + jitter(nxt), "error": ""}

    if not st or not st.get("next_due"):
        return fresh(now, None), "wait", 0, False
    if now < st["run_at"]:
        return st, "wait", 0, False
    slot, late = st["next_due"], now - st["run_at"]
    nxt = fresh(now, st) | {"last_slot": slot}
    if late <= grace_ms:
        return nxt, "run", slot, False
    if missed == "pular":
        return nxt | {"last_status": "Perdido (pulado)", "last_at": now}, "skip", slot, True
    return nxt, "run", slot, True


def local_label(ms: int, tz: tzinfo) -> str:
    return datetime.fromtimestamp(ms / 1000, tz).strftime("%d/%m %H:%M")
