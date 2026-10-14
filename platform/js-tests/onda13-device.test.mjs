// Wave 13 — device features (hub-device.js): pure rules in node and each
// data-hub-* behavior in real Chromium with the app-page CSP (Trusted Types),
// with simulated camera/BarcodeDetector/vibration/audio and real IndexedDB. Skipped without
// Playwright/Chromium.   node --test platform/js-tests/
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
const Dev = require(path.join(assets, 'hub-device.js'));

test('formatos: lista do atributo, só os conhecidos; vazio = padrão', () => {
  assert.deepEqual(Dev.parseFormats('ean_13, QR_CODE,xyz'), ['ean_13', 'qr_code']);
  assert.ok(Dev.parseFormats('').includes('data_matrix'));
  assert.ok(Dev.parseFormats('nada').includes('ean_13'));
  assert.equal(Dev.FORMATS.code_128, 'CODE_128');
});

test('modo da câmera: QR = mira quadrada; o outro modo é reconhecido para avisar', () => {
  const qr = Dev.parseFormats('qr_code,data_matrix');
  const bar = Dev.parseFormats('ean_13,ean_8,upc_a,upc_e,code_128,code_39,itf');
  assert.ok(Dev.is2d(qr));
  assert.ok(!Dev.is2d(bar));
  assert.ok(!Dev.is2d(Dev.parseFormats('')));
  assert.deepEqual(Dev.complement(bar), ['qr_code', 'data_matrix']);
  assert.ok(Dev.complement(qr).includes('ean_13'));
  assert.ok(Dev.outside('qr_code', bar));
  assert.ok(!Dev.outside('ean_13', bar));
  assert.ok(!Dev.outside('', bar));
});

test('leitor tipo teclado: rajada rápida vs. digitação humana; repetição na janela', () => {
  assert.ok(Dev.isBurst([0, 10, 20, 31, 40, 52]));
  assert.ok(!Dev.isBurst([0, 120, 260, 380, 500]), 'humano');
  assert.ok(!Dev.isBurst([0, 10, 20]), 'curto demais');
  assert.ok(!Dev.isBurst([0, 10, 20, 30, 400]), 'pausa no meio');
  assert.ok(Dev.duplicate({ code: 'A', at: 1000 }, 'A', 1500, 800));
  assert.ok(!Dev.duplicate({ code: 'A', at: 1000 }, 'A', 2000, 800));
  assert.ok(!Dev.duplicate({ code: 'A', at: 1000 }, 'B', 1100, 800));
  assert.ok(!Dev.duplicate(null, 'A', 1, 800));
});

test('foto: redução para 1600 px no maior lado, sem ampliar', () => {
  assert.deepEqual(Dev.fit(4000, 3000), [1600, 1200]);
  assert.deepEqual(Dev.fit(3000, 4000), [1200, 1600]);
  assert.deepEqual(Dev.fit(800, 600), [800, 600]);
});

test('retorno: tipos, tons e vibração', () => {
  assert.equal(Dev.feedbackKind(' OK '), 'ok');
  assert.equal(Dev.feedbackKind('error'), 'error');
  assert.equal(Dev.feedbackKind('<b>'), null);
  for (const k of ['ok', 'warn', 'error', 'queued']) {
    assert.ok(Dev.tones(k).length > 0, k);
    assert.ok(Dev.vibration(k).length > 0, k);
  }
  assert.ok(Dev.vibration('error').reduce((a, b) => a + b) > Dev.vibration('ok').reduce((a, b) => a + b));
});

test('fila offline: resultado de cada reenvio', () => {
  assert.equal(Dev.replayOutcome(200), 'done');
  assert.equal(Dev.replayOutcome(201), 'done');
  assert.equal(Dev.replayOutcome(409), 'error');
  assert.equal(Dev.replayOutcome(422), 'error');
  assert.equal(Dev.replayOutcome(404), 'error');
  assert.equal(Dev.replayOutcome(0), 'retry');
  assert.equal(Dev.replayOutcome(503), 'retry');
  assert.equal(Dev.replayOutcome(429), 'retry');
  assert.equal(Dev.replayOutcome(401), 'login');
  assert.ok(Dev.queueFirst(false) && !Dev.queueFirst(true));
});

