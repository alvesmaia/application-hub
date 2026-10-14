"""Federated global search: platform (apps, pages, administration) + the apps' records.

The apps answer `GET {upstream}/{id}/_hub/search?q=&limit=` (internal token + the user's identity),
in parallel, each with a maximum time of `HUB_SEARCH_TIMEOUT_MS` (default 1500 ms). Apps first; the
administration's internal data (users, deployments, variables) goes in its own group, at the end."""

from __future__ import annotations

import asyncio
import json
import os
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import httpx

from hub.auth import admin_required
from hub.gateway.identity import Identity
from hub.gateway.registry import Registry
from hub.ux.texts import t, tr

PER_APP, PER_APP_ALL, PLATFORM_MAX, ADMIN_MAX = 5, 50, 8, 6
_BURST, _PER_SEC = 20.0, 4.0
ADMIN_KINDS = ("Usuário", "Implantação", "Variável de ambiente")
_unsupported: dict[str, float] = {}  # app without search (404): out of the round for 5 min
_buckets: dict[str, tuple[float, float]] = {}


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if not unicodedata.combining(c))


def score(text: str, q: str) -> float:
    s, n = fold(text), fold(q.strip())
    if not n:
        return 0.0
    if s == n:
        return 1.0
    if s.startswith(n):
        return 0.8
    words = "".join(c if c.isalnum() else " " for c in s).split()
    if any(w.startswith(n) for w in words):
        return 0.6
    return 0.4 if n in s else 0.0


def mark_split(text: str, q: str) -> tuple[str, str, str]:
    """(before, matching snippet, after) without distinguishing case or accents."""
    n = fold(q.strip())
    chars = [fold(c) for c in text]
    i = "".join(chars).find(n) if n else -1
    if i < 0:
        return text, "", ""
    pos, start, end = 0, 0, len(text)
    found = False
    for k, f in enumerate(chars):
        if not found and pos >= i:
            start, found = k, True
        pos += len(f)
        if pos >= i + len(n):
            end = k + 1
            break
    return text[:start], text[start:end], text[end:]


def search_allowed(user: str, now: float | None = None) -> bool:
    """Per-user token bucket: each search opens one call to each app."""
    now = time.monotonic() if now is None else now
    tokens, at = _buckets.get(user, (_BURST, now))
    tokens = min(_BURST, tokens + (now - at) * _PER_SEC)
    if tokens < 1:
        _buckets[user] = (tokens, now)
        return False
    _buckets[user] = (tokens - 1, now)
    return True


def timeout_s() -> float:
    try:
        ms = int(os.getenv("HUB_SEARCH_TIMEOUT_MS", "1500"))
    except ValueError:
        ms = 1500
    return min(max(ms, 100), 1500) / 1000


token_source: Callable[[str], str | None] | None = None  # managed vault variable (`AdminStore.tokens`)


def internal_token(app: str) -> str | None:
    """App's `/_hub/*` endpoints token: the managed variable `HUB_INTERNAL_TOKEN_<APP>` (vault); only without it,
    the hub's environment (`HUB_INTERNAL_TOKEN_<APP>`, used as the seed at startup and in dev mode)."""
    key = "HUB_INTERNAL_TOKEN_" + app.upper().replace("-", "_")
    tok = (
        (token_source(app) if token_source else None)
        or os.getenv(key)
        or os.getenv("HUB_INTERNAL_TOKEN")
        or ""
    ).strip()
    return tok if len(tok) >= 16 else None


def safe_app_link(app: str, link: str) -> str:
    """Only the app's own path (`/apps/<id>…`); anything else falls back to the app root."""
    base = f"/apps/{app}"
    if link == f"/{app}" or link.startswith((f"/{app}/", f"/{app}?", f"/{app}#")):
        link = "/apps" + link
    ok = (
        (link == base or link.startswith((f"{base}/", f"{base}?", f"{base}#")))
        and len(link) <= 300
        and "\\" not in link
        and not any(c.isspace() or not c.isprintable() for c in link)
    )
    cur = link.split("?")[0].split("#")[0]
    for _ in range(4):
        low = cur.lower()
        if (
            not ok
            or "//" in cur
            or "%2f" in low
            or "%5c" in low
            or any(s in (".", "..") for s in cur.split("/"))
        ):
            return base
        nxt = unquote(cur)
        if nxt == cur:
            return link
        cur = nxt
    return base


@dataclass
class Results:
    q: str
    groups: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    partial: bool = False
    unavailable: list[str] = field(default_factory=list)
    took_ms: int = 0

    def json(self) -> dict[str, Any]:
        return {
            "q": self.q,
            "groups": self.groups,
            "total": self.total,
            "partial": self.partial,
            "unavailable": self.unavailable,
            "took_ms": self.took_ms,
        }


@dataclass
class Ctx:
    """What the search needs to know about the requester and the hub."""

    identity: Identity
    perms: list[str]
    superuser: bool
    reg: Registry
    client: httpx.AsyncClient
    nav: dict[str, Any]
    lang: str
    data_dir: Path
    users: list[dict[str, Any]] = field(default_factory=list)

    def can_app(self, app: str) -> bool:
        return self.identity.allows_app(app)


def _hit(title: str, sub: str, url: str, kind: str, score_: float = 0.0) -> dict[str, Any]:
    return {"title": title, "subtitle": sub, "url": url, "kind": kind, "score": score_}


