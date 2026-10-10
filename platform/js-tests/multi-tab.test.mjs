// Testes das abas múltiplas (onda 5), sem navegador:
//   node --test platform/js-tests/
// - logshare.js: uma conexão SSE para N abas (BroadcastChannel real do node + Web Locks
//   simulado), failover quando a aba líder fecha, filtros locais;
// - hub.js: política do loader (deep link, recarregar, sessão, cache por versão) e o
//   estado "reiniciando" com novas tentativas — executando o hub.js de verdade num DOM
//   (jsdom de demo-apps/tools; instale com `npm ci --prefix platform/demo-apps/tools`).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const platform = path.resolve(here, '..');
const require = createRequire(import.meta.url);
const { createLogShare, matches } = require(path.join(platform, 'assets/web/logshare.js'));
const Policy = require(path.join(platform, 'assets/shell/hub.js'));
const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));

/** Web Locks simulado: trava exclusiva por nome, fila FIFO, cancelável por AbortSignal. */
function fakeLocks() {
  const queues = new Map();
  const held = new Set();
  function pump(name) {
    if (held.has(name)) return;
    const q = queues.get(name) || [];
    const next = q.shift();
    if (!next) return;
    held.add(name);
    Promise.resolve(next.cb({ name })).finally(() => { held.delete(name); pump(name); }).then(next.resolve, next.reject);
  }
  return {
    request(name, opts, cb) {
      return new Promise((resolve, reject) => {
        const entry = { cb, resolve, reject };
        if (!queues.has(name)) queues.set(name, []);
        queues.get(name).push(entry);
        opts?.signal?.addEventListener('abort', () => {
          const q = queues.get(name);
          const i = q.indexOf(entry);
          if (i >= 0) { q.splice(i, 1); reject(new Error('AbortError')); }
        });
        pump(name);
      });
    },
  };
}

/** EventSource simulado: registra as conexões abertas. */
function sources() {
  const open = [];
  return {
    open,
    make() {
      const listeners = {};
      const s = {
        closed: false,
        addEventListener: (t, f) => { (listeners[t] ||= []).push(f); },
        close() { s.closed = true; open.splice(open.indexOf(s), 1); },
        emit(t, data) { (listeners[t] || []).forEach((f) => f({ data })); },
      };
      open.push(s);
      return s;
    },
  };
}

function tab(locks, src, name) {
  const got = [];
  const share = createLogShare({
    name,
    locks,
    channel: new BroadcastChannel(name),
    openSource: () => src.make(),
    onLine: (l) => got.push(l.message),
  });
  return { share, got };
}

test('uma única conexão SSE para três abas, cada linha uma vez em cada aba', async () => {
  const locks = fakeLocks();
  const src = sources();
  const name = `hub-logs-t1-${Date.now()}`;
  const tabs = [tab(locks, src, name), tab(locks, src, name), tab(locks, src, name)];
  tabs.forEach((t) => t.share.start());
  await tick();
  assert.equal(src.open.length, 1, 'só a líder conecta');
  assert.deepEqual(tabs.map((t) => t.share.role()), ['líder', 'seguidora', 'seguidora']);
  src.open[0].emit('log', JSON.stringify({ message: 'a', app: 'ativos', user: 'ana' }));
  await tick();
  assert.deepEqual(tabs.map((t) => t.got), [['a'], ['a'], ['a']]);
  tabs.forEach((t) => t.share.stop());
});

test('failover: a aba líder fecha e a próxima assume, sem conexão duplicada', async () => {
  const locks = fakeLocks();
  const src = sources();
  const name = `hub-logs-t2-${Date.now()}`;
  const [a, b, c] = [tab(locks, src, name), tab(locks, src, name), tab(locks, src, name)];
  [a, b, c].forEach((t) => t.share.start());
  await tick();
  const first = src.open[0];
  a.share.stop(); // aba fechada/pausada: libera a trava
  await tick();
  assert.ok(first.closed, 'conexão da antiga líder encerrada');
  assert.equal(src.open.length, 1, 'uma conexão depois do failover');
  assert.equal(b.share.role(), 'líder');
  assert.equal(c.share.role(), 'seguidora');
  src.open[0].emit('log', JSON.stringify({ message: 'depois', app: 'ativos', user: 'x' }));
  await tick();
  assert.deepEqual(b.got, ['depois']);
  assert.deepEqual(c.got, ['depois']);
  assert.deepEqual(a.got, [], 'aba parada não recebe');
  b.share.stop();
  await tick();
  assert.equal(c.share.role(), 'líder');
  assert.equal(src.open.length, 1);
  c.share.stop();
  await tick();
  assert.equal(src.open.length, 0);
});