test('diagnóstico: relatório; iOS; texto de resposta; luminância', () => {
  const r = Dev.report([{ label: 'Câmera', result: 'OK', detail: '1280×720' }], [['App', 'Coletor 1.0.0']], { ua: 'UA', when: 'hoje' });
  assert.match(r, /- Câmera: OK \(1280×720\)/);
  assert.match(r, /App: Coletor 1\.0\.0/);
  assert.match(r, /Navegador: UA/);
  assert.ok(Dev.isIOS('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)', 5));
  assert.ok(Dev.isIOS('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', 5), 'iPad em modo desktop');
  assert.ok(!Dev.isIOS('Mozilla/5.0 (Linux; Android 15)', 5));
  assert.equal(Dev.plain('<div><strong>Erro</strong> &amp; <p>x</p></div>'), 'Erro & x');
  assert.deepEqual([...Dev.luminance(new Uint8ClampedArray([255, 255, 255, 255, 0, 0, 0, 255]), 2, 1)], [255, 0]);
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

const CSP = "default-src 'self'; script-src https://hub.test/assets/; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src https://hub.test/_frag/coletor https://hub.test/_frag/coletor/; worker-src https://hub.test/hub-sw.js; form-action https://hub.test/coletor https://hub.test/coletor/; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; require-trusted-types-for 'script'; trusted-types hub-parse hub-svg hub-script default";
const APP = `<div class="workspace" id="workspace"><section class="workspace-content" data-hub-feedback="beep vibrate flash">
<div data-hub-offline-status><span data-hub-offline-text></span></div><ul data-hub-offline-errors hidden></ul>
<form id="f" method="post" action="/coletor/ler" data-hub-target="#painel" data-hub-offline-queue="Leitura"><input id="codigo" name="codigo" data-hub-wedge data-hub-clear><button type="submit">ok</button></form>
<button id="scan" type="button" data-hub-scan data-hub-scan-target="#codigo">ler</button>
<button id="scan-c" type="button" data-hub-scan data-hub-scan-target="#codigo" data-hub-scan-continuous>ler contínuo</button>
<form id="fp" method="post" action="/coletor/foto" enctype="multipart/form-data"><input id="foto" type="file" name="foto"><button id="cap" type="button" data-hub-capture="#foto" data-hub-capture-preview="#prev">foto</button><div id="prev"></div></form>
<section id="painel"></section>
<section data-hub-diag><table><tr data-hub-diag-check="secure"><th>Seguro</th><td data-hub-diag-status></td></tr><tr data-hub-diag-check="vibrate"><th>Vibração</th><td data-hub-diag-status></td><td><button data-hub-diag-run>t</button></td></tr><tr data-hub-diag-check="wedge"><th>Leitor</th><td data-hub-diag-status></td><td><input id="dw" data-hub-wedge data-hub-diag-wedge></td></tr></table><ul><li data-hub-diag-info="App">Coletor</li></ul><button id="copy" data-hub-diag-copy>copiar</button></section>
<button id="print" data-hub-print>imprimir</button><button data-hub-install hidden>instalar</button><p data-hub-install-ios hidden>ios</p>
</section></div>`;

/** Simulated app page at https://hub.test/coletor/x with the hub scripts. `mocks`:
 *  init script (camera/BarcodeDetector/vibration/audio). `posts` collects the submissions. */
async function appPage(b, { mocks = '', respond } = {}) {
  const ctx = await b.newContext({ permissions: ['clipboard-read', 'clipboard-write'] });
  const page = await ctx.newPage();
  const posts = [];
  const violations = [];
  page.on('console', (m) => { if (process.env.DEBUG_DEV) console.log('console:', m.text()); if (/Trusted Type|Content Security Policy|refused/i.test(m.text())) violations.push(m.text()); });
  page.on('pageerror', (e) => { if (process.env.DEBUG_DEV) console.log('pageerror:', e.message); });
  await ctx.addInitScript(mocks);
  await page.route('https://hub.test/**', async (route) => {
    const req = route.request();
    const u = new URL(req.url());
    const file = { '/assets/hub-safe.js': 'hub-safe.js', '/assets/hub-kit.js': 'hub-kit.js', '/assets/hub-device.js': 'hub-device.js', '/assets/hub-device-camera.js': 'hub-device-camera.js', '/assets/hub-device-forms.js': 'hub-device-forms.js', '/assets/hub-device-mount.js': 'hub-device-mount.js', '/assets/vendor/zxing.min.js': 'vendor/zxing.min.js' }[u.pathname];
    if (file) return route.fulfill({ contentType: 'text/javascript; charset=utf-8', body: fs.readFileSync(path.join(assets, file)) });
    if (u.pathname === '/coletor/x') {
      return route.fulfill({ contentType: 'text/html; charset=utf-8', headers: { 'content-security-policy': CSP }, body: `<!doctype html><html><head></head><body><main id="app-root">${APP}</main><div id="toast"></div><script src="/assets/hub-safe.js"></script><script src="/assets/hub-kit.js"></script><script data-hub-app="coletor" data-hub-frag="/_frag" src="/assets/hub-device.js"></script><script src="/assets/hub-device-camera.js"></script><script src="/assets/hub-device-forms.js"></script><script src="/assets/hub-device-mount.js"></script></body></html>` });
    }
    if (u.pathname.startsWith('/_frag/coletor/') && req.method() === 'POST') {
      const p = { path: u.pathname, body: req.postData() || '', key: req.headers()['idempotency-key'] };
      posts.push(p);
      const r = respond ? respond(p) : { status: 200, body: `<section id="painel"><div data-hub-feedback-result="ok">lido ${new URLSearchParams(p.body).get('codigo')}</div></section>` };
      return route.fulfill({ status: r.status, contentType: 'text/html; charset=utf-8', body: r.body });
    }
    return route.fulfill({ status: 404, body: '' });
  });
  await page.goto('https://hub.test/coletor/x');
  await page.waitForFunction(() => window.HubDevice && window.HubKit);
  return { page, posts, violations, ctx };
}

const CAMERA = `
  window.__vib = [];
  navigator.vibrate = (p) => { window.__vib.push(p); return true; };
  window.__tones = 0;
  window.AudioContext = class { constructor() { this.state = 'running'; this.currentTime = 0; this.destination = {}; }
    resume() { return Promise.resolve(); }
    createOscillator() { window.__tones++; return { type: '', frequency: {}, connect: (g) => g, start() {}, stop() {} }; }
    createGain() { return { gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect: (d) => d }; } };
  const fake = () => { const c = document.createElement('canvas'); c.width = 320; c.height = 240; const g = c.getContext('2d');
    setInterval(() => { g.fillStyle = '#' + Math.floor(Math.random() * 999); g.fillRect(0, 0, 320, 240); }, 40);
    const s = c.captureStream(15); const tr = s.getVideoTracks()[0];
    tr.getCapabilities = () => ({ torch: true }); tr.applyConstraints = async (c) => { window.__torch = c; }; return s; };
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: async (c) => { window.__gum = c; return fake(); } } });
`;
const DETECTOR = `
  window.__codes = ['7890000000017', 'COL-0002', 'COL-0002', 'COL-0003'];
  window.BarcodeDetector = class { static async getSupportedFormats() { return ['ean_13', 'code_128', 'qr_code']; }
    async detect() { await new Promise((r) => setTimeout(r, 30)); const c = window.__codes.shift(); return c ? [{ rawValue: c, format: 'ean_13' }] : []; } };
`;

test('Chromium: câmera (BarcodeDetector) preenche o campo e envia; contínuo, lanterna, câmera traseira, retorno', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const { page, posts, violations } = await appPage(b, { mocks: CAMERA + DETECTOR });
    await page.click('#scan');
    await page.waitForFunction(() => document.querySelectorAll('[data-hub-feedback-result]').length > 0 && !document.querySelector('.hub-camera'));
    assert.equal(posts.length, 1);
    assert.equal(new URLSearchParams(posts[0].body).get('codigo'), '7890000000017');
    assert.ok(posts[0].key, 'Idempotency-Key');
    assert.deepEqual(await page.evaluate(() => window.__gum.video.facingMode), { ideal: 'environment' });
    assert.equal(await page.inputValue('#codigo'), '', 'data-hub-clear');
    // fragment feedback: vibration + tone + flash
    assert.ok((await page.evaluate(() => window.__vib)).length >= 1);
    assert.ok(await page.evaluate(() => window.__tones) >= 1);
    // continuous: COL-0002 twice in a row counts once (window), then COL-0003
    await page.click('#scan-c');
    await page.waitForSelector('.hub-camera[data-decoder="nativo"]');
    await page.waitForFunction(() => document.querySelector('.hub-camera')?.dataset.reads === '2', null, { timeout: 10_000 });
    assert.ok(await page.isVisible('.hub-camera-torch'), 'lanterna quando a câmera oferece');
    await page.click('.hub-camera-torch');
    await page.waitForFunction(() => window.__torch);
    assert.equal(await page.getAttribute('.hub-camera-torch', 'aria-pressed'), 'true');
    assert.equal(await page.getAttribute('.hub-camera', 'role'), 'dialog');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('.hub-camera'));
    assert.deepEqual(posts.slice(1).map((p) => new URLSearchParams(p.body).get('codigo')), ['COL-0002', 'COL-0003']);
    assert.deepEqual(violations, []);
  } finally { await b.close(); }
});