def platform_entities(c: Ctx) -> list[dict[str, Any]]:
    lang = c.lang
    out = [
        _hit(e.name, e.display.get("description", ""), f"/apps/{e.id}", t("shell.apps", lang))
        for e in c.reg.entries()
        if c.can_app(e.id)
    ]
    for u, key in (
        ("/", "shell.home"),
        ("/applications", "shell.apps"),
        ("/inbox", "shell.inbox"),
        ("/status", "shell.status"),
    ):
        out.append(_hit(t(key, lang), u, u, t("search.platform", lang)))
    items = [i for g in c.nav["groups"] for i in g["items"]]
    for i in items:
        seg = i["href"].removeprefix("/admin").strip("/")
        if any(p in c.perms for p in admin_required(seg)):
            out.append(_hit(tr(i["label"], lang), i["href"], i["href"], t("shell.admin", lang)))
    return out


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _platform_hits(c: Ctx, q: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_ = platform_entities(c)
    if "gerenciar_usuarios" in c.perms:
        all_ += [
            _hit(u["name"], u["email"], f"/admin/users?q={quote(u['email'])}", "Usuário")
            for u in c.users[:5000]
        ]
    if "editar_configuracoes" in c.perms:
        names = {e.id: e.name for e in c.reg.entries()}
        deployed = _read(c.data_dir / "deployments.json")
        for app, version in deployed.items() if isinstance(deployed, dict) else []:
            if c.can_app(app):
                all_.append(
                    _hit(
                        f"{names.get(app, app)} {version}", "Produção", "/admin/deploys", "Implantação", -0.5
                    )
                )
    if {"gerenciar_variaveis", "editar_configuracoes"} & set(c.perms):
        for v in _read(c.data_dir / "admin.json").get("env", []):
            internal = v["name"].startswith("HUB_")
            if not internal or c.superuser:
                sub = "Interna da plataforma" if internal else ""
                all_.append(
                    _hit(v["name"], sub, f"/admin/env?q={quote(v['name'])}", "Variável de ambiente", -0.5)
                )
    hits = []
    for x in all_:
        s = max(score(x["title"], q), score(x["subtitle"], q) * 0.8)
        if x["score"] < 0:
            s *= 0.5
        elif s > 0 and x["kind"] == t("shell.apps", c.lang):
            s = min(1.0, s + 0.15)  # only highlights what already matched
        if s > 0:
            hits.append(x | {"score": round(s, 4)})
    hits.sort(key=lambda h: -h["score"])
    pages = [h for h in hits if h["kind"] not in ADMIN_KINDS][:PLATFORM_MAX]
    return pages, [h for h in hits if h["kind"] in ADMIN_KINDS][:ADMIN_MAX]


async def _ask_app(
    c: Ctx, app: str, q: str, limit: int, headers: dict[str, str]
) -> list[dict[str, Any]] | None:
    """App records; `None` = app without search (404); raises on failure or timeout."""
    entry = c.reg.get(app)
    if entry is None:
        return None
    url = f"{entry.upstream.rstrip('/')}/{app}/_hub/search?q={quote(q)}&limit={limit}"
    async with asyncio.timeout(timeout_s()):
        r = await c.client.get(url, headers=headers)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    hits = []
    for h in json.loads(r.content[: 64 * 1024])["results"]:
        link = safe_app_link(app, str(h.get("url", "")))
        title = str(h.get("title", "")).strip()
        if title and (link != f"/apps/{app}" or h.get("url") in (f"/apps/{app}", f"/{app}")):
            sc = h.get("score", 0)
            sc = min(max(float(sc), 0.0), 1.0) if isinstance(sc, int | float) else 0.0
            hits.append(
                _hit(title[:120], str(h.get("subtitle", ""))[:200], link, str(h.get("kind", ""))[:40], sc)
            )
    return hits[:limit]


async def search(c: Ctx, q: str, only_app: str | None = None, per_app: int = PER_APP) -> Results:
    t0 = time.monotonic()
    q = q.strip()[:100]
    out = Results(q=q)
    if not q:
        return out
    platform, admin = ([], []) if only_app else _platform_hits(c, q)
    if platform:
        out.groups.append(
            {
                "id": "platform",
                "label": t("search.platform", c.lang),
                "hits": platform,
                "section": "platform",
                "more_url": "",
            }
        )
    ident = c.identity
    ident.locale = c.lang
    headers = {"x-hub-user": ident.header_value(), "x-hub-locale": c.lang}
    now = time.monotonic()
    todo = [
        e
        for e in c.reg.entries()
        if c.can_app(e.id)
        and (not only_app or e.id == only_app)
        and _unsupported.get(e.id, 0) <= now
        and internal_token(e.id)
    ]
    answers = await asyncio.gather(
        *(
            _ask_app(c, e.id, q, per_app, headers | {"x-hub-internal-token": internal_token(e.id) or ""})
            for e in todo
        ),
        return_exceptions=True,
    )
    groups = []
    for e, ans in zip(todo, answers, strict=True):
        if isinstance(ans, BaseException):
            out.partial = True
            out.unavailable.append(e.id)
        elif ans is None:
            _unsupported[e.id] = now + 300
        elif ans:
            more = f"/search?q={quote(q)}&app={e.id}" if not only_app and len(ans) >= per_app else ""
            groups.append({"id": e.id, "label": e.name, "hits": ans, "section": "app", "more_url": more})

    def best(g: dict[str, Any]) -> float:
        return float(max((h["score"] for h in g["hits"]), default=0.0))

    groups.sort(key=lambda g: (-best(g), g["label"]))
    # Platform first only when it has something as relevant as the apps' records.
    first = best(out.groups[0]) if out.groups else 0.0
    if groups and first + 0.05 < best(groups[0]):
        out.groups = groups + out.groups
    else:
        out.groups += groups
    if admin:
        out.groups.append(
            {
                "id": "admin",
                "label": t("search.admin", c.lang),
                "hits": admin,
                "section": "admin",
                "more_url": "",
            }
        )
    out.total = sum(len(g["hits"]) for g in out.groups)
    out.took_ms = int((time.monotonic() - t0) * 1000)
    return out