test('sem Web Locks/BroadcastChannel cada aba abre a própria conexão', () => {
  const src = sources();
  const s = createLogShare({ locks: null, channel: null, openSource: () => src.make(), onLine() {} });
  s.start();
  assert.equal(src.open.length, 1);
  assert.equal(s.role(), 'líder');
  s.stop();
});

test('filtro local igual ao do servidor (app e usuário por substring)', () => {
  const l = { app: 'ativos', user: 'Ana.Souza@demo', message: 'x' };
  assert.ok(matches(l, { app: 'all', user: '' }));
  assert.ok(matches(l, { app: 'ativos', user: 'ana' }));
  assert.ok(!matches(l, { app: 'servicos', user: '' }));
  assert.ok(!matches(l, { app: 'all', user: 'bruno' }));
});

test('política do carregamento: deep link, novas tentativas e erros', () => {
  assert.ok(Policy.deepLink('/ativos/assets', '', 'ativos'));
  assert.ok(Policy.deepLink('/ativos', '?q=1', 'ativos'));
  assert.ok(!Policy.deepLink('/ativos', '', 'ativos'));
  assert.ok(Policy.deepLink('/apps/ativos/assets', '', 'ativos'));
  assert.ok(!Policy.deepLink('/apps/ativos', '', 'ativos'));
  assert.deepEqual([0, 1, 2, 3, 4].map(Policy.retryDelay), [2000, 4000, 8000, 15000, 15000]);
  assert.ok(Policy.retriable(502, '') && Policy.retriable(0, '') && Policy.retriable(503, ''));
  assert.ok(Policy.retriable(404, '<section data-hub-unavailable="">'));
  assert.ok(!Policy.retriable(404, '<section>não existe</section>'));
});

// ---- hub.js real num DOM (jsdom) -----------------------------------------------------
const jsdomPath = path.join(platform, 'demo-apps/tools/node_modules/jsdom');
const hasJsdom = fs.existsSync(jsdomPath);
const hubJs = fs.readFileSync(path.join(platform, 'assets/shell/hub.js'), 'utf8');
// Onda 12 (2.1): o hub.js só insere HTML de app pelo sanitizador (hub-safe.js).
const safeJs = fs.readFileSync(path.join(platform, 'assets/shell/hub-safe.js'), 'utf8');

/** Página de app com o hub.js; `respond(url)` → {status, body} simula o gateway. */
function appPage({ url, respond, local = {}, session = {}, navType = 'navigate' }) {
  const { JSDOM } = require(jsdomPath);
  const html = `<!DOCTYPE html><html><head></head><body><main id="app-root"><div class="workspace"><section class="workspace-content"><div class="app-loading"><strong class="loader-percentage">0%</strong><span class="loader-fill"></span></div></section></div></main></body></html>`;
  const calls = [];
  const dom = new JSDOM(html, {
    url,
    runScripts: 'outside-only',
    pretendToBeVisual: true,
  });
  const w = dom.window;
  for (const [k, v] of Object.entries(local)) w.localStorage.setItem(k, v);
  for (const [k, v] of Object.entries(session)) w.sessionStorage.setItem(k, v);
  w.performance.getEntriesByType = () => [{ type: navType }];
  w.fetch = async (u) => {
    calls.push(String(u));
    const r = respond(String(u));
    return { ok: r.status >= 200 && r.status < 300, status: r.status, headers: { get: (k) => (r.headers || {})[k.toLowerCase()] ?? null }, text: async () => r.body };
  };
  // Assets do app (css/js) carregam na hora.
  const append = w.Element.prototype.append;
  w.Element.prototype.append = function (...nodes) {
    append.apply(this, nodes);
    nodes.forEach((n) => { if (n.tagName === 'LINK' || n.tagName === 'SCRIPT') setTimeout(() => n.onload && n.onload(), 0); });
  };
  const s = w.document.createElement('script');
  s.dataset.hubApp = 'ativos';
  s.dataset.hubSection = 'dashboard';
  s.dataset.hubFrag = '/_frag';
  s.dataset.hubVersion = '2.4.1';
  Object.defineProperty(w.document, 'currentScript', { get: () => s, configurable: true });
  w.eval(safeJs);
  w.eval(hubJs);
  return { w, calls };
}

