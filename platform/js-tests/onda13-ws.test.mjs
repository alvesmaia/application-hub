// Wave 13 — real time and reading (hub-kit data-hub-ws; hub-device: code-type toggle
// INSIDE the camera, "Próximo" and pause between reads). Pure rules in node; each
// behavior in real Chromium with the app-page CSP (Trusted Types) and a server
// simulated WebSocket (page.routeWebSocket). Skipped without Playwright/Chromium.
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
const require = createRequire(import.meta.url);
const Kit = require(path.join(assets, 'hub-kit.js'));
const Dev = require(path.join(assets, 'hub-device.js'));

test('data-hub-ws: espera crescente com teto, caminho do app e mensagens', () => {
  assert.equal(Kit.wsBackoff(0, 0.5), 1000);
  assert.equal(Kit.wsBackoff(1, 0.5), 2000);
  assert.equal(Kit.wsBackoff(3, 0.5), 8000);
  assert.equal(Kit.wsBackoff(99, 0.5), 30000, 'teto de 30 s');
  assert.equal(Kit.wsBackoff(0, 0), 750);
  assert.equal(Kit.wsBackoff(0, 1), 1250, '±25 %');
  assert.ok(Kit.isWsPath('/coletor/ws'));
  assert.ok(Kit.isWsPath('/coletor/ws/live?view=folha'));
  assert.ok(!Kit.isWsPath('/coletor/folha'));
  assert.ok(!Kit.isWsPath('/coletor/x/ws'));
  assert.ok(!Kit.isWsPath('//evil/ws'));
  assert.deepEqual(Kit.wsMessages('{"html":"<p>a</p>","target":"#x"}'), [{ html: '<p>a</p>', target: '#x' }]);
  assert.deepEqual(Kit.wsMessages('{"fragments":[{"html":"a"},{"event":"e","data":1}]}'), [{ html: 'a' }, { event: 'e', data: 1 }]);
  assert.deepEqual(Kit.wsMessages('[{"text":"t"}, 3, null]'), [{ text: 't' }]);
  assert.deepEqual(Kit.wsMessages('<b>cru</b>'), [{ html: '<b>cru</b>' }], 'não-JSON = HTML');
  assert.deepEqual(Kit.wsMessages('  '), []);
  assert.equal(Kit.wsMessages(JSON.stringify(Array.from({ length: 50 }, () => ({ html: 'x' })))).length, 20);
});

test('câmera: grupos da alternância, modo atual e pausa', () => {
  const m = Dev.scanModes('ean_13,ean_8,code_128|qr_code,data_matrix', 'barras|qr', 'Código de barras|QR', 'Use QR|Use barras');
  assert.deepEqual(m.map((x) => [x.key, x.label, x.formats, x.hint]), [
    ['barras', 'Código de barras', ['ean_13', 'ean_8', 'code_128'], 'Use QR'],
    ['qr', 'QR', ['qr_code', 'data_matrix'], 'Use barras'],
  ]);
  assert.deepEqual(Dev.scanModes('ean_13,qr_code', 'a', 'A'), [], 'um grupo só: sem alternância');
  assert.deepEqual(Dev.scanModes('ean_13|nada', 'a|b', 'A|B'), [], 'grupo sem formato válido sai');
  assert.equal(Dev.scanModes('ean_13|qr_code', 'a b|<x>', '')[0].key, 'm0', 'chave inválida vira índice');
  assert.equal(Dev.scanModes('ean_13|qr_code', 'a|b', '', 'mesmo')[1].hint, 'mesmo', 'um aviso para todos');
  assert.equal(Dev.modeIndex(m, 'qr'), 1);
  assert.equal(Dev.modeIndex(m, 'zzz'), 0);
  assert.equal(Dev.cooldownMs('2000'), 2000);
  assert.equal(Dev.cooldownMs('99999'), 10000);
  assert.equal(Dev.cooldownMs('-5'), 0);
  assert.equal(Dev.cooldownMs('x'), 0);
});

