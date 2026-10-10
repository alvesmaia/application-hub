"""Textos do shell (catálogo do hub Rust: `crates/hub-shell/src/i18n.rs`, extraído em `texts.json`)
e tradução por catálogo (`catalog.json`, chave = texto em pt-BR) como o `hub_shell::tr`."""

from __future__ import annotations

import json
import re
from functools import cache
from html import escape, unescape
from pathlib import Path
from typing import Any, cast

from fastapi import Request, Response
from fastapi.templating import Jinja2Templates

_LANGS = ("pt-BR", "en", "es")
LANG_NAMES = {"pt-BR": "Português (Brasil)", "en": "English", "es": "Español"}
_WEB = Path(__file__).parent.parent / "web"
_MARK = r"\{[^{}\s\"]{0,24}\}"  # marcador `{…}` do catálogo


@cache
def _table() -> dict[str, list[str]]:
    return cast(dict[str, list[str]], json.loads((_WEB / "texts.json").read_text(encoding="utf-8")))


def parse_lang(tag: str) -> str | None:
    head = re.split(r"[-_]", tag.strip().lower())[0]
    return {"pt": "pt-BR", "en": "en", "es": "es"}.get(head)


def resolve(pref: str, accept: str | None) -> str:
    """Preferência salva (vazia = automático) → `Accept-Language` (pesos `q`) → pt-BR."""
    if lang := parse_lang(pref):
        return lang
    best: tuple[float, int, str] | None = None
    for i, part in enumerate((accept or "").split(",")[:20]):
        tag, *params = part.split(";")
        q = next((float(p.strip()[2:]) for p in params if re.fullmatch(r"q=\d+(\.\d+)?", p.strip())), 1.0)
        lang = parse_lang(tag)
        if lang and q > 0 and (best is None or q > best[0]):
            best = (q, i, lang)
    return best[2] if best else "pt-BR"


def t(key: str, lang: str = "pt-BR") -> str:
    row = _table().get(key)
    return row[_LANGS.index(lang) if lang in _LANGS else 0] if row else key


def tn(key: str, n: object, lang: str = "pt-BR") -> str:
    if str(n) == "1" and f"{key}_one" in _table():
        key += "_one"
    return t(key, lang).replace("{n}", str(n))


