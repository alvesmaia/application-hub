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
  return { fonts, palette, radii, counts };
}"""
# The hub shows its loader before the app's fragment: wait until the app area has content and no loader.
_HUB_READY_JS = """() => { const r = document.getElementById('app-root');
  const busy = [...document.querySelectorAll('.app-loading, .app-skeleton, [data-hub-loading]')]
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
        p.wait_for_timeout(800)
        p.screenshot(path=str(path))
        return as_style(p.evaluate(_FINGERPRINT_JS, selector))

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()
