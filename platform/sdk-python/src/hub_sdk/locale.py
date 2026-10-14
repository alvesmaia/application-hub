"""Language and regional formatting of the fragments (wave 9).

The hub resolves the user's language (saved preference → ``Accept-Language`` → pt-BR) and
passes it on in ``x-hub-locale`` and in the ``locale`` field of ``x-hub-user``. :func:`from_headers`
reads in that order and falls back to the browser's ``Accept-Language`` (the gateway also passes it on).

Translation: the **pt-BR text is the key** — ``t("Contratos")`` returns "Contracts" in
English if the catalog has the entry, otherwise the text itself (nothing breaks if it is missing).
``{name}`` placeholders are filled by ``t("{n} linhas", n=...)``. The request's language lives in a
``ContextVar`` set by :class:`LocaleMiddleware` (``HubSdk.install``
already installs it), so templates and code call ``t()`` without passing the language along.
"""

from __future__ import annotations

import contextvars
import datetime as dt
from collections.abc import Mapping
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from hub_sdk.identity import parse_identity

LOCALE_HEADER = "x-hub-locale"
SUPPORTED = ("pt-BR", "en")
DEFAULT = "pt-BR"


def parse(s: str | None) -> str | None:
    """Supported code for a code/prefix (``pt``, ``pt-PT``, ``en-US``…) or ``None``."""
    lang = (s or "").strip().lower().replace("_", "-").split("-")[0]
    return {"pt": "pt-BR", "en": "en", "es": "en"}.get(lang)  # `es` was dropped in 10/2026: it becomes en


def from_accept_language(v: str | None) -> str | None:
    """Best supported language from an ``Accept-Language`` (respects the ``q`` weights)."""
    best: tuple[float, int, str] | None = None
    for i, part in enumerate((v or "").split(",")[:20]):
        bits = part.split(";")
        q = 1.0
        for b in bits[1:]:
            b = b.strip()
            if b.startswith("q="):
                try:
                    q = float(b[2:])
                except ValueError:
                    q = 0.0
        code = parse(bits[0])
        if q <= 0 or code is None:
            continue
        if best is None or q > best[0] or (q == best[0] and i < best[1]):
            best = (q, i, code)
    return best[2] if best else None


def from_headers(headers: Mapping[str, str]) -> str:
    """``x-hub-locale`` → ``locale`` of ``x-hub-user`` → ``Accept-Language`` → pt-BR."""
    code = parse(headers.get(LOCALE_HEADER))
    if code is None:
        ident = parse_identity(headers.get("x-hub-user"))
        code = parse(ident.locale) if ident is not None else None
    return code or from_accept_language(headers.get("accept-language")) or DEFAULT


_current: contextvars.ContextVar[str] = contextvars.ContextVar("hub_locale", default=DEFAULT)
_catalog: dict[str, dict[str, str]] = {}


def current() -> str:
    """Language of the current request."""
    return _current.get()


def use(code: str) -> contextvars.Token[str]:
    """Sets the language (tests, jobs); returns the token for ``reset``."""
    return _current.set(parse(code) or DEFAULT)


def register(catalog: Mapping[str, Mapping[str, str]]) -> None:
    """Adds translations: ``{"texto pt-BR": {"en": "..."}}``."""
    for k, v in catalog.items():
        _catalog.setdefault(k, {}).update(v)


def t(text: str, **kw: Any) -> str:
    """Translation of ``text`` (pt-BR) in the current language, with ``{placeholders}`` filled."""
    code = current()
    out = text if code == DEFAULT else _catalog.get(text, {}).get(code, text)
    if kw:
        for k, v in kw.items():
            out = out.replace("{" + k + "}", str(v))
    return out


def number(v: float, places: int = 0, code: str | None = None) -> str:
    code = code or current()
    s = f"{abs(v):,.{places}f}"
    if code != "en":
        s = s.replace(",", "\0").replace(".", ",").replace("\0", ".")
    return f"-{s}" if v < 0 and any(c.isdigit() and c != "0" for c in s) else s


def thousands(n: int) -> str:
    return number(int(n), 0)


def money_cents(cents: int | None) -> str:
    """Cents → ``R$ 1.234,50`` (pt-BR) or ``BRL 1,234.50`` (en)."""
    if cents is None:
        return ""
    code = current()
    v = number(int(cents) / 100, 2, code)
    return f"BRL {v}" if code == "en" else f"R$ {v}"


def date(d: dt.date | None) -> str:
    """Short date: ``28/09/2026`` (pt-BR) or ``09/28/2026`` (en)."""
    if d is None:
        return ""
    return d.strftime("%m/%d/%Y") if current() == "en" else d.strftime("%d/%m/%Y")


class LocaleMiddleware:
    """ASGI middleware: sets the language of each request (ContextVar) from the
    hub headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        token = _current.set(from_headers(headers))
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(token)


# Strings of the SDK itself (workspace, 404, skeleton, 429).
register(
    {
        "Aplicação interna": {"en": "Internal application"},
        "Workspace": {"en": "Workspace"},
        "Navegação": {"en": "Navigation"},
        "Conectado como": {"en": "Signed in as"},
        "Precisa de ajuda?": {"en": "Need help?"},
        "Consulte a documentação ou fale com o time de suporte.": {
            "en": "Check the documentation or talk to the support team.",
        },
        "O suporte entrará em contato em breve.": {
            "en": "Support will contact you shortly.",
        },
        "Acessar central de ajuda →": {"en": "Open help center →"},
        "Voltar à plataforma": {"en": "Back to the platform"},
        "Menu da aplicação": {"en": "Application menu"},
        "Navegação da aplicação": {"en": "Application navigation"},
        "Página não encontrada": {"en": "Page not found"},
        "Esta seção não existe.": {"en": "This section does not exist."},
        "Voltar ao início do app": {"en": "Back to the app home"},
        "Carregando {name}": {"en": "Loading {name}"},
        "Muitas operações pesadas em andamento": {
            "en": "Too many heavy operations in progress",
        },
        "{what} não pôde começar agora porque o limite de operações simultâneas deste app foi atingido. Tente novamente em alguns segundos.": {
            "en": "{what} could not start now because this app's concurrent operation limit was reached. Try again in a few seconds.",
        },
    }
)