// ---- Chromium with the app CSP ------------------------------------------------------------
async function browser() {
  let pw;
  try {
    const req = createRequire(path.join(platform, 'tests-e2e', 'package.json'));
    pw = req('playwright');
  } catch (_) { return null; }
  try { return await pw.chromium.launch(); } catch (_) { return null; }
}

const CSP = "default-src 'self'; script-src https://hub.test/assets/; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src https://hub.test/_frag/coletor https://hub.test/_frag/coletor/ wss://hub.test/apps/coletor/ws/; worker-src https://hub.test/hub-sw.js; form-action https://hub.test/coletor https://hub.test/coletor/; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; require-trusted-types-for 'script'; trusted-types hub-parse hub-svg hub-script default";

const APP = `<div class="workspace" id="workspace"><section class="workspace-content" data-hub-feedback="beep vibrate flash">
<div id="live" data-hub-ws="/apps/coletor/ws/live?view=t" data-hub-ws-target="#estado" data-hub-get="/apps/coletor/estado" data-hub-trigger="every 1s" data-hub-target="#estado"><section id="estado">inicial</section></div>
<div id="fora" data-hub-ws="https://evil.example/ws" data-hub-get="/apps/coletor/estado" data-hub-trigger="every 1s" data-hub-target="#estado2"><section id="estado2">x</section></div>
<form id="f" method="post" action="/coletor/ler" data-hub-target="#painel" data-hub-offline-queue="Leitura"><input type="hidden" id="fmt" name="formato" value=""><input id="codigo" name="codigo" data-hub-wedge data-hub-clear><button type="submit">ok</button></form>
<input type="hidden" id="mf" name="modo" value="barras" data-hub-post="/apps/coletor/modo" data-hub-target="#mfout" data-hub-trigger="change"><div id="mfout"></div>
<button id="scan" type="button" data-hub-scan data-hub-scan-target="#codigo" data-hub-scan-format-target="#fmt" data-hub-scan-continuous data-hub-scan-formats="ean_13,code_128|qr_code,data_matrix" data-hub-scan-modes="barras|qr" data-hub-scan-mode-labels="Código de barras|QR" data-hub-scan-mode="barras" data-hub-scan-mode-target="#mf" data-hub-scan-wrong-hint="troque para QR|troque para barras" data-hub-scan-dedup="300">ler</button>
<section id="painel"></section>
</section></div>`;

/** Simulated app page (https://hub.test/coletor/x) with hub-safe + hub-kit + hub-device.
 *  `respond(p)` answers the POSTs; `gets` counts the GETs to /_frag (polling). */