test('Chromium: sem BarcodeDetector o hub carrega o ZXing de /assets/vendor sob Trusted Types', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const { page, violations } = await appPage(b, { mocks: CAMERA });
    await page.click('#scan');
    await page.waitForSelector('.hub-camera[data-decoder="zxing"]', { timeout: 15_000 });
    assert.equal(await page.evaluate(() => typeof window.ZXing.MultiFormatReader), 'function');
    await page.click('.hub-camera-close');
    assert.deepEqual(violations, []);
  } finally { await b.close(); }
});

test('Chromium: câmera negada e contexto inseguro mostram a mensagem certa', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const { page } = await appPage(b, { mocks: `Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: async () => { throw Object.assign(new Error('x'), { name: 'NotAllowedError' }); } } });` });
    await page.click('#scan');
    await page.waitForSelector('.hub-camera-failed');
    assert.match(await page.textContent('.hub-camera-status'), /Permissão da câmera negada/);
    await page.click('.hub-camera-close');
    const ins = await page.evaluate(() => { Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true }); return true; });
    assert.ok(ins);
    await page.click('#scan');
    await page.waitForSelector('.hub-camera-failed');
    assert.match(await page.textContent('.hub-camera-status'), /HTTPS/);
  } finally { await b.close(); }
});

test('Chromium: leitor tipo teclado — rajada + Enter envia uma vez; repetição descartada; digitação lenta é normal', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const { page, posts } = await appPage(b, { mocks: CAMERA });
    await page.click('#codigo');
    await page.keyboard.type('COL-0010', { delay: 5 });
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('#painel [data-hub-feedback-result]'));
    assert.equal(posts.length, 1);
    assert.equal(await page.getAttribute('#f', 'data-hub-via'), 'leitor');
    await page.click('#codigo');
    await page.keyboard.type('COL-0010', { delay: 5 });
    await page.keyboard.press('Enter');
    await page.waitForTimeout(300);
    assert.equal(posts.length, 1, 'mesmo código dentro de 800 ms: descartado');
    assert.match(await page.textContent('#toast'), /repetida/i);
    await page.waitForTimeout(900);
    await page.fill('#codigo', '');
    await page.keyboard.type('COL-0011', { delay: 120 });
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelectorAll('#painel').length && true);
    await page.waitForTimeout(300);
    assert.equal(posts.length, 2, 'digitação humana também envia (Enter normal)');
    // diagnostics: the reader test field marks OK with the code
    await page.click('#dw');
    await page.keyboard.type('LOC-A01', { delay: 5 });
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('[data-hub-diag-check="wedge"] [data-hub-diag-status]').dataset.diagState === 'ok');
  } finally { await b.close(); }
});

