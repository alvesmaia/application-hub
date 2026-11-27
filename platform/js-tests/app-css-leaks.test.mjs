// The hub's own element rules must not reach an adapted app's original markup: the hub's font (an
// `!important` rule on every element) lets the app's font-family win inside #app-root, and the hub's
// heading weight stays out of `[data-hub-original]`. Real Chromium with the hub's stylesheets.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const platform = path.resolve(here, '..');
const assets = path.join(platform, 'assets/shell');

async function browser() {
  try {
    const pw = createRequire(path.join(platform, 'tests-e2e', 'package.json'))('playwright');
    return await pw.chromium.launch();
  } catch (_) { return null; }
}

test('hub stylesheets: the app keeps its own font and, in original markup, its heading weight', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const page = await b.newPage();
    const css = ['tokens.css', 'styles.css', 'hub.css'].map((f) => fs.readFileSync(path.join(assets, f), 'utf8')).join('\n');
    await page.setContent(`<!doctype html><html data-theme="light"><head><style>${css}</style></head><body>
      <h2 id="hub-h">Hub</h2>
      <main id="app-root"><p id="plain">sdk app text</p><h2 id="sdk-h">SDK heading</h2>
        <div data-hub-original><span class="lk-cat" id="mono">pp_environment</span><h2 id="orig-h">Original</h2></div>
      </main></body></html>`);
    await page.addScriptTag({ content: fs.readFileSync(path.join(assets, 'hub-safe.js'), 'utf8') });
    const r = await page.evaluate(() => {
      document.adoptedStyleSheets = [window.HubSafe.scopedSheet('.lk-cat{font-family:monospace}')];
      const cs = (id) => getComputedStyle(document.getElementById(id));
      return {
        mono: cs('mono').fontFamily, plain: cs('plain').fontFamily === cs('hub-h').fontFamily,
        hub: cs('hub-h').fontWeight, sdk: cs('sdk-h').fontWeight, orig: cs('orig-h').fontWeight,
      };
    });
    assert.deepEqual(r, { mono: 'monospace', plain: true, hub: '800', sdk: '800', orig: '700' });
  } finally { await b.close(); }
});