def ago(at_ms: int, now_ms: int, lang: str = "pt-BR") -> str:
    s = max(now_ms - at_ms, 0) // 1000
    if s < 60:
        return t("time.now", lang)
    if s < 3600:
        return tn("time.min", s // 60, lang)
    if s < 86_400:
        return tn("time.hour", s // 3600, lang)
    return tn("time.day", s // 86_400, lang)


@cache
def _catalog() -> tuple[dict[str, list[str]], list[tuple[re.Pattern[str], list[str]]]]:
    rows = cast(list[list[str]], json.loads((_WEB / "catalog.json").read_text(encoding="utf-8")))
    exact = {r[0]: r for r in rows}
    molds = []
    for r in rows:
        parts = re.split(f"({_MARK})", r[0])
        if len(parts) > 1 and sum(c.isalpha() for c in "".join(parts[::2])) >= 3:
            rx = "".join(re.escape(p) if i % 2 == 0 else "(.+?)" for i, p in enumerate(parts))
            molds.append((re.compile(f"^{rx}$", re.S), r))
    molds.sort(key=lambda m: -len(m[1][0]))
    return exact, molds


def tr(text: str, lang: str) -> str:
    """Traduz um texto em pt-BR pelo catálogo (exato, molde `{…}` ou partes ` · `); o que não consta passa."""
    if lang not in _LANGS[1:] or not text.strip():
        return text
    col, (exact, molds) = _LANGS.index(lang), _catalog()
    s = " ".join(text.split())
    if row := exact.get(s):
        return row[col]
    for rx, row in molds:
        if m := rx.match(s):
            vals = iter([tr(v, lang) for v in m.groups()])
            return re.sub(_MARK, lambda _, it=vals: next(it, ""), row[col])
    if " · " in s:
        return " · ".join(tr(p, lang) for p in s.split(" · "))
    return text


_ATTRS = r"(?:placeholder|aria-label|title|alt|data-hub-colfilter-empty|data-hub-col-label|data-label|data-confirm|data-obs-chart)"
_SKIP = re.compile(r"<(script|style|code|textarea)\b.*?</\1>|<!--notr-->.*?<!--/notr-->", re.S | re.I)
_NODE = re.compile(r">([^<>]*[^\s<>][^<>]*)<")
_ATTR = re.compile(rf'(\s{_ATTRS}=")([^"]*)(")')


@cache
def _pt_keys() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in _table().values():
        out.setdefault(row[0], row)
    return out


def tr_html(html: str, lang: str) -> str:
    """Traduz os textos e atributos visíveis de uma página já renderizada (templates em pt-BR)."""
    if lang not in _LANGS[1:]:
        return html

    def one(s: str) -> str:
        raw = unescape(s)
        out = tr(raw, lang)
        if out == raw and raw not in _catalog()[0] and (row := _pt_keys().get(" ".join(raw.split()))):
            out = row[_LANGS.index(lang)]
        return s if out == raw else escape(out, quote=False)

    def node(m: re.Match[str]) -> str:
        s = m.group(1)
        lead, core = s[: len(s) - len(s.lstrip())], s.strip()
        return f">{lead}{one(core)}{s[len(s.rstrip()) :]}<"

    def part(chunk: str) -> str:
        chunk = _ATTR.sub(lambda m: m[1] + one(m[2]).replace('"', "&quot;") + m[3], chunk)
        return _NODE.sub(node, f">{chunk}<")[1:-1]

    keep = [(m.start(), m.end()) for m in _SKIP.finditer(html)]
    out: list[str] = []
    pos = 0
    for a, b in [*keep, (len(html), len(html))]:
        out += [part(html[pos:a]), html[a:b]]
        pos = b
    return "".join(out)


def page_response(templates: Jinja2Templates, request: Request, name: str, data: dict[str, Any]) -> Response:
    """`TemplateResponse` com o corpo traduzido para o idioma do usuário."""
    resp = templates.TemplateResponse(request, name, data)  # corpo já renderizado (bytes)
    lang = data.get("lang", "pt-BR")
    if lang != "pt-BR":
        resp.body = tr_html(bytes(resp.body).decode(), lang).encode()
        resp.headers["content-length"] = str(len(resp.body))
    return resp


_JSON_FIELDS = ("error", "title", "detail", "message", "reason", "hint")


def js_catalog(lang: str) -> dict[str, Any]:
    """Catálogo para o `hub-i18n.js`: `{"lang", "exact": {pt: t}, "templates": [[pt, t]]}`."""
    col = _LANGS.index(lang)
    rows = cast(list[list[str]], json.loads((_WEB / "catalog.json").read_text(encoding="utf-8")))
    exact = {r[0]: r[col] for r in rows if not re.search(_MARK, r[0])}
    return {
        "lang": lang,
        "exact": exact,
        "templates": [[r[0], r[col]] for r in rows if re.search(_MARK, r[0])],
    }


def tr_json(v: Any, lang: str) -> bool:
    """Traduz no lugar os campos de mensagem de primeiro nível (e `errors`) de um objeto JSON de erro."""
    if lang not in _LANGS[1:] or not isinstance(v, dict):
        return False
    changed = False
    for f in _JSON_FIELDS:
        if isinstance(v.get(f), str) and (out := tr(v[f], lang)) != v[f]:
            v[f], changed = out, True
    e = v.get("errors")
    for k, x in e.items() if isinstance(e, dict) else enumerate(e) if isinstance(e, list) else ():
        if isinstance(x, str) and (out := tr(x, lang)) != x:
            e[k], changed = out, True
    return changed
