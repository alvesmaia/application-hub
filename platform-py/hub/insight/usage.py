"""Usage per identified user: per day, (user, app) → opens, navigations, actions and last access.

Collected at the gateway (nothing required from the apps). On by default; `HUB_USAGE=0` turns it off. No IP or form
content. The retention is the same as analytics.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from hub.filelock import file_lock
from hub.insight.analytics import DAY_MS, day_key

KINDS = ("open", "section", "action")
PLATFORM = "_hub"  # the hub's own pages (Home, Inbox, Administration…)


class Usage:
    def __init__(self, directory: Path | None, enabled: bool = True) -> None:
        self.path = directory / "usage.json" if directory else None
        self.enabled, self.dirty = enabled, False
        # day → "user\tapp" → [opens, navigations, actions, last access in ms]
        # Replicas on the same volume: what this replica added since the last write (same format).
        self.pending: dict[str, dict[str, list[int]]] = {}
        self._mtime = 0
        self.days = self._read()

    def _read(self) -> dict[str, dict[str, list[int]]]:
        if self.path and self.path.is_file():
            try:
                self._mtime = self.path.stat().st_mtime_ns
                return dict(json.loads(self.path.read_text(encoding="utf-8")).get("days", {}))
            except (OSError, ValueError, AttributeError):
                return {}
        return {}

    def record(self, user: str, app: str, kind: str, now: int | None = None) -> None:
        if not self.enabled or not user or not app:
            return
        now = int(time.time() * 1000) if now is None else now
        for days in (self.days, self.pending):
            row = days.setdefault(day_key(now), {}).setdefault(f"{user}\t{app}", [0, 0, 0, 0])
            if kind in KINDS:
                row[KINDS.index(kind)] += 1
            row[3] = max(row[3], now)
        self.dirty = True

    def query(self, now: int, days: int = 30, user: str = "", app: str = "") -> dict[str, Any]:  # noqa: PLR0915
        """Totals in the period per (user, app), per user and per app; exact filters."""
        days = min(max(days, 1), 400)
        keep = day_key(now - (days - 1) * DAY_MS)
        pairs: dict[tuple[str, str], list[Any]] = {}
        for d, rows in self.days.items():
            if d < keep:
                continue
            for k, (o, s, a, last) in rows.items():
                u, ap = k.split("\t", 1)
                if (user and u != user) or (app and ap != app):
                    continue
                t = pairs.setdefault((u, ap), [0, 0, 0, 0, set()])
                t[0], t[1], t[2], t[3] = t[0] + o, t[1] + s, t[2] + a, max(t[3], last)
                t[4].add(d)
        rows_out: list[dict[str, Any]] = [
            {"user": u, "app": ap, "opens": o, "sections": s, "actions": a, "days": len(ds), "last": last}
            for (u, ap), (o, s, a, last, ds) in pairs.items()
        ]
        rows_out.sort(key=lambda r: (-r["last"], r["user"], r["app"]))

        def group(field: str, other: str) -> list[dict[str, Any]]:
            g: dict[str, dict[str, Any]] = {}
            for r in rows_out:
                new = {field: r[field], other + "s": set(), "opens": 0, "actions": 0, "last": 0}
                x = g.setdefault(str(r[field]), new)
                x[other + "s"].add(r[other])
                x["opens"] += r["opens"]
                x["actions"] += r["actions"]
                x["last"] = max(x["last"], r["last"])
            out = [x | {other + "s": len(x[other + "s"])} for x in g.values()]
            return sorted(out, key=lambda x: (-x["last"], x[field]))

        return {
            "days": days,
            "rows": rows_out,
            "by_user": group("user", "app"),
            "by_app": group("app", "user"),
        }

    def csv(self, now: int, days: int = 30, user: str = "", app: str = "") -> str:
        def esc(s: str) -> str:
            s = re.sub(r"[\x00-\x1f\x7f]", "", s).strip().replace('"', '""')
            return f'"\'{s}"' if s[:1] in "=+-@|%" and s else f'"{s}"'

        out = ["usuario;app;aberturas;navegacoes;acoes;dias_ativos;ultimo_acesso_utc"]
        for r in self.query(now, days, user, app)["rows"]:
            last = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["last"] / 1000))
            out.append(
                f"{esc(r['user'])};{esc(r['app'])};{r['opens']};{r['sections']};{r['actions']};{r['days']};{last}"
            )
        return "\n".join(out) + "\n"

    def flush(self, retention_days: int, now: int | None = None) -> None:  # noqa: PLR0915
        now = int(time.time() * 1000) if now is None else now
        keep = day_key(now - retention_days * DAY_MS)
        for k in [k for k in self.days if k < keep]:
            del self.days[k]
            self.dirty = True
        if not self.path:
            return
        with file_lock(self.path):
            try:
                other = self.path.stat().st_mtime_ns != self._mtime  # another replica wrote after us
            except OSError:
                other = False
            if other:  # adopts the file and adds on top only what this replica counted since the last write
                self.days = {d: rows for d, rows in self._read().items() if d >= keep}
                for d, rows in self.pending.items():
                    for k, add in rows.items():
                        r = self.days.setdefault(d, {}).setdefault(k, [0, 0, 0, 0])
                        r[0], r[1], r[2], r[3] = (
                            r[0] + add[0],
                            r[1] + add[1],
                            r[2] + add[2],
                            max(r[3], add[3]),
                        )
                self.dirty = True
            if self.dirty:
                tmp = self.path.with_suffix(f".json.{os.getpid()}.tmp")
                tmp.write_text(json.dumps({"days": self.days}, separators=(",", ":")), encoding="utf-8")
                os.replace(tmp, self.path)
                self._mtime = self.path.stat().st_mtime_ns
            self.pending, self.dirty = {}, False