test('hub.js: abrir o app reproduz o mockup — carregador 0→100% em 3,5 s, skeleton 0,9 s, conteúdo', { skip: !hasJsdom && 'jsdom ausente' }, async () => {
  const { w } = appPage({
    url: 'http://hub.test/ativos/assets',
    respond: () => ({ status: 200, body: '<div class="workspace" id="workspace"><aside class="app-sidebar"></aside><section class="workspace-content"><p id="ok">Conteúdo</p></section></div>' }),
  });
  await tick(1500);
  assert.ok(w.document.querySelector('.app-loading'), 'carregador ainda na tela (tempo fixo)');
  assert.ok(!w.document.getElementById('ok'));
  await tick(2300);
  assert.ok(!w.document.querySelector('.app-loading'), 'carregador some aos 3,5 s');
  assert.ok(w.document.querySelector('.component-loading .skeleton-page-heading'), 'skeleton de componentes');
  assert.match(w.document.querySelector('.component-loading-caption').textContent, /Carregando ativos/);
  await tick(1000);
  assert.ok(w.document.getElementById('ok'), 'conteúdo depois do skeleton');
  assert.equal(w.sessionStorage.getItem('hub:opened:ativos'), '1');
  assert.equal(w.localStorage.getItem('hub:assets:ativos'), '2.4.1', 'versão dos assets lembrada');
  w.close();
});

test('hub.js: app reiniciando mostra estado amigável e volta sozinho', { skip: !hasJsdom && 'jsdom ausente' }, async () => {
  let down = true;
  const { w, calls } = appPage({
    url: 'http://hub.test/ativos/assets',
    local: { 'hub:assets:ativos': '2.4.1' },
    respond: (u) => {
      if (u.includes('_skeleton')) return { status: 404, body: '' };
      return down
        ? { status: 502, body: '<section class="empty-state" data-hub-unavailable=""><h1>Aplicação indisponível</h1></section>' }
        : { status: 200, body: '<div class="workspace" id="workspace"><p id="ok">De volta</p></div>' };
    },
  });
  await tick(50);
  const retry = w.document.querySelector('[data-hub-retry]');
  assert.ok(retry, 'estado "reiniciando" dentro do shell');
  assert.match(retry.textContent, /Aplicação indisponível/);
  assert.match(retry.textContent, /Tentando de novo em 2 s/);
  down = false;
  await tick(2300);
  assert.ok(w.document.getElementById('ok'), 'fragmento recarregado quando o app voltou');
  assert.ok(calls.filter((c) => c.startsWith('/_frag/ativos/assets')).length >= 2);
  w.close();
});

test('líder com EventSource fechado de vez (401/sessão vencida): reconecta com espera e, sem sucesso, devolve a liderança', async () => {
  const locks = fakeLocks();
  const src = sources();
  const name = `hub-logs-t5-${Date.now()}`;
  const delays = [];
  const mk = (sessionOk) => {
    const got = [];
    const statuses = [];
    const share = createLogShare({
      name,
      locks,
      channel: new BroadcastChannel(name),
      openSource: () => { const s = src.make(); s.sessionOk = sessionOk; return s; },
      onLine: (l) => got.push(l.message),
      onStatus: (s) => statuses.push(s),
      setTimeout: (f, ms) => { delays.push(ms); return setTimeout(f, 0); },
      maxFailures: 3,
    });
    return { share, got, statuses };
  };
  const a = mk(false);
  const b = mk(true);
  a.share.start();
  await tick();
  b.share.start();
  await tick();
  assert.equal(a.share.role(), 'líder');
  assert.equal(b.statuses[0].connected, false, 'seguidora não se diz conectada antes de uma líder confirmar');
  // A conexão da líder fecha de vez três vezes seguidas.
  for (let i = 0; i < 3; i++) {
    const s = src.open.find((x) => !x.closed && !x.sessionOk);
    assert.ok(s, `conexão da líder aberta (tentativa ${i + 1})`);
    s.readyState = 2;
    s.onerror();
    await tick(20);
  }
  assert.deepEqual(delays, [1000, 2000], 'espera crescente entre as tentativas');
  assert.equal(b.share.role(), 'líder', 'outra aba assume depois das falhas');
  assert.equal(a.share.role(), 'seguidora');
  const live = src.open.filter((x) => !x.closed);
  assert.equal(live.length, 1, 'uma conexão só');
  live[0].emit('ready', '');
  live[0].emit('log', JSON.stringify({ message: 'ok', app: 'ativos', user: 'x' }));
  await tick();
  assert.deepEqual(a.got, ['ok'], 'a antiga líder recebe como seguidora');
  assert.equal(a.statuses.at(-1).connected, true);
  a.share.stop();
  b.share.stop();
});