test('Chromium: fila offline no IndexedDB, reenvio em ordem com a mesma chave; erro de validação vai para a lista', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    let n = 0;
    const respond = (p) => {
      n++;
      const code = new URLSearchParams(p.body).get('codigo');
      if (code === 'RUIM') return { status: 422, body: '<div data-hub-feedback-result="error"><strong>Código não encontrado</strong></div>' };
      return { status: 200, body: `<section id="painel"><div data-hub-feedback-result="ok">lido ${code}</div></section>` };
    };
    const { page, posts, ctx } = await appPage(b, { mocks: CAMERA, respond });
    await ctx.setOffline(true);
    for (const c of ['A1', 'RUIM', 'A2']) { await page.fill('#codigo', c); await page.click('#f button'); await page.waitForTimeout(120); }
    await page.waitForFunction(() => document.querySelector('[data-hub-offline-status]').dataset.pending === '3');
    assert.equal(await page.getAttribute('[data-hub-offline-status]', 'data-state'), 'pending');
    assert.equal(posts.length, 0);
    const keys = (await page.evaluate(() => window.HubDevice.queue.all('coletor'))).map((x) => x.key);
    assert.equal(new Set(keys).size, 3);
    await ctx.setOffline(false);
    await page.waitForFunction(() => document.querySelector('[data-hub-offline-status]').dataset.pending === '0', null, { timeout: 15_000 });
    assert.deepEqual(posts.map((p) => new URLSearchParams(p.body).get('codigo')), ['A1', 'RUIM', 'A2']);
    assert.deepEqual(posts.map((p) => p.key), keys, 'mesma Idempotency-Key do envio original');
    await page.waitForSelector('[data-hub-offline-errors]:not([hidden]) li');
    assert.match(await page.textContent('[data-hub-offline-errors]'), /422.*Código não encontrado/);
    assert.equal(await page.getAttribute('[data-hub-offline-status]', 'data-state'), 'error');
    assert.equal(n, 3);
  } finally { await b.close(); }
});

