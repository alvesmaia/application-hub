"""Hub context in the fragments and HTML translation.

Fragments carry markers (``{{HUB_USER}}``, ``{{HUB_FIRST}}``, ``{{HUB_INITIALS}}``,
``{{HUB_TODAY}}``, ``{{HUB_GREETING}}``, ``{{HUB_APP_NAME}}``) that :func:`personalize` replaces in the
response. HTML leaving the app goes through :class:`I18nMiddleware`: for a locale other than
pt-BR, texts from the ``i18n.json`` catalog (rows ``[pt-BR, en]``, key = pt-BR text) are
replaced — only text between tags and the ``placeholder``/``title``/``aria-label``/
``data-hub-toast``/``alt`` attributes; data (names, numbers, asset tags) pass through intact.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from hub_sdk import locale
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ativos.ui import esc

APP_NAME = "Gestão de Ativos"
HERE = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def catalog() -> dict[str, str]:
    rows = json.loads((HERE / "i18n.json").read_text("utf-8"))
    return {r[0]: r[1] for r in rows}


def tr(loc: str, pt: str) -> str | None:
    """Exact translation of ``pt`` (or ``None`` if it is not in the catalog / is pt-BR)."""
    hit = catalog().get(pt)
    if hit is None or loc == "pt-BR":
        return None
    return hit


def unescape(s: str) -> str:
    return (
        s.replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
    )


def _translate_text(loc: str, raw: str, out: list[str]) -> None:
    trimmed = raw.strip()
    if not trimmed:
        out.append(raw)
        return
    t = tr(loc, unescape(trimmed))
    if t is not None:
        lead = raw[: len(raw) - len(raw.lstrip())]
        trail = raw[len(raw.rstrip()) :]
        out.append(lead + esc(t) + trail)
    elif loc == "en":
        out.append(en_numbers(raw))  # outside the catalog (data): only numbers and dates change
    else:
        out.append(raw)


def en_numbers(s: str) -> str:
    """Pt-BR → en-US numbers and dates within a text (``2.055`` → ``2,055``, ``3,6`` →
    ``3.6``, ``05/01/2017`` → ``01/05/2017``); codes, versions and IPs stay intact."""
    c = s
    out: list[str] = []
    i = 0
    n = len(c)
    while i < n:
        prev_ok = i == 0 or not (c[i - 1].isalnum() or c[i - 1] in "-.,/_")
        if "0" <= c[i] <= "9" and prev_ok:
            j = i
            while j < n and ("0" <= c[j] <= "9" or c[j] in ".,/"):
                j += 1
            end = j
            while end > i and c[end - 1] in ".,/":
                end -= 1
            next_ok = end == n or end < j or not (c[end].isalnum() or c[end] in "-_")
            tok = c[i:end]
            out.append(_en_token(tok) if next_ok else tok)
            i = end
            continue
        out.append(c[i])
        i += 1
    return "".join(out)


def _digits(x: str) -> bool:
    return bool(x) and all("0" <= ch <= "9" for ch in x)


def _en_token(t: str) -> str:
    parts = t.split("/")
    if (
        len(parts) == 3
        and (len(parts[0]), len(parts[1]), len(parts[2])) == (2, 2, 4)
        and all(_digits(p) for p in parts)
    ):
        return f"{parts[1]}/{parts[0]}/{parts[2]}"
    if "/" in t:
        return t
    dec: str | None = None
    if "," in t:
        int_part, _, d = t.rpartition(",")
        if not _digits(d):
            return t
        dec = d
    else:
        int_part = t
    groups = int_part.split(".")
    thousands_ok = (
        len(groups) > 1
        and 1 <= len(groups[0]) <= 3
        and all(_digits(g) for g in groups)
        and all(len(g) == 3 for g in groups[1:])
    )
    if len(groups) > 1 and not thousands_ok:
        return t
    if len(groups) == 1 and (not _digits(int_part) or dec is None):
        return t
    int_en = ",".join(groups)
    return f"{int_en}.{dec}" if dec is not None else int_en


_ATTRS = ('placeholder="', 'title="', 'aria-label="', 'data-hub-toast="', 'alt="')


def _translate_tag(loc: str, tag: str, out: list[str]) -> None:
    rest = tag
    while True:
        best: tuple[int, int] | None = None
        for a in _ATTRS:
            i = rest.find(a)
            if i >= 0 and (best is None or i < best[0]):
                best = (i, len(a))
        if best is None:
            out.append(rest)
            return
        vstart = best[0] + best[1]
        vlen = rest.find('"', vstart)
        if vlen < 0:
            out.append(rest)
            return
        out.append(rest[:vstart])
        v = rest[vstart:vlen]
        t = tr(loc, unescape(v))
        out.append(esc(t) if t is not None else v)
        rest = rest[vlen:]


def tr_html(loc: str, html: str) -> str:
    """Translates the HTML (text between tags and some attributes); ``<script>``/``<style>``/
    ``<textarea>`` intactos."""
    if loc == "pt-BR":
        return html
    out: list[str] = []
    rest = html
    while rest:
        i = rest.find("<")
        if i < 0:
            _translate_text(loc, rest, out)
            break
        _translate_text(loc, rest[:i], out)
        rest = rest[i:]
        j = rest.find(">")
        end = j + 1 if j >= 0 else len(rest)
        tag = rest[:end]
        _translate_tag(loc, tag, out)
        rest = rest[end:]
        low = tag.lower()
        for raw in ("script", "style", "textarea"):
            if low.startswith(f"<{raw}"):
                k = rest.lower().find(f"</{raw}")
                k = len(rest) if k < 0 else k
                out.append(rest[:k])
                rest = rest[k:]
    return "".join(out)


class I18nMiddleware:
    """ASGI: ``text/html`` responses in the user's language (``x-hub-locale``)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        loc = locale.from_headers(headers)
        if loc == "pt-BR":
            await self.app(scope, receive, send)
            return
        start: Message | None = None
        body = bytearray()
        html = False

        async def wrapper(msg: Message) -> None:
            nonlocal start, html
            if msg["type"] == "http.response.start":
                ct = next((v for k, v in msg["headers"] if k.lower() == b"content-type"), b"")
                html = ct.startswith(b"text/html")
                if html:
                    start = msg
                    return
                await send(msg)
            elif html and msg["type"] == "http.response.body":
                body.extend(msg.get("body", b""))
                if msg.get("more_body"):
                    return
                assert start is not None
                text = tr_html(loc, body.decode("utf-8", "replace")).encode()
                hs = [(k, v) for k, v in start["headers"] if k.lower() != b"content-length"]
                hs.append((b"content-length", str(len(text)).encode()))
                await send({**start, "headers": hs})
                await send({"type": "http.response.body", "body": text})
            else:
                await send(msg)

        await self.app(scope, receive, wrapper)


