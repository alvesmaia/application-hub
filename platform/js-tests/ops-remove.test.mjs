// Containers › "Remover aplicação": the remove button stays disabled until the typed name (or id) matches; the
// modal sends the confirmation, the password and the two options, then follows the job. Rule in node; the modal
// in a real Chromium (tests-e2e Playwright) — skipped if Playwright/the browser is not installed.
//   node --test platform/js-tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const platform = path.resolve(here, '..');
const script = path.join(platform, 'assets/web/ops-remove.js');
const require = createRequire(import.meta.url);
const { matches } = require(script);

test('the name or the id, case-insensitive and trimmed; anything else does not match', () => {
  const ok = ['Gestão de Ativos', 'inventory'];
  for (const typed of ['Gestão de Ativos', '  gestão  de ativos ', 'INVENTORY']) assert.ok(matches(typed, ok), typed);
  for (const typed of ['', '   ', 'Gestão', 'inventoryx', 'suppliers']) assert.ok(!matches(typed, ok), typed);
  assert.ok(matches(' Apps de Exemplo ', ['apps de exemplo', 'sample apps']));
});

async function browser() {
  try {
    const pw = createRequire(path.join(platform, 'tests-e2e', 'package.json'))('playwright');
    return await pw.chromium.launch();
  } catch (_) { return null; }
}

const PAGE = `<!doctype html><html><body><button type="button" id="rm" data-ops-remove="inventory" data-name="Gestão de Ativos">Remover aplicação</button>
<script src="/assets/ops-remove.js"></script></body></html>`;

test('modal: disabled until the name matches, then posts and follows the job', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const page = await b.newPage();
    const posts = [];
    await page.route('http://hub.test/**', async (route) => {
      const req = route.request();
      const u = new URL(req.url());
      if (u.pathname === '/assets/ops-remove.js') return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(script) });
      if (u.pathname === '/admin/runtime') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: PAGE });
      if (req.method() === 'POST') { posts.push({ path: u.pathname, body: JSON.parse(req.postData()) }); return route.fulfill({ status: 202, contentType: 'application/json', body: '{"id":7}' }); }
      if (u.pathname === '/api/ops/jobs/7') return route.fulfill({ contentType: 'application/json', body: '{"id":7,"done":true,"ok":true,"message":"Remoção concluída: Gestão de Ativos."}' });
      return route.fulfill({ status: 404, body: '' });
    });
    await page.goto('http://hub.test/admin/runtime');
    await page.click('#rm');
    const submit = page.locator('[data-rm="submit"]');
    assert.equal(await submit.isDisabled(), true, 'disabled when empty');
    await page.fill('[data-rm="confirm"]', 'Gestão');
    assert.equal(await submit.isDisabled(), true, 'disabled on a partial name');
    await page.fill('[data-rm="confirm"]', ' gestão de ativos ');
    assert.equal(await submit.isDisabled(), false, 'enabled once it matches');
    await page.fill('[data-rm="password"]', 'segredo');
    await page.check('[data-rm="keep"]');
    await Promise.all([page.waitForEvent('load'), submit.click()]);
    assert.deepEqual(posts, [{ path: '/api/ops/apps/inventory/remove', body: { confirm: 'gestão de ativos', password: 'segredo', backup: true, keep_data: true } }]);
    assert.equal(await page.evaluate(() => sessionStorage.getItem('hub-admin-toast')), 'Remoção concluída: Gestão de Ativos.');
  } finally {
    await b.close();
  }
});