test('Chromium: foto pela câmera vira JPEG ≤ 1600 px no <input type=file> com prévia', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const { page } = await appPage(b, { mocks: CAMERA });
    await page.click('#cap');
    await page.waitForSelector('.hub-camera-shot:not([disabled])');
    await page.waitForTimeout(300);
    await page.click('.hub-camera-shot');
    await page.waitForSelector('#prev img');
    const f = await page.evaluate(() => { const x = document.getElementById('foto').files[0]; return { type: x.type, size: x.size, name: x.name }; });
    assert.equal(f.type, 'image/jpeg');
    assert.ok(f.size > 0 && /\.jpg$/.test(f.name));
    assert.match(await page.getAttribute('#prev img', 'src'), /^blob:/);
  } finally { await b.close(); }
});

test('Chromium: diagnóstico (seguro, vibração), relatório copiado, impressão, instalação escondida sem o evento', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const { page } = await appPage(b, { mocks: CAMERA + 'window.print = () => { window.__printed = 1; };' });
    await page.waitForFunction(() => document.querySelector('[data-hub-diag-check="secure"] [data-hub-diag-status]').dataset.diagState === 'ok');
    await page.click('[data-hub-diag-check="vibrate"] [data-hub-diag-run]');
    await page.waitForFunction(() => document.querySelector('[data-hub-diag-check="vibrate"] [data-hub-diag-status]').dataset.diagState === 'ok');
    await page.click('#copy');
    await page.waitForFunction(() => document.getElementById('copy').dataset.report);
    const rep = await page.getAttribute('#copy', 'data-report');
    assert.match(rep, /Seguro: OK/);
    assert.match(rep, /App: Coletor/);
    await page.click('#print');
    assert.equal(await page.evaluate(() => window.__printed), 1);
    assert.equal(await page.isHidden('[data-hub-install]'), true);
    await page.evaluate(() => { const e = new Event('beforeinstallprompt'); e.prompt = async () => {}; e.userChoice = Promise.resolve({ outcome: 'accepted' }); window.dispatchEvent(e); });
    assert.equal(await page.isVisible('[data-hub-install]'), true);
    await page.click('[data-hub-install]');
    await page.waitForFunction(() => document.querySelector('[data-hub-install]').hidden);
  } finally { await b.close(); }
});

test('service worker: nunca intercepta rotas autenticadas; offline cai na página /offline', () => {
  const sw = fs.readFileSync(path.join(assets, 'hub-sw.js'), 'utf8');
  for (const p of ["'/api/'", "'/_frag/'", "'/mcp'", "'/login'", "'/oauth/'"]) assert.ok(sw.includes(p), p);
  assert.match(sw, /req\.method !== 'GET'\) return/);
  assert.match(sw, /caches\.match\('\/offline'\)/);
});
