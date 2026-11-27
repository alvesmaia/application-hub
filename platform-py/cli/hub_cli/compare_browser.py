"""Playwright side of `hub compare` (optional dependency): the screenshot and the style fingerprint of a page
region, measured in the browser (`getComputedStyle`)."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Any

from hub_cli.compare import Style, as_style

# Fonts weighted by text length (the hub's `app-` prefix of an app's own font removed), palette (colors
# quantized to 16 levels) weighted by area, radii, and counts of the main kinds of elements.
_FINGERPRINT_JS = """(sel) => {
  const root = document.querySelector(sel) || document.body;
  const q = (c) => { const m = String(c).match(/[\\d.]+/g); if (!m) return null; const [r, g, b, a = 1] = m.map(Number);
    if (a < 0.1) return null; const h = (v) => (Math.min(240, Math.round(v / 16) * 16)).toString(16).padStart(2, '0');
    return `#${h(r)}${h(g)}${h(b)}`; };
  const fonts = {}, palette = {}, radii = {}, counts = { button: 0, field: 0, row: 0, link: 0, heading: 0, icon: 0 };
  const add = (o, k, w) => { if (k != null && w > 0) o[k] = (o[k] || 0) + w; };
  for (const el of root.querySelectorAll('*')) {
    const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    const text = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join('');
    if (text) {
      add(fonts, cs.fontFamily.split(',')[0].replace(/["']/g, '').replace(/^app-/, '').trim().toLowerCase(), text.length);
      add(palette, q(cs.color), text.length * 40);
    }
    add(palette, q(cs.backgroundColor), (r.width * r.height) / 100);
    if (parseFloat(cs.borderTopWidth) > 0) add(palette, q(cs.borderTopColor), (r.width + r.height) * 2);
    const rad = Math.round(parseFloat(cs.borderTopLeftRadius)); if (rad > 0) add(radii, String(Math.min(rad, 99)), 1);
    const t = el.tagName;
    if (t === 'BUTTON' || el.getAttribute('role') === 'button') counts.button++;
    else if (t === 'INPUT' || t === 'SELECT' || t === 'TEXTAREA') counts.field++;
    else if (t === 'TR') counts.row++;
    else if (t === 'A') counts.link++;
    else if (/^H[1-6]$/.test(t)) counts.heading++;
    else if (t === 'svg' || t === 'IMG' || /symbol|icon/i.test(cs.fontFamily)) counts.icon++;
  }
  // Structure: table headers and form fields as the reader sees them (icon-font glyph names left out) and
  // images that did not load.
  const iconish = (n) => /symbol|icon/i.test(getComputedStyle(n).fontFamily) || n.getAttribute('aria-hidden') === 'true';
  const words = (el) => [...el.childNodes].map((n) => (n.nodeType === 3 ? n.textContent : n.nodeType === 1 && !iconish(n) ? words(n) : '')).join(' ');
  const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const shown = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width >= 2 && r.height >= 2 && cs.visibility !== 'hidden'; };
  const headers = {}, controls = {}, broken = {};
  for (const th of root.querySelectorAll('th')) if (shown(th)) add(headers, norm(words(th)) || null, 1);
  for (const f of root.querySelectorAll('input, select, textarea')) {
    if (!shown(f) || /^(hidden|submit|button|reset|image)$/i.test(f.type || '')) continue;
    add(controls, norm(`${f.tagName} ${f.name || f.getAttribute('aria-label') || f.placeholder || f.id}`), 1);
  }
  for (const img of root.querySelectorAll('img')) if (img.complete && img.naturalWidth === 0) add(broken, 'img', 1);
  // Components: each visible element keyed by tag, classes (not the hub's own `hub-*`) and its own text (not
  // its children's: a wrapped header cell must not change the table's key), with the computed style the eye
  // notices; font and color only where the element shows text. Repeated keys get #2, #3… Up to 800.
  const components = {}, seen = {};
  const font = (cs) => cs.fontFamily.split(',')[0].replace(/["']/g, '').replace(/^app-/, '').trim().toLowerCase();
  for (const el of root.querySelectorAll('*')) {
    if (Object.keys(components).length >= 800) break;
    if (el.closest('svg') !== null && el.tagName.toLowerCase() !== 'svg') continue;
    const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    const cls = [...el.classList].filter((c) => !c.startsWith('hub-')).sort().join('.');
    const own = norm([...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(' ')).slice(0, 30);
    if (!cls && !own) continue;
    let key = `${el.tagName.toLowerCase()}${cls ? '.' + cls : ''}|${own}`;
    seen[key] = (seen[key] || 0) + 1;
    if (seen[key] > 1) key += `#${seen[key]}`;
    const box = {
      background: cs.backgroundColor, padding: `${cs.paddingTop} ${cs.paddingRight} ${cs.paddingBottom} ${cs.paddingLeft}`,
      border: cs.borderTopStyle === 'none' || parseFloat(cs.borderTopWidth) === 0 ? 'none' : `${cs.borderTopWidth} ${cs.borderTopStyle} ${cs.borderTopColor}`, radius: cs.borderTopLeftRadius,
      height: String(Math.round(r.height)), width: String(Math.round(r.width)),
    };
    components[key] = own ? { ...box, color: cs.color, font: font(cs), size: cs.fontSize, weight: cs.fontWeight } : box;
  }
  return { fonts, palette, radii, counts, headers, controls, broken, components };
}"""
# The hub shows its loader before the app's fragment: wait until the app area has content and no loader.
_HUB_READY_JS = """() => { const r = document.getElementById('app-root');
  const busy = [...document.querySelectorAll('.app-loading, .app-skeleton, [data-hub-loading], .skeleton-block, #app-root [aria-busy="true"]')]
    .some((e) => e.getBoundingClientRect().height > 0 && getComputedStyle(e).display !== 'none');
  return Boolean(r && r.querySelector('#workspace, .workspace') && !busy); }"""


class PlaywrightBrowser:
    def __init__(self, api: ModuleType, width: int = 1440, height: int = 900) -> None:
        self._pw = api.sync_playwright().start()
        self._browser = self._pw.chromium.launch()
        self._page: Any = self._browser.new_page(viewport={"width": width, "height": height})

    def login(self, url: str, user: str, password: str) -> None:
        p = self._page
        p.goto(url)
        p.locator("input[type=email], input[type=text], input:not([type])").first.fill(user)
        p.locator("input[type=password]").first.fill(password)
        with p.expect_navigation():
            p.locator("input[type=password]").first.press("Enter")

    def shoot(self, url: str, path: Path, selector: str, hub: bool) -> Style:
        p = self._page
        p.goto(url)
        if hub:  # the hub keeps a live connection open (SSE): "network idle" never comes
            p.wait_for_function(_HUB_READY_JS, timeout=30000)
        else:
            p.wait_for_load_state("networkidle", timeout=15000)
        self._settle(selector)
        p.screenshot(path=str(path))
        return as_style(p.evaluate(_FINGERPRINT_JS, selector))

    def _settle(self, selector: str) -> None:
        """Waits until the region stops changing (lazy fragments, a loader fading out): the same element
        count twice, 400 ms apart, for at most 10 s."""
        count = "(s) => (document.querySelector(s) || document.body).querySelectorAll('*').length"
        last = -1
        for _ in range(25):
            now = self._page.evaluate(count, selector)
            if now == last:
                return
            last = now
            self._page.wait_for_timeout(400)

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()
