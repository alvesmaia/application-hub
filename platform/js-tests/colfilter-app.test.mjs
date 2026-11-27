// Column filters (`data-hub-colfilter`) inside an app: an adapted app keeps the original's per-column
// filter/sort (spreadsheet style) without JS of its own. The fragment arrives after the page loaded, so
// HubKit.mount sets the table up (once, even when mounted again). Real Chromium via tests-e2e Playwright.
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

const TABLE = `<table data-hub-colfilter><thead><tr><th data-hub-col="st" data-hub-col-label="Status">Status</th><th>#</th></tr></thead>
<tbody><tr><td>Open</td><td>1</td></tr><tr><td>Done</td><td>2</td></tr><tr><td>Open</td><td>3</td></tr></tbody></table>`;

test('app fragment: HubKit.mount gives the columns their filter, once', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const page = await b.newPage();
    await page.setContent('<!doctype html><html><body data-hub-i18n="{}"><main id="app-root"></main></body></html>');
    for (const f of ['hub-safe.js', 'hub-kit.js', 'hub-kit-mount.js', 'hub-ux-colfilter.js']) {
      await page.addScriptTag({ content: fs.readFileSync(path.join(assets, f), 'utf8') });
    }
    const funnels = await page.evaluate((html) => {
      const root = document.getElementById('app-root');
      root.innerHTML = html;
      window.HubKit.mount(root);
      window.HubKit.mount(root);
      return root.querySelectorAll('[data-hub-cf-col]').length;
    }, TABLE);
    assert.equal(funnels, 1);
  } finally { await b.close(); }
});
