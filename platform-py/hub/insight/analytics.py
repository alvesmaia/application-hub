"""Usage analytics: only aggregates per day and opaque ids (HMAC with a key in the vault).

Nothing of email, name or IP on disk. The id key rotates every retention period (and on demand).
`HUB_ANALYTICS=0` turns it off; the per-user opt-out comes from the preferences.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hub.filelock import file_lock
from hub.insight.merge import merge3, snapshot
from hub.vault import Vault

MAX_KEYS, OTHERS, DAY_MS = 300, "(outros)", 86_400_000
VAULT_KEY = "analytics.hmac_key"
FEATURES = (
    "palette_open",
    "palette_select",
    "tour_start",
    "tour_done",
    "tour_skip",
    "home_customize",
    "theme_toggle",
    "inbox_open",
    "favorite",
)
_MAPS = ("app_opens", "sections", "pages", "zero_terms", "features", "events")


def day_key(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def norm_term(q: str) -> str | None:
    q = q.strip().lower()
    if not q or "@" in q:
        return None
    s = "".join("#" if c.isdigit() else c for c in " ".join(q.split()) if c.isprintable())[:40]
    return s or None


def _bump(m: dict[str, int], k: str, n: int = 1) -> None:
    if k in m or len(m) < MAX_KEYS:
        m[k] = m.get(k, 0) + n
    else:
        m[OTHERS] = m.get(OTHERS, 0) + n


def _new_day() -> dict[str, Any]:
    day: dict[str, Any] = {m: {} for m in _MAPS}
    day |= {"users": set(), "app_users": {}, "searches": 0, "searches_zero": 0}
    day |= {"f_login": set(), "f_open": set(), "f_action": set()}
    return day


def _top(items: Counter[str] | dict[str, int], n: int) -> list[list[Any]]:
    return [[k, v] for k, v in sorted(items.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


class Analytics:
    def __init__(self, directory: Path | None, vault: Vault | None, enabled: bool = True) -> None:
        self.path = directory / "analytics.json" if directory else None
        self.key_path = directory / "analytics.key" if directory and not vault else None
        self.vault, self.enabled, self.dirty = vault, enabled, True
        self.ux: Any = None  # UxStore: per-user opt-out
        self.days: dict[str, dict[str, Any]] = {}
        self.raw_terms, self.key_since = False, int(time.time() * 1000)
        self.retention = min(max(int(os.environ.get("HUB_ANALYTICS_RETENTION_DAYS", "90") or 90), 7), 400)
        if self.path and self.path.is_file():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                self.raw_terms = bool(d.get("raw_terms"))
                self.key_since = int(d.get("key_since", self.key_since))
                self.retention = int(d.get("retention_days", self.retention))
                self.days = {k: self._load_day(v) for k, v in d.get("days", {}).items()}
            except (OSError, ValueError, TypeError):
                self.days = {}
        self.base = snapshot(self.days)  # state adopted at the last merge with the file (replicas)
        self.key = self._load_key()

    @staticmethod
    def _load_day(raw: dict[str, Any]) -> dict[str, Any]:
        day = _new_day()
        for k, v in raw.items():
            if k in ("users", "f_login", "f_open", "f_action"):
                day[k] = set(v)
            elif k == "app_users":
                day[k] = {a: set(u) for a, u in v.items()}
            else:
                day[k] = v
        return day

    def _load_key(self) -> str:
        if self.vault:
            if (k := self.vault.protected(VAULT_KEY)) and len(k) >= 32:
                return k
            k = secrets.token_hex(32)
            self.vault.set_protected(VAULT_KEY, k)
            self.key_since = int(time.time() * 1000)
            return k
        if self.key_path and self.key_path.is_file() and len(k := self.key_path.read_text().strip()) >= 32:
            return k
        return self._write_key(secrets.token_hex(32))

    def _write_key(self, k: str) -> str:
        if self.key_path:
            self.key_path.write_text(k, encoding="utf-8")
            self.key_path.chmod(0o600)
        return k

    def rotate_key(self, now: int) -> None:
        k = secrets.token_hex(32)
        if self.vault:
            self.vault.set_protected(VAULT_KEY, k)
        else:
            self._write_key(k)
        self.key, self.key_since, self.dirty = k, now, True

    def _h(self, msg: str) -> bytes:
        return hmac.new(self.key.encode(), msg.encode(), hashlib.sha256).digest()

    def _term_label(self, term: str) -> str:
        return f"termo #{self._h('term|' + term)[:5].hex()}"

    def set_raw_terms(self, on: bool) -> None:
        self.raw_terms, self.dirty = on, True
        if not on:  # deletes the stored texts: only the counts remain
            for day in self.days.values():
                old, day["zero_terms"] = day["zero_terms"], {}
                for t, n in old.items():
                    _bump(
                        day["zero_terms"],
                        t if t.startswith("termo #") or t == OTHERS else self._term_label(t),
                        n,
                    )

    def set_retention(self, days: int) -> int:
        self.retention, self.dirty = min(max(days, 7), 400), True
        return self.retention

    # ---- recording events -----------------------------------------------------------------------------
    def record(  # noqa: C901
        self, user: str, kind: str, a: str = "", b: str = "", n: int = 0, now: int | None = None
    ) -> None:
        """`kind`: login, page(a), open(a), section(a,b), action(a), search(a=texto, n=resultados), feature(a)."""
        if (
            not self.enabled
            or not user
            or (self.ux and getattr(self.ux.data.users.get(user), "analytics_opt_out", False))
        ):
            return
        now = int(time.time() * 1000) if now is None else now
        uid = int.from_bytes(self._h("user|" + user)[:8], "big")
        day = self.days.setdefault(day_key(now), _new_day())
        day["users"].add(uid)
        self.dirty = True
        if kind == "login":
            day["f_login"].add(uid)
            _bump(day["events"], "login")
        elif kind == "page":
            _bump(day["pages"], a)
        elif kind in ("open", "section"):
            _bump(day["app_opens" if kind == "open" else "sections"], a if kind == "open" else f"{a}/{b}")
            day["f_open"].add(uid)
            if a in day["app_users"] or len(day["app_users"]) < MAX_KEYS:
                day["app_users"].setdefault(a, set()).add(uid)
        elif kind == "action":
            day["f_action"].add(uid)
            _bump(day["events"], f"acao:{a}")
        elif kind == "search":
            day["searches"] += 1
            if n == 0:
                day["searches_zero"] += 1
                if t := norm_term(a):
                    _bump(day["zero_terms"], t if self.raw_terms else self._term_label(t))
        elif kind == "feature" and a in FEATURES:
            _bump(day["features"], a)

    def record_global(self, name: str, app: str, now: int | None = None) -> None:
        if self.enabled:
            now = int(time.time() * 1000) if now is None else now
            _bump(self.days.setdefault(day_key(now), _new_day())["events"], f"{name}:{app}")
            self.dirty = True

    # ---- leitura ---------------------------------------------------------------------------------------
    def window(self, now: int, n: int) -> list[tuple[str, dict[str, Any]]]:
        return [
            (k := day_key(now - i * DAY_MS), self.days.get(k) or _new_day()) for i in range(n - 1, -1, -1)
        ]

    def summary(self, now: int) -> dict[str, Any]:
        w = self.window(now, 30)
        days = [d for _, d in w]
        union = lambda ds, f: set().union(*(d[f] for d in ds))  # noqa: E731
        apps = sorted({a for d in days for a in d["app_users"]})

        def users_of(a: str, k: int) -> int:
            return len(set().union(*(d["app_users"].get(a, set()) for d in days[-k:])))

        total: dict[str, Counter[str]] = {m: Counter() for m in _MAPS}
        for d in days:
            for m in _MAPS:
                total[m].update(d[m])
        login = union(days, "f_login")
        opened = union(days, "f_open") & login
        acted = union(days, "f_action") & opened
        return {
            "dau": len(days[-1]["users"]),
            "wau": len(union(days[-7:], "users")),
            "mau": len(union(days, "users")),
            "apps": [
                [
                    a,
                    users_of(a, 1),
                    users_of(a, 7),
                    users_of(a, 30),
                    sum(d["app_opens"].get(a, 0) for d in days),
                ]
                for a in apps
            ],
            "top_sections": _top(total["sections"], 10),
            "top_pages": _top(total["pages"], 10),
            "zero_terms": _top(total["zero_terms"], 15),
            "searches": sum(d["searches"] for d in days),
            "searches_zero": sum(d["searches_zero"] for d in days),
            "funnel": [len(login), len(opened), len(acted)],
            "features": _top(total["features"], 20),
            "daily": [[k, len(d["users"])] for k, d in w],
        }

    def csv(self, now: int) -> str:
        def esc(s: str) -> str:
            s = re.sub(r"[\x00-\x1f\x7f]", "", s).strip().replace('"', '""')
            return f'"\'{s}"' if s[:1] in "=+-@|%" and s else f'"{s}"'

        out = [
            "dia;usuarios_ativos;aberturas_de_app;navegacoes;paginas_do_hub;buscas;buscas_sem_resultado;logins"
        ]
        for k, d in self.window(now, 30):
            out.append(
                f"{k};{len(d['users'])};{sum(d['app_opens'].values())};{sum(d['sections'].values())};"
                f"{sum(d['pages'].values())};{d['searches']};{d['searches_zero']};{d['events'].get('login', 0)}"
            )
        s = self.summary(now)
        out += [
            "",
            "app;dau;wau;mau;aberturas_30d",
            *(f"{esc(a)};{x};{y};{z};{o}" for a, x, y, z, o in s["apps"]),
        ]
        out += ["", "secao;navegacoes_30d", *(f"{esc(k)};{v}" for k, v in s["top_sections"])]
        out += ["", "busca_sem_resultado;vezes_30d", *(f"{esc(k)};{v}" for k, v in s["zero_terms"])]
        return "\n".join(out) + "\n"

    def bytes_estimate(self) -> int:
        return len(self._dump())

    def _dump(self) -> bytes:
        days = {
            k: {
                f: (
                    sorted(v)
                    if isinstance(v, set)
                    else {a: sorted(u) for a, u in v.items()}
                    if f == "app_users"
                    else v
                )
                for f, v in d.items()
            }
            for k, d in self.days.items()
        }
        doc = {
            "key_since": self.key_since,
            "raw_terms": self.raw_terms,
            "retention_days": self.retention,
            "days": days,
        }
        return json.dumps(doc, separators=(",", ":")).encode()

    def flush(self, now: int | None = None) -> None:
        """Applies retention, rotates the expired key and writes (if it changed)."""
        now = int(time.time() * 1000) if now is None else now
        if now - self.key_since >= self.retention * DAY_MS:
            self.rotate_key(now)
        keep = day_key(now - self.retention * DAY_MS)
        for k in [k for k in self.days if k < keep]:
            del self.days[k]
            self.dirty = True
        if not self.path:
            return
        with file_lock(self.path):  # replicas on the same volume: merges what the others wrote
            file: dict[str, Any] = {}
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8")).get("days", {})
                file = {k: self._load_day(v) for k, v in raw.items()}
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            merged = {k: v for k, v in merge3(file, self.days, self.base).items() if k >= keep}
            changed = self.dirty or merged != file
            self.days, self.base, self.dirty = merged, snapshot(merged), False
            if changed:
                tmp = self.path.with_suffix(f".json.{os.getpid()}.tmp")
                tmp.write_bytes(self._dump())
                os.replace(tmp, self.path)
