"""HTML fragments with the shell's look (Jinja2) and common responses.

Contract conventions (``docs/CONTRATO-APP.md``):

- ``GET /{id}`` and ``GET /{id}/{section}`` return a **fragment** (no ``<html>``/
  ``<body>``) with the app workspace - sidebar + content - using the mockup classes
  (``app-sidebar``, ``workspace-content``, ``content-panel``, ``data-table``…);
- ``GET /{id}/_skeleton`` - skeleton body shown between sections;
- ``GET /{id}/assets/{demo.css,app.css,chart.js,interact.js}`` - app assets; the app's own fonts and
  images through ``asset_file`` (adapting an existing app keeps them);
- no inline ``<script>`` (hub CSP); ``POST`` forms with ``data-hub-target``;
  polling with ``data-hub-poll``; downloads at ``/_frag/{id}/…``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PackageLoader, select_autoescape
from markupsafe import Markup
from starlette.requests import HTTPConnection
from starlette.responses import HTMLResponse, Response

from hub_sdk import locale as _locale
from hub_sdk.assets import asset_file as asset_file  # re-exported: apps import helpers from here
from hub_sdk.identity import identity_of

FRAG = "/_frag"

# Icons (mockup SVG) used in the sidebar.
ICONS: dict[str, str] = {
    "dashboard": '<rect x="3" y="3" width="7" height="7" rx="1"></rect><rect x="14" y="3" width="7" height="7" rx="1">'
    '</rect><rect x="3" y="14" width="7" height="7" rx="1"></rect><rect x="14" y="14" width="7" height="7" rx="1">'
    "</rect>",
    "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>'
    '<path d="M14 2v6h6M8 13h8M8 17h8"></path>',
    "upload": '<path d="M12 16V4M7 9l5-5 5 5"></path><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"></path>',
    "chart": '<path d="M3 3v18h18"></path><path d="m7 14 4-4 4 3 6-7"></path>',
    "settings": '<circle cx="12" cy="12" r="3"></circle><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7'
    'l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"></path>',
    "back": '<path d="m15 18-6-6 6-6M9 12h11"></path>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"></path>',
    # common domains, so an adapted app keeps meaningful icons (a vehicle is not an "upload")
    "truck": '<path d="M3 16V8a1 1 0 0 1 1-1h10v9"></path><path d="M14 10h4l3 3v3h-7"></path>'
    '<circle cx="7.5" cy="17" r="1.8"></circle><circle cx="17.5" cy="17" r="1.8"></circle>',
    "wrench": '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z"></path>',
    "user": '<circle cx="12" cy="8" r="4"></circle><path d="M4 21a8 8 0 0 1 16 0"></path>',
    "users": '<circle cx="9" cy="8" r="3.5"></circle><path d="M2 20a7 7 0 0 1 14 0"></path>'
    '<path d="M16 4.5a3.5 3.5 0 0 1 0 7M22 20a7 7 0 0 0-4-6.3"></path>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"></rect><path d="M3 10h18M8 3v4M16 3v4"></path>',
    "list": '<path d="M9 6h12M9 12h12M9 18h12"></path><circle cx="4" cy="6" r="1"></circle>'
    '<circle cx="4" cy="12" r="1"></circle><circle cx="4" cy="18" r="1"></circle>',
    "box": '<path d="M21 8 12 3 3 8v8l9 5 9-5z"></path><path d="M3 8l9 5 9-5M12 13v8"></path>',
    "tag": '<path d="M20 12 12 20 4 12V4h8z"></path><circle cx="8" cy="8" r="1.5"></circle>',
    "money": '<rect x="2" y="6" width="20" height="12" rx="2"></rect><circle cx="12" cy="12" r="3"></circle>',
    "map": '<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2z"></path><path d="M9 4v14M15 6v14"></path>',
    "home": '<path d="M3 11 12 4l9 7"></path><path d="M5 10v10h14V10"></path>',
    "search": '<circle cx="11" cy="11" r="7"></circle><path d="m20 20-4-4"></path>',
    "collapse": '<path d="m11 17-5-5 5-5M18 17l-5-5 5-5"></path>',
    "bell": '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"></path><path d="M10 21h4"></path>',
}


def icon(name: str | Markup) -> Markup:
    """A named icon (`ICONS`) or the app's own SVG body (text starting with ``<``, or `Markup`), e.g. the
    icons of an app being adapted. The body is the app's own constant, never user input."""
    own = isinstance(name, Markup) or name.lstrip().startswith("<")
    body = str(name) if own else ICONS.get(name, ICONS["file"])
    return Markup(f'<svg viewBox="0 0 24 24" aria-hidden="true">{body}</svg>')  # noqa: S704 - SVG fixo


@dataclass(frozen=True)
class NavItem:
    section: str
    label: str
    icon: str | Markup = "file"


@dataclass(frozen=True)
class AppInfo:
    id: str
    name: str
    icon: str = "file"
    color: str = "#e3f2ea"
    ink: str = "#2f7d57"
    nav: Sequence[NavItem] = field(default_factory=tuple)
    #: Short line under the name in the sidebar (e.g. "Contracts and suppliers"); empty = name only.
    tagline: str = ""
    #: "Signed in as" and the help card at the bottom of the sidebar; False keeps an adapted app's sidebar as it was.
    help_card: bool = True

    def has_section(self, s: str) -> bool:
        return any(n.section == s for n in self.nav)


