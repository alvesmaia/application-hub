// Two kit behaviors for adapted apps (no JS of their own): a dropdown menu (split buttons, "⋮" menus) and
// drag and drop between/within lists that posts the move (kanban boards). Real Chromium (tests-e2e Playwright).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const platform = path.resolve(here, '..');
const assets = path.join(platform, 'assets/shell');
const KIT = ['hub-safe.js', 'hub-kit.js', 'hub-kit-menu-drag.js', 'hub-kit-mount.js'];

async function browser() {
  try {
    const pw = createRequire(path.join(platform, 'tests-e2e', 'package.json'))('playwright');
    return await pw.chromium.launch();
  } catch (_) { return null; }
}

const PAGE = `<!doctype html><html data-hub-app="demo"><body data-hub-i18n="{}"><main id="app-root">
<div class="split"><a href="/apps/demo/new">New</a><button type="button" id="caret" data-hub-dropdown="#menu">v</button>
<div id="menu" hidden><a href="#" id="item">Import</a></div></div>
<div class="board">
  <section id="todo" data-hub-dropzone data-hub-drop-name="new_state" data-hub-drop-value="todo"><div class="body">
    <article id="c1" data-hub-drag="/apps/demo/board/1/reorder" data-hub-drag-id="1" style="height:40px">1</article>
    <article id="c2" data-hub-drag="/apps/demo/board/2/reorder" data-hub-drag-id="2" style="height:40px">2</article>
  </div></section>
  <section id="done" data-hub-dropzone data-hub-drop-name="new_state" data-hub-drop-value="done" data-hub-drop-body=".body" style="min-height:200px"><div class="body">
    <article id="c3" data-hub-drag="/apps/demo/board/3/reorder" data-hub-drag-id="3">
      <ul data-hub-dropzone data-hub-drop-group="sub" data-hub-drop-name="card" data-hub-drop-value="3" style="margin:0;height:30px"><li data-hub-drag="/apps/demo/sub/9/reorder" data-hub-drag-id="9" data-hub-drag-group="sub">s9</li></ul>3
    </article>
  </div></section>
</div></main></body></html>`;

async function open(b, answer) {
  const page = await b.newPage();
  const posts = [];
  await page.route('http://hub.test/**', async (route) => {
    const req = route.request();
    const u = new URL(req.url());
    if (u.pathname.startsWith('/assets/')) {
      return route.fulfill({ contentType: 'text/javascript; charset=utf-8', body: fs.readFileSync(path.join(assets, u.pathname.slice(8))) });
    }
    if (u.pathname === '/apps/demo/board') {
      return route.fulfill({ contentType: 'text/html; charset=utf-8', body: PAGE.replace('</body>', `${KIT.map((f) => `<script src="/assets/${f}"></script>`).join('')}</body>`) });
    }
    if (req.method() === 'POST') { posts.push({ path: u.pathname, body: req.postData() }); return route.fulfill({ status: answer, body: '' }); }
    return route.fulfill({ status: 404, body: '' });
  });
  await page.goto('http://hub.test/apps/demo/board');
  await page.evaluate(() => window.HubKit.mount(document.getElementById('app-root')));
  return { page, posts };
}

test('dropdown: the button opens and closes its menu (outside click, Escape, an item)', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const { page } = await open(b, 200);
    const shown = () => page.evaluate(() => [!document.getElementById('menu').hidden, document.getElementById('caret').getAttribute('aria-expanded')]);
    await page.click('#caret');
    assert.deepEqual(await shown(), [true, 'true']);
    await page.mouse.click(5, 400);
    assert.deepEqual(await shown(), [false, 'false']);
    await page.click('#caret');
    await page.keyboard.press('Escape');
    assert.deepEqual(await shown(), [false, 'false']);
    await page.click('#caret');
    await page.click('#item');
    assert.deepEqual(await shown(), [false, 'false']);
  } finally { await b.close(); }
});

test('drag and drop: the card moves to the other list, before the card under it, and the move is posted', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const { page, posts } = await open(b, 200);
    assert.equal(await page.getAttribute('#c1', 'draggable'), 'true');
    // dropped on the subtask list of #c3 (another group): the card goes to the column around it
    await page.dragAndDrop('#c1', '#c3 ul', { targetPosition: { x: 5, y: 2 } });
    await page.waitForTimeout(200);
    assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('#done .body > article')].map((e) => e.id)), ['c1', 'c3']);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].path, '/_frag/demo/board/1/reorder');
    assert.deepEqual(Object.fromEntries(new URLSearchParams(posts[0].body)), { new_state: 'done', before_id: '3' });
  } finally { await b.close(); }
});

test('drag and drop: a refused move puts the card back', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const { page } = await open(b, 422);
    await page.dragAndDrop('#c2', '#done', { targetPosition: { x: 5, y: 150 } });
    await page.waitForTimeout(300);
    assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('#todo .body > article')].map((e) => e.id)), ['c1', 'c2']);
  } finally { await b.close(); }
});

test('drag and drop: while dragging, a placeholder the size of the card marks where it will land', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const { page } = await open(b, 200);
    const r = await page.evaluate(async () => {
      const done = document.getElementById('done');
      done.setAttribute('data-hub-drop-indicator', 'kbv-drop-indicator');
      const c1 = document.getElementById('c1');
      const dt = new DataTransfer();
      c1.dispatchEvent(new DragEvent('dragstart', { bubbles: true, dataTransfer: dt }));
      const top = document.getElementById('c3').getBoundingClientRect().top;
      document.getElementById('c3').dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt, clientY: top + 2 }));
      const ph = done.querySelector('.kbv-drop-indicator');
      const seen = ph && { next: ph.nextElementSibling.id, height: ph.style.height };
      c1.dispatchEvent(new DragEvent('dragend', { bubbles: true, dataTransfer: dt }));
      return { seen, after: document.querySelectorAll('.kbv-drop-indicator').length };
    });
    assert.deepEqual(r, { seen: { next: 'c3', height: '40px' }, after: 0 });
  } finally { await b.close(); }
});
