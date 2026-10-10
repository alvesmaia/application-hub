"""Idioma e formatação regional dos fragmentos (onda 9) — espelho de ``hub_sdk::locale``.

O hub resolve o idioma do usuário (preferência salva → ``Accept-Language`` → pt-BR) e o
repassa em ``x-hub-locale`` e no campo ``locale`` do ``x-hub-user``. :func:`from_headers`
lê nessa ordem e cai no ``Accept-Language`` do navegador (o gateway também o repassa).

Tradução: o **texto em pt-BR é a chave** — ``t("Contratos")`` devolve "Contracts" em
inglês se o catálogo tiver a entrada, senão o próprio texto (nada quebra se faltar).
Marcadores ``{nome}`` são preenchidos por ``t("{n} linhas", n=...)``. O idioma da
requisição fica numa ``ContextVar`` posta por :class:`LocaleMiddleware` (``HubSdk.install``
já a instala), então templates e código chamam ``t()`` sem passar o idioma adiante.
"""

from __future__ import annotations

import contextvars
import datetime as dt
from collections.abc import Mapping
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from hub_sdk.identity import parse_identity

LOCALE_HEADER = "x-hub-locale"
SUPPORTED = ("pt-BR", "en", "es")
DEFAULT = "pt-BR"


def parse(s: str | None) -> str | None:
    """Código suportado para um código/prefixo (``pt``, ``pt-PT``, ``en-US``…) ou ``None``."""
    lang = (s or "").strip().lower().replace("_", "-").split("-")[0]
    return {"pt": "pt-BR", "en": "en", "es": "es"}.get(lang)


def from_accept_language(v: str | None) -> str | None:
    """Melhor idioma suportado de um ``Accept-Language`` (respeita os pesos ``q``)."""
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
    """``x-hub-locale`` → ``locale`` do ``x-hub-user`` → ``Accept-Language`` → pt-BR."""
    code = parse(headers.get(LOCALE_HEADER))
    if code is None:
        ident = parse_identity(headers.get("x-hub-user"))
        code = parse(ident.locale) if ident is not None else None
    return code or from_accept_language(headers.get("accept-language")) or DEFAULT


_current: contextvars.ContextVar[str] = contextvars.ContextVar("hub_locale", default=DEFAULT)
_catalog: dict[str, dict[str, str]] = {}


def current() -> str:
    """Idioma da requisição em curso."""
    return _current.get()


def use(code: str) -> contextvars.Token[str]:
    """Fixa o idioma (testes, tarefas); devolve o token para ``reset``."""
    return _current.set(parse(code) or DEFAULT)


def register(catalog: Mapping[str, Mapping[str, str]]) -> None:
    """Acrescenta traduções: ``{"texto pt-BR": {"en": "...", "es": "..."}}``."""
    for k, v in catalog.items():
        _catalog.setdefault(k, {}).update(v)


def t(text: str, **kw: Any) -> str:
    """Tradução de ``text`` (pt-BR) no idioma atual, com ``{marcadores}`` preenchidos."""
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
    """Centavos → ``R$ 1.234,50`` (pt-BR/es) ou ``BRL 1,234.50`` (en)."""
    if cents is None:
        return ""
    code = current()
    v = number(int(cents) / 100, 2, code)
    return f"BRL {v}" if code == "en" else f"R$ {v}"


def date(d: dt.date | None) -> str:
    """Data curta: ``28/09/2026`` (pt-BR, es) ou ``09/28/2026`` (en)."""
    if d is None:
        return ""
    return d.strftime("%m/%d/%Y") if current() == "en" else d.strftime("%d/%m/%Y")


class LocaleMiddleware:
    """Middleware ASGI: fixa o idioma de cada requisição (ContextVar) a partir dos
    cabeçalhos do hub."""

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


# Textos do próprio SDK (workspace, 404, skeleton, 429).
register(
    {
        "Aplicação interna": {"en": "Internal application", "es": "Aplicación interna"},
        "Workspace": {"en": "Workspace", "es": "Espacio de trabajo"},
        "Navegação": {"en": "Navigation", "es": "Navegación"},
        "Conectado como": {"en": "Signed in as", "es": "Conectado como"},
        "Precisa de ajuda?": {"en": "Need help?", "es": "¿Necesita ayuda?"},
        "Consulte a documentação ou fale com o time de suporte.": {
            "en": "Check the documentation or talk to the support team.",
            "es": "Consulte la documentación o hable con el equipo de soporte.",
        },
        "O suporte entrará em contato em breve.": {
            "en": "Support will contact you shortly.",
            "es": "El soporte se pondrá en contacto pronto.",
        },
        "Acessar central de ajuda →": {"en": "Open help center →", "es": "Abrir centro de ayuda →"},
        "Voltar à plataforma": {"en": "Back to the platform", "es": "Volver a la plataforma"},
        "Menu da aplicação": {"en": "Application menu", "es": "Menú de la aplicación"},
        "Navegação da aplicação": {"en": "Application navigation", "es": "Navegación de la aplicación"},
        "Página não encontrada": {"en": "Page not found", "es": "Página no encontrada"},
        "Esta seção não existe.": {"en": "This section does not exist.", "es": "Esta sección no existe."},
        "Voltar ao início do app": {"en": "Back to the app home", "es": "Volver al inicio de la aplicación"},
        "Carregando {name}": {"en": "Loading {name}", "es": "Cargando {name}"},
        "Muitas operações pesadas em andamento": {
            "en": "Too many heavy operations in progress",
            "es": "Demasiadas operaciones pesadas en curso",
        },
        "{what} não pôde começar agora porque o limite de operações simultâneas deste app foi atingido. Tente novamente em alguns segundos.": {
            "en": "{what} could not start now because this app's concurrent operation limit was reached. Try again in a few seconds.",
            "es": "{what} no pudo comenzar ahora porque se alcanzó el límite de operaciones simultáneas de esta aplicación. Inténtelo de nuevo en unos segundos.",
        },
    }
)