async function appPage(b, { mocks = '', respond, ws } = {}) {
  const ctx = await b.newContext();
  const page = await ctx.newPage();
  const posts = [];
  const gets = [];
  const violations = [];
  page.on('console', (m) => { if (process.env.DEBUG_DEV) console.log('console:', m.text()); if (/Trusted Type|Content Security Policy|refused/i.test(m.text())) violations.push(m.text()); });
  page.on('pageerror', (e) => { if (process.env.DEBUG_DEV) console.log('pageerror:', e.message); });
  await ctx.addInitScript(mocks);
  if (ws) await page.routeWebSocket(/wss:\/\/hub\.test\/apps\/coletor\/ws\//, ws);
  await page.route('https://hub.test/**', async (route) => {
    const req = route.request();
    const u = new URL(req.url());
    const file = { '/assets/hub-safe.js': 'hub-safe.js', '/assets/hub-kit.js': 'hub-kit.js', '/assets/hub-device.js': 'hub-device.js', '/assets/hub-device-camera.js': 'hub-device-camera.js', '/assets/hub-device-forms.js': 'hub-device-forms.js', '/assets/hub-device-mount.js': 'hub-device-mount.js' }[u.pathname];
    if (file) return route.fulfill({ contentType: 'text/javascript; charset=utf-8', body: fs.readFileSync(path.join(assets, file)) });
    if (u.pathname === '/coletor/x') {
      return route.fulfill({ contentType: 'text/html; charset=utf-8', headers: { 'content-security-policy': CSP }, body: `<!doctype html><html><head></head><body><main id="app-root">${APP}</main><div id="toast"></div><script src="/assets/hub-safe.js"></script><script src="/assets/hub-kit.js"></script><script data-hub-app="coletor" data-hub-frag="/_frag" src="/assets/hub-device.js"></script><script src="/assets/hub-device-camera.js"></script><script src="/assets/hub-device-forms.js"></script><script src="/assets/hub-device-mount.js"></script><script src="/assets/boot.js"></script></body></html>` });
    }
    if (u.pathname === '/assets/boot.js') return route.fulfill({ contentType: 'text/javascript', body: "window.HubKit.mount(document.getElementById('app-root'));" });
    if (u.pathname.startsWith('/_frag/coletor/') && req.method() === 'GET') {
      gets.push(u.pathname);
      return route.fulfill({ contentType: 'text/html; charset=utf-8', body: `<section id="estado" data-n="${gets.length}">polling ${gets.length}</section>` });
    }
    if (u.pathname.startsWith('/_frag/coletor/') && req.method() === 'POST') {
      const p = { path: u.pathname, body: req.postData() || '' };
      posts.push(p);
      const r = respond ? respond(p) : { status: 200, body: `<section id="painel"><div data-hub-feedback-result="ok">lido ${new URLSearchParams(p.body).get('codigo')}</div></section>` };
      return route.fulfill({ status: r.status, contentType: 'text/html; charset=utf-8', body: r.body });
    }
    return route.fulfill({ status: 404, body: '' });
  });
  await page.goto('https://hub.test/coletor/x');
  await page.waitForFunction(() => window.HubDevice && window.HubKit);
  return { page, posts, gets, violations };
}

test('Chromium: data-hub-ws aplica fragmentos pelo sanitizador, eventos; cai → polling; volta → socket', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const socks = [];
    let refuse = false;
    const { page, gets, violations } = await appPage(b, {
      ws: (ws) => {
        if (refuse) { ws.close({ code: 1011 }); return; }
        socks.push(ws);
        ws.onMessage(() => {});
      },
    });
    await page.waitForFunction(() => document.getElementById('live').getAttribute('data-hub-ws-state') === 'open');
    assert.equal(socks.length, 1, 'um socket por URL');
    assert.equal(gets.length, 0, 'sem polling com o socket aberto');
    // Pushed fragment: enters through hub-safe (script and on* removed).
    socks[0].send(JSON.stringify({ target: '#estado', html: '<section id="estado" data-v="1"><b onclick="window.__x=1">novo</b><script>window.__pwned=1</script><img src=x onerror="window.__pwned=1"></section>' }));
    await page.waitForFunction(() => document.querySelector('#estado[data-v="1"]'));
    assert.equal(await page.evaluate(() => document.querySelector('#estado b').getAttribute('onclick')), null);
    assert.equal(await page.evaluate(() => document.querySelectorAll('#estado script').length), 0);
    assert.equal(await page.evaluate(() => window.__pwned), undefined);
    // Target outside the app is ignored; JSON event becomes hub:ws.
    await page.evaluate(() => { window.__ev = []; document.addEventListener('hub:ws', (e) => window.__ev.push(e.detail)); });
    socks[0].send(JSON.stringify({ fragments: [{ target: '#toast', html: '<p>x</p>' }, { event: 'leitura', data: { n: 2 } }] }));
    await page.waitForFunction(() => window.__ev.length === 1);
    assert.deepEqual(await page.evaluate(() => window.__ev[0]), { event: 'leitura', data: { n: 2 } });
    assert.equal(await page.evaluate(() => document.getElementById('toast').innerHTML), '');
    // Socket drops (server refuses reconnection): polling on data-hub-get (every 1s).
    refuse = true;
    socks[0].close({ code: 1001 });
    await page.waitForFunction(() => document.getElementById('live').getAttribute('data-hub-ws-state') === 'polling');
    await page.waitForFunction(() => /polling/.test(document.getElementById('estado').textContent), null, { timeout: 5000 });
    assert.ok(gets.length >= 1);
    // Back: reconnection with growing backoff → socket again and polling stops.
    refuse = false;
    await page.waitForFunction(() => document.getElementById('live').getAttribute('data-hub-ws-state') === 'open', null, { timeout: 15_000 });
    const n = gets.length;
    await page.waitForTimeout(2200);
    assert.ok(gets.length <= n + 1, `polling parou (${n} → ${gets.length})`);
    // data-hub-ws from another origin: never opens a socket, only polling.
    assert.equal(await page.getAttribute('#fora', 'data-hub-ws-state'), 'polling');
    assert.equal(socks.length, 2);
    assert.deepEqual(violations, []);
  } finally { await b.close(); }
});