// ---- 429 (fila de operações pesadas): novas tentativas com Retry-After + jitter --------
test('política da fila (429): honra Retry-After com jitter, limites e rótulo do app', () => {
  assert.equal(Policy.queued(429), true);
  assert.equal(Policy.queued(502), false);
  assert.equal(Policy.queueDelay('10', 0, () => 0), 10000);
  assert.equal(Policy.queueDelay('10', 0, () => 1), 15000, 'jitter de até 50%');
  assert.equal(Policy.queueDelay('0.2', 0, () => 0), 1000, 'mínimo 1 s');
  assert.equal(Policy.queueDelay('120', 0, () => 0), 30000, 'máximo 30 s');
  assert.equal(Policy.queueDelay(null, 0, () => 0), 2000, 'sem Retry-After: espera crescente');
  assert.equal(Policy.queueDelay(null, 2, () => 0), 8000);
  const now = Date.parse('2026-09-27T12:00:00Z');
  assert.equal(Policy.queueDelay('Sun, 27 Sep 2026 12:00:05 GMT', 0, () => 0, now), 5000, 'Retry-After como data HTTP');
  const seen = new Set(Array.from({ length: 20 }, () => Policy.queueDelay('10', 0)));
  assert.ok(seen.size > 1, 'jitter real (abas não voltam juntas)');
  for (const d of seen) assert.ok(d >= 10000 && d <= 15000, String(d));
  assert.ok(Policy.QUEUE_MAX >= 3 && Policy.QUEUE_MAX <= 10);
  assert.equal(Policy.queuedLabel('<div data-hub-queued="Relatório em fila…">'), 'Relatório em fila…');
  assert.equal(Policy.queuedLabel('<div>'), 'Operação em fila…');
});

test('hub.js: relatório pesado com 429 mostra "Relatório em fila…" e entra sozinho quando há vaga', { skip: !hasJsdom && 'jsdom ausente' }, async () => {
  let busy = 1;
  const { w, calls } = appPage({
    url: 'http://hub.test/ativos/reports?from=2026-01-01',
    local: { 'hub:assets:ativos': '2.4.1' },
    respond: (u) => {
      if (u.includes('_skeleton') || u.includes('/assets/')) return { status: 404, body: '' };
      if (busy-- > 0) {
        return {
          status: 429,
          headers: { 'retry-after': '1' },
          body: '<div class="hub-notice hub-notice-warn" role="alert" data-hub-queued="Relatório em fila…"><strong>Muitas operações pesadas em andamento</strong></div>',
        };
      }
      return { status: 200, body: '<div class="workspace" id="workspace"><p id="ok">Relatório</p></div>' };
    },
  });
  await tick(60);
  const q = w.document.querySelector('[data-hub-queued-view]');
  assert.ok(q, 'estado "em fila", não um erro');
  assert.match(q.textContent, /Relatório em fila…/);
  assert.match(q.textContent, /tentativa 1 de \d/);
  assert.ok(!w.document.querySelector('.hub-app-error'), 'sem tela de erro');
  await tick(1700);
  assert.ok(w.document.getElementById('ok'), 'relatório carregado na nova tentativa');
  assert.equal(calls.filter((c) => c.startsWith('/_frag/ativos/reports')).length, 2);
  w.close();
});