# ---- hub context (user, today, greeting) --------------------------------------


def who(name: str) -> tuple[str, str, str]:
    """(name, first name, initials) of the display name."""
    words = name.split()
    first = words[0] if words else ""
    if not words:
        initials = "?"
    elif len(words) == 1:
        initials = words[0][:2].upper()
    else:
        initials = (words[0][:1] + words[-1][:1]).upper()
    return name, first, initials


def _now_local() -> dt.datetime:
    tz = os.environ.get("HUB_TZ", "America/Sao_Paulo")
    try:
        from zoneinfo import ZoneInfo

        zone: dt.tzinfo = ZoneInfo(tz)
    except Exception:
        zone = dt.timezone(dt.timedelta(hours=-3)) if tz == "America/Sao_Paulo" else dt.UTC
    return dt.datetime.now(zone)


_DAYS = {
    "pt-BR": [
        "segunda-feira",
        "terça-feira",
        "quarta-feira",
        "quinta-feira",
        "sexta-feira",
        "sábado",
        "domingo",
    ],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}
_MONTHS = {
    "pt-BR": ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
}


def today_label(loc: str) -> str:
    """ "QUARTA-FEIRA, 1 OUT 2026" / "WEDNESDAY, OCT 1 2026"."""
    d = _now_local()
    wd, m = _DAYS[loc][d.weekday()], _MONTHS[loc][d.month - 1]
    s = f"{wd}, {m} {d.day} {d.year}" if loc == "en" else f"{wd}, {d.day} {m} {d.year}"
    return s.upper()


def greeting(loc: str) -> str:
    h = _now_local().hour
    i = 0 if h < 12 else (1 if h < 18 else 2)
    return {
        "pt-BR": ("Bom dia", "Boa tarde", "Boa noite"),
        "en": ("Good morning", "Good afternoon", "Good evening"),
    }[loc][i]


def personalize(body: str, name: str, loc: str) -> str:
    """Replaces the hub context markers (all values escaped)."""
    if "{{HUB_" not in body:
        return body
    n, first, initials = who(name)
    return (
        body.replace("{{HUB_USER}}", esc(n))
        .replace("{{HUB_FIRST}}", esc(first or n))
        .replace("{{HUB_INITIALS}}", esc(initials))
        .replace("{{HUB_TODAY}}", esc(today_label(loc)))
        .replace("{{HUB_GREETING}}", greeting(loc))
        .replace("{{HUB_APP_NAME}}", esc(APP_NAME))
    )


_ = (re, Any)