const CAMERA = `
  navigator.vibrate = () => true;
  window.AudioContext = class { constructor() { this.state = 'running'; this.currentTime = 0; this.destination = {}; }
    resume() { return Promise.resolve(); }
    createOscillator() { return { type: '', frequency: {}, connect: (g) => g, start() {}, stop() {} }; }
    createGain() { return { gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect: (d) => d }; } };
  const fake = () => { const c = document.createElement('canvas'); c.width = 160; c.height = 120; const g = c.getContext('2d');
    setInterval(() => { g.fillStyle = '#' + Math.floor(Math.random() * 999); g.fillRect(0, 0, 160, 120); }, 40);
    return c.captureStream(15); };
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: async () => fake() } });
  window.__fmts = [];
  window.__codes = [];
  window.BarcodeDetector = class {
    constructor(o) { this.f = (o && o.formats) || []; window.__fmts.push(this.f); }
    static async getSupportedFormats() { return ['ean_13', 'code_128', 'qr_code', 'data_matrix']; }
    async detect() { await new Promise((r) => setTimeout(r, 25)); const c = window.__codes[0]; if (!c || !this.f.includes(c.format)) return []; window.__codes.shift(); return [c]; } };
`;

test('Chromium: alternância Código de barras | QR dentro da câmera troca os formatos sem fechar e avisa o app', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const { page, posts, violations } = await appPage(b, { mocks: CAMERA });
    await page.click('#scan');
    await page.waitForSelector('.hub-camera[data-decoder="nativo"]');
    assert.deepEqual(await page.$$eval('.hub-camera-mode', (bs) => bs.map((x) => [x.textContent, x.getAttribute('aria-pressed')])), [['Código de barras', 'true'], ['QR', 'false']]);
    assert.equal(await page.getAttribute('.hub-camera', 'data-formats'), 'ean_13,code_128');
    assert.ok(!(await page.getAttribute('.hub-camera-aim', 'class')).includes('square'));
    await page.click('.hub-camera-mode[data-mode="qr"]');
    await page.waitForFunction(() => document.querySelector('.hub-camera')?.dataset.mode === 'qr');
    assert.equal(await page.getAttribute('.hub-camera', 'data-formats'), 'qr_code,data_matrix');
    assert.ok((await page.getAttribute('.hub-camera-aim', 'class')).includes('hub-camera-aim-square'));
    await page.waitForFunction(() => window.__fmts.some((f) => f.join() === 'qr_code,data_matrix'));
    // The app learns of it (field + change → the app's own data-hub-post).
    await page.waitForFunction(() => document.getElementById('mf').value === 'qr');
    await page.waitForFunction(() => window.__codes.length === 0 || true);
    const deadline = Date.now() + 5000;
    while (!posts.some((p) => p.path === '/_frag/coletor/modo') && Date.now() < deadline) await page.waitForTimeout(50);
    assert.equal(new URLSearchParams(posts.find((p) => p.path === '/_frag/coletor/modo').body).get('modo'), 'qr');
    // Camera stays open and reads a QR with the new format.
    await page.evaluate(() => window.__codes.push({ rawValue: 'REC-1001', format: 'qr_code' }));
    await page.waitForFunction(() => document.querySelector('.hub-camera')?.dataset.reads === '1');
    assert.ok(await page.isVisible('.hub-camera'));
    assert.deepEqual(violations, []);
  } finally { await b.close(); }
});

