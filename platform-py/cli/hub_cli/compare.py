"""`hub compare`: the original app and the adapted one (inside `hub dev`) page by page.

Each route is opened on both sides (the original page's `--original-selector`, default `body`; the hub's
`#app-root`): a screenshot for the eyes and a **style fingerprint** for the number — the fonts of the text
(weighted by text length), the palette (text, background and border colors weighted by area), the corner radii
and how many buttons, fields, table rows, links, headings and icons there are. The difference (0-100%) is
the mean of what does not match; pixels are not compared (the hub moves things a few pixels on purpose, a
rewrite in another look keeps similar pixels). The structure is checked apart (table columns, form
fields, broken images): a port that adds filter fields or drops a column fails whatever the style.
`.hub/compare/index.html` shows the pages side by side with what differs; the command fails while any page
differs more than `--max-diff` or changed its structure.
"""

from __future__ import annotations

import html
import importlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

from hub_cli.errors import CliError

INSTALL = (
    "hub compare precisa do Playwright: uv tool install --reinstall hub-cli --with playwright "
    "--with-executables-from playwright --index <hub>/dev/cli/simple/ && playwright install chromium"
)
Style = dict[str, dict[str, float]]


class Browser(Protocol):
    def login(self, url: str, user: str, password: str) -> None: ...
    def shoot(self, url: str, path: Path, selector: str, hub: bool) -> Style: ...


@dataclass
class Options:
    original: str
    routes: list[str]
    directory: Path = Path()
    hub: str = "http://127.0.0.1:4173"
    original_selector: str = "body"
    login: str | None = None
    max_diff: float = 25.0


@dataclass
class Row:
    route: str
    name: str
    diff: dict[str, float]
    why: list[str]
    structure: list[str]

    def failed(self, limit: float) -> bool:
        return self.diff["total"] > limit or bool(self.structure)


