// data-hub-sse (hub-kit): EventSource em /_frag/{app}/…, HTML pelo hub-safe.js, evento hub:sse,
// fluxo fechado → polling no data-hub-get. Chromium real com a CSP de app; pulado sem Playwright.
//   node --test platform/js-tests/
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

const CSP = "default-src 'self'; script-src https://hub.test/assets/; connect-src https://hub.test/_frag/kanban https://hub.test/_frag/kanban/; require-trusted-types-for 'script'; trusted-types hub-parse hub-svg hub-script default";
const APP = `<div id="q" data-hub-sse="/apps/kanban/eventos" data-hub-get="/apps/kanban/quadro" data-hub-trigger="every 1s"><section>inicial</section></div>
<div id="fora" data-hub-sse="https://evil.example/eventos" data-hub-get="/apps/kanban/quadro" data-hub-trigger="every 1s" data-hub-target="#f2"><section id="f2">x</section></div>`;

test('Chromium: data-hub-sse aplica o HTML sanitizado, dispara hub:sse e cai para polling', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const page = await (await b.newContext()).newPage();
    const violations = [];
    page.on('console', (m) => { if (process.env.DEBUG_DEV) console.log('console:', m.text()); if (/Trusted Type|Content Security Policy|refused/i.test(m.text())) violations.push(m.text()); });
    page.on('pageerror', (e) => { if (process.env.DEBUG_DEV) console.log('pageerror', e.message); });
    let streams = 0;
    let gets = 0;
    await page.route('https://hub.test/**', async (route) => {
      const u = new URL(route.request().url()); if (process.env.DEBUG_DEV) console.log('req', u.pathname);
      const file = { '/assets/hub-safe.js': 'hub-safe.js', '/assets/hub-kit.js': 'hub-kit.js' }[u.pathname];
      if (file) return route.fulfill({ contentType: 'text/javascript; charset=utf-8', body: fs.readFileSync(path.join(assets, file)) });
      if (u.pathname === '/assets/boot.js') return route.fulfill({ contentType: 'text/javascript', body: "window.HubKit.mount(document.getElementById('app-root'));" });
      if (u.pathname === '/kanban/x') {
        return route.fulfill({ contentType: 'text/html; charset=utf-8', headers: { 'content-security-policy': CSP }, body: `<!doctype html><html><body><main id="app-root">${APP}</main><script src="/assets/hub-safe.js"></script><script data-hub-app="kanban" src="/assets/hub-kit.js"></script><script src="/assets/boot.js"></script></body></html>` });
      }
      if (u.pathname === '/_frag/kanban/eventos') {
        streams++;
        if (streams > 1) return route.fulfill({ status: 503, body: '' }); // fecha de vez → polling
        const msg = (o) => `data: ${JSON.stringify(o)}\n\n`;
        return route.fulfill({ contentType: 'text/event-stream', body:
          msg({ html: '<div id="q" data-hub-sse="/apps/kanban/eventos" data-hub-get="/apps/kanban/quadro" data-hub-trigger="every 1s"><section data-v="1"><b onclick="window.__x=1">novo</b><script>window.__pwned=1</script></section></div>' })
          + msg({ event: 'quadro', data: { n: 1 } }) });
      }
      if (u.pathname === '/_frag/kanban/quadro') { gets++; return route.fulfill({ contentType: 'text/html', body: `<section data-poll="${gets}">polling</section>` }); }
      return route.fulfill({ status: 404, body: '' });
    });
    await page.addInitScript(() => { window.__ev = []; document.addEventListener('hub:sse', (e) => window.__ev.push(e.detail)); });
    await page.goto('https://hub.test/kanban/x');
    await page.waitForFunction(() => document.querySelector('#q section[data-v="1"]'));
    assert.equal(await page.evaluate(() => document.querySelector('#q b').getAttribute('onclick')), null);
    assert.equal(await page.evaluate(() => window.__pwned), undefined);
    await page.waitForFunction(() => window.__ev.length === 1);
    assert.deepEqual(await page.evaluate(() => window.__ev[0]), { event: 'quadro', data: { n: 1 } });
    await page.waitForFunction(() => document.getElementById('q').getAttribute('data-hub-sse-state') === 'polling', null, { timeout: 10_000 });
    await page.waitForFunction(() => document.querySelector('#app-root [data-poll]'), null, { timeout: 5000 });
    assert.ok(gets >= 1);
    assert.equal(await page.getAttribute('#fora', 'data-hub-sse-state'), 'polling', 'outra origem: só polling');
    assert.deepEqual(violations, []);
  } finally { await b.close(); }
});