test('Chromium: resposta com data-hub-scan-next para a câmera até "Próximo"; data-hub-scan-cooldown espera', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    let mode = 'hold';
    const respond = (p) => {
      if (p.path === '/_frag/coletor/proximo') return { status: 200, body: '<section id="painel"><div>liberado</div></section>' };
      const code = new URLSearchParams(p.body).get('codigo');
      if (mode === 'hold') return { status: 200, body: `<section id="painel"><div data-hub-feedback-result="ok">Item ${code}<button type="button" data-hub-scan-next data-hub-post="/apps/coletor/proximo" data-hub-target="#painel">Próximo</button></div></section>` };
      return { status: 200, body: `<section id="painel"><div data-hub-feedback-result="ok" data-hub-scan-cooldown="900">Item ${code}</div></section>` };
    };
    const { page, posts, violations } = await appPage(b, { mocks: CAMERA, respond });
    const reads = () => posts.filter((p) => p.path === '/_frag/coletor/ler').map((p) => new URLSearchParams(p.body).get('codigo'));
    await page.click('#scan');
    await page.waitForSelector('.hub-camera[data-decoder="nativo"]');
    await page.evaluate(() => window.__codes.push({ rawValue: '7890000000017', format: 'ean_13' }, { rawValue: 'COL-0002', format: 'code_128' }));
    await page.waitForSelector('.hub-camera[data-held] .hub-camera-next:not([hidden])');
    assert.equal(await page.textContent('.hub-camera-next'), 'Próximo');
    assert.ok(!(await page.textContent('.hub-camera-last')).includes('Próximo'), 'texto do cartão sem o botão');
    await page.waitForTimeout(600);
    assert.deepEqual(reads(), ['7890000000017'], 'parada: nada lido até Próximo');
    await page.click('.hub-camera-next');
    await page.waitForFunction(() => !document.querySelector('.hub-camera[data-held]'));
    const deadline = Date.now() + 5000;
    while (reads().length < 2 && Date.now() < deadline) await page.waitForTimeout(50);
    assert.ok(posts.some((p) => p.path === '/_frag/coletor/proximo'), 'o botão do app foi acionado');
    assert.deepEqual(reads().slice(0, 2), ['7890000000017', 'COL-0002']);
    // Automático mode: 900 ms pause before the next read.
    mode = 'cooldown';
    await page.waitForSelector('.hub-camera[data-held]');
    await page.click('.hub-camera-next');
    await page.evaluate(() => window.__codes.push({ rawValue: 'COL-0003', format: 'code_128' }));
    await page.waitForSelector('.hub-camera[data-cooldown="900"]');
    await page.evaluate(() => window.__codes.push({ rawValue: 'COL-0004', format: 'code_128' }));
    const t0 = Date.now();
    while (reads().length < 4 && Date.now() - t0 < 5000) await page.waitForTimeout(20);
    assert.deepEqual(reads().slice(2), ['COL-0003', 'COL-0004']);
    assert.ok(Date.now() - t0 >= 700, `esperou a pausa (${Date.now() - t0} ms)`);
    assert.deepEqual(violations, []);
  } finally { await b.close(); }
});