def html(body: str, status: int = 200, headers: dict[str, str] | None = None) -> HTMLResponse:
    h = {"cache-control": "no-store"}
    h.update(headers or {})
    return HTMLResponse(body, status_code=status, headers=h)


def public_base(conn: HTTPConnection, app_id: str) -> str:
    """App's public base for links/forms: the hub's ``x-hub-base`` (``/apps/{id}``, or ``/staging/apps/{id}``
    in staging), or ``/apps/{id}`` when the header is missing or has anything besides letters, digits, ``/``
    and ``-``."""
    b = conn.headers.get("x-hub-base", "").strip()
    ok = b.startswith(("/apps/", "/staging/apps/")) and all(c.isalnum() or c in "/-" for c in b)
    return b if ok else f"/apps/{app_id}"


class Renderer:
    """The app's Jinja2 environment: the app's templates (``templates_dir``) + the SDK's
    (``workspace.html``, ``skeleton.html``, ``notice.html``)."""

    def __init__(self, app: AppInfo, templates_dir: Path | str) -> None:
        self.app = app
        self.env = Environment(
            loader=ChoiceLoader(
                [FileSystemLoader(str(templates_dir)), PackageLoader("hub_sdk", "templates")]
            ),
            autoescape=select_autoescape(default=True, default_for_string=True),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # Wave 9: `t()` translates by the request's language (LocaleMiddleware ContextVar);
        # numbers, values and dates in the language's format.
        shared: dict[str, Any] = dict(
            icon=icon,
            app=app,
            FRAG=FRAG,
            # Wave 13: app's public base (`/apps/{id}`) for links/forms.
            base=f"/apps/{app.id}",
            thousands=_locale.thousands,
            money=_locale.money_cents,
            date=_locale.date,
            t=_locale.t,
            locale=_locale.current,
        )
        self.env.globals.update(shared)

    def render(self, template: str, **ctx: Any) -> str:
        return self.env.get_template(template).render(**ctx)

    def page(
        self,
        conn: HTTPConnection,
        template: str,
        active: str,
        status: int = 200,
        **ctx: Any,
    ) -> HTMLResponse:
        """App page (full workspace)."""
        user = identity_of(conn).display_name
        ctx.setdefault("base", public_base(conn, self.app.id))
        return html(self.render(template, active=active, user=user, **ctx), status)

    def fragment(self, template: str, status: int = 200, **ctx: Any) -> HTMLResponse:
        """Page fragment (panel that replaces ``data-hub-target``)."""
        return html(self.render(template, **ctx), status)

    def notice(self, kind: str, title: str, text: str) -> Markup:
        return Markup(self.render("notice.html", kind=kind, title=title, text=text))  # noqa: S704

    def skeleton(self) -> HTMLResponse:
        return html(self.render("skeleton.html"))

    def not_found(self) -> HTMLResponse:
        return html(self.render("not_found.html"), 404)


def busy(what: str, queued: str, retry_after: int = 10) -> Response:
    """429 for a heavy operation (app limit reached) with ``Retry-After``: the ``hub.js``
    shows ``queued`` ("Report queued…") and retries on its own."""
    from markupsafe import escape

    t = _locale.t
    msg = t(
        "{what} não pôde começar agora porque o limite de operações simultâneas deste app foi "
        "atingido. Tente novamente em alguns segundos.",
        what=t(what),
    )
    body = (
        f'<div class="hub-notice hub-notice-warn" role="alert" data-hub-queued="{escape(t(queued))}">'
        f"<strong>{escape(t('Muitas operações pesadas em andamento'))}</strong>"
        f"<p>{escape(msg)}</p></div>"
    )
    return html(body, 429, {"retry-after": str(retry_after)})


def download_headers(content_type: str, filename: str) -> dict[str, str]:
    """``Content-Disposition: attachment`` + ``nosniff`` + ``no-store``."""
    safe = "".join(c if c.isascii() and (c.isalnum() or c in ".-_") else "_" for c in filename)
    return {
        "content-type": content_type,
        "content-disposition": f'attachment; filename="{safe}"',
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
    }


def thousands(n: int) -> str:
    """12345678 → "12.345.678"."""
    s = f"{abs(int(n)):,}".replace(",", ".")
    return f"-{s}" if n < 0 else s


def money_cents(cents: int | None) -> str:
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    c = abs(int(cents))
    return f"{sign}R$ {thousands(c // 100)},{c % 100:02d}"


def _is_number(s: str) -> bool:
    try:
        float(s)
    except ValueError:
        return False
    return True


def csv_field(s: str) -> str:
    """CSV field (``;``, Excel pt-BR) with formula neutralization (OWASP): values that
    start with ``= + - @`` (even after TAB/CR) get a ``'``."""
    hides = s.lstrip("\t\r")[:1] in ("=", "+", "-", "@")
    if hides and not _is_number(s):
        s = "'" + s
    if any(c in s for c in ';"\n\r'):
        s = '"' + s.replace('"', '""') + '"'
    return s


def csv_line(fields: Iterable[str]) -> str:
    return ";".join(csv_field(f) for f in fields) + "\r\n"


BOM = "﻿"