def slug(route: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", route.lower()).strip("-") or "root"


def _shares(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values()) or 1.0
    return {k: v / total for k, v in weights.items()}


def _overlap(a: dict[str, float], b: dict[str, float]) -> float:
    """Share of the weight both sides have in common (1 = same distribution)."""
    if not a and not b:
        return 1.0
    sa, sb = _shares(a), _shares(b)
    return sum(min(sa.get(k, 0.0), sb.get(k, 0.0)) for k in set(sa) | set(sb))


def _counts(a: dict[str, float], b: dict[str, float]) -> float:
    kinds = [k for k in set(a) | set(b) if a.get(k, 0) or b.get(k, 0)]
    if not kinds:
        return 1.0
    return sum(min(a.get(k, 0), b.get(k, 0)) / max(a.get(k, 0), b.get(k, 0)) for k in kinds) / len(kinds)


def difference(original: Style, hub: Style) -> dict[str, float]:
    """Per aspect and in total, how far (0-100%) the hub page is from the original's style."""
    parts = {
        "fonts": _overlap(original.get("fonts", {}), hub.get("fonts", {})),
        "palette": _overlap(original.get("palette", {}), hub.get("palette", {})),
        "radii": _overlap(original.get("radii", {}), hub.get("radii", {})),
        "counts": _counts(original.get("counts", {}), hub.get("counts", {})),
    }
    out = {k: round((1 - v) * 100, 1) for k, v in parts.items()}
    out["total"] = round(sum(out.values()) / len(parts), 1)
    return out


def _top(weights: dict[str, float], n: int = 4) -> list[str]:
    return [k for k, v in sorted(weights.items(), key=lambda kv: -kv[1]) if _shares(weights)[k] >= 0.05][:n]


def explain(original: Style, hub: Style) -> list[str]:
    """What to fix: the original's main fonts, colors and radii missing in the hub; counts that differ."""
    lines: list[str] = []
    for aspect, label in (("fonts", "fonte"), ("palette", "cor"), ("radii", "raio")):
        missing = [k for k in _top(original.get(aspect, {})) if k not in hub.get(aspect, {})]
        lines += [f"{label} do original ausente: {k}" for k in missing]
    a, b = original.get("counts", {}), hub.get("counts", {})
    for kind in sorted(set(a) | set(b)):
        x, y = int(a.get(kind, 0)), int(b.get(kind, 0))
        if max(x, y) and min(x, y) / max(x, y) < 0.7:
            lines.append(f"{kind}: {x} no original, {y} no hub")
    return lines


def structure(original: Style, hub: Style) -> list[str]:
    """What the port changed in the page itself, whatever the style: table columns missing or added, fields
    the original does not have (e.g. filters above a table that had them in the header), broken images."""
    a_cols, b_cols = original.get("headers", {}), hub.get("headers", {})
    lines = [f"coluna do original ausente: {k}" for k in sorted(set(a_cols) - set(b_cols))]
    lines += [f"coluna que o original não tem: {k}" for k in sorted(set(b_cols) - set(a_cols))]
    a_fields, b_fields = original.get("controls", {}), hub.get("controls", {})
    lines += [f"campo do original ausente: {k}" for k in sorted(set(a_fields) - set(b_fields))]
    lines += [f"campo que o original não tem: {k}" for k in sorted(set(b_fields) - set(a_fields))]
    broken = int(sum(hub.get("broken", {}).values()) - sum(original.get("broken", {}).values()))
    if broken > 0:
        lines.append(f"imagem quebrada no hub: {broken}")
    return lines


def _app_id(directory: Path) -> str:
    mf = directory / "manifest.json"
    if not mf.is_file():
        raise CliError(f"manifest.json não encontrado em {directory.resolve()}", 2)
    return str(json.loads(mf.read_text(encoding="utf-8"))["id"])


def _login(opts: Options, browser: Browser) -> None:
    if not opts.login:
        return
    user, password = os.environ.get("HUB_COMPARE_USER"), os.environ.get("HUB_COMPARE_PASSWORD")
    if not user or password is None:
        raise CliError("--login precisa de HUB_COMPARE_USER e HUB_COMPARE_PASSWORD no ambiente", 2)
    browser.login(opts.original.rstrip("/") + opts.login, user, password)


def run(opts: Options, browser: Browser) -> int:
    out = opts.directory / ".hub" / "compare"
    out.mkdir(parents=True, exist_ok=True)
    hub_base = f"{opts.hub.rstrip('/')}/apps/{_app_id(opts.directory)}"
    _login(opts, browser)
    rows = [_compare_route(opts, browser, out, hub_base, r) for r in opts.routes]
    (out / "index.html").write_text(report(rows, opts.max_diff), encoding="utf-8")
    return _summary(rows, opts.max_diff, out)


def _compare_route(opts: Options, browser: Browser, out: Path, hub_base: str, route: str) -> Row:
    name = slug(route)
    a = browser.shoot(
        opts.original.rstrip("/") + route, out / f"original--{name}.png", opts.original_selector, False
    )
    b = browser.shoot(hub_base + route, out / f"hub--{name}.png", "#app-root", True)
    return Row(route, name, difference(a, b), explain(a, b), structure(a, b))


def _summary(rows: list[Row], limit: float, out: Path) -> int:
    worse = [r for r in rows if r.failed(limit)]
    for r in rows:
        print(f"{'FALHOU' if r in worse else 'ok    '}  {r.diff['total']:5.1f}%  {r.route}")
        for ln in r.structure + r.why if r in worse else []:
            print(f"          - {ln}")
    print(f"relatório: {(out / 'index.html').resolve()}")
    if worse:
        print(
            f"{len(worse)} página(s) acima do limite de {limit:g}% ou com a estrutura mudada: "
            "aproxime do original e rode de novo"
        )
    return 1 if worse else 0


def report(rows: list[Row], limit: float) -> str:
    def row(r: Row) -> str:
        cls = "bad" if r.failed(limit) else "ok"
        parts = " · ".join(f"{k} {v:.1f}%" for k, v in r.diff.items() if k != "total")
        why = "".join(f"<li>{html.escape(w)}</li>" for w in r.structure + r.why)
        return (
            f'<section><h2 class="{cls}">{html.escape(r.route)} — {r.diff["total"]:.1f}%</h2><p>{parts}</p>'
            f'<ul>{why}</ul><div class="pair">'
            f'<figure><img src="original--{r.name}.png" alt=""><figcaption>original</figcaption></figure>'
            f'<figure><img src="hub--{r.name}.png" alt=""><figcaption>no hub</figcaption></figure></div></section>'
        )

    style = (
        "body{font:14px system-ui;margin:24px;background:#f6f7f9;color:#1d2733}.pair{display:grid;"
        "grid-template-columns:1fr 1fr;gap:12px}img{width:100%;border:1px solid #c8ced6}figure{margin:0}"
        "h2.bad{color:#b42318}h2.ok{color:#1a7f37}"
    )
    body = "".join(row(r) for r in rows)
    return f'<!doctype html><meta charset="utf-8"><title>hub compare</title><style>{style}</style>' + (
        f"<h1>hub compare (limite {limit:g}%)</h1>{body}"
    )


def _playwright() -> ModuleType | None:
    """The optional Playwright module (`--with playwright`), or None."""
    try:
        return importlib.import_module("playwright.sync_api")
    except ImportError:
        return None


def real_browser() -> Browser:
    api = _playwright()
    if api is None:
        raise CliError(INSTALL, 2)
    from hub_cli.compare_browser import PlaywrightBrowser

    return PlaywrightBrowser(api)


def as_style(raw: Any) -> Style:
    """The browser's answer as a fingerprint (keeps only numeric weights of the known aspects)."""
    aspects = ("fonts", "palette", "radii", "counts", "headers", "controls", "broken")
    src = raw if isinstance(raw, dict) else {}
    return {a: {str(k): float(v) for k, v in (src.get(a) or {}).items()} for a in aspects}
