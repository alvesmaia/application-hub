// Wave 9, no browser: pure logic of the ⌘K palette and the tour (hub-ux.js), reordering of
// home-page widgets (ux.js), the live Inbox reusing the connection shared between
// tabs (logshare.js with the `inbox` event) and the dark-theme generator (contrast).
//   node --test platform/js-tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const here = path.dirname(fileURLToPath(import.meta.url));
const platform = path.resolve(here, '..');
const require = createRequire(import.meta.url);
const Ux = require(path.join(platform, 'assets/shell/hub-ux.js'));
const { moveIn } = require(path.join(platform, 'assets/web/ux.js'));
const { createLogShare } = require(path.join(platform, 'assets/web/logshare.js'));

test('tour: passos na ordem canônica e retomada limitada', () => {
  assert.deepEqual(Ux.tourSteps(['admin', 'home', 'inbox']), ['home', 'inbox', 'admin']);
  assert.deepEqual(Ux.tourSteps(['search', 'home']), ['home']); // no search in the hub any more
  assert.equal(Ux.score, undefined); // the search palette is gone
  assert.equal(Ux.resumeAt('step:3', 5), 3);
  assert.equal(Ux.resumeAt('step:9', 5), 4);
  assert.equal(Ux.resumeAt('', 5), 0);
  assert.equal(Ux.resumeAt('done', 0), 0);
});

test('Início: mover widget (teclado) respeita os limites', () => {
  const o = ['a', 'b', 'c'];
  assert.deepEqual(moveIn(o, 'b', -1), ['b', 'a', 'c']);
  assert.deepEqual(moveIn(o, 'b', 1), ['a', 'c', 'b']);
  assert.deepEqual(moveIn(o, 'a', -1), o);
  assert.deepEqual(moveIn(o, 'z', 1), o);
});

test('Inbox ao vivo: uma conexão SSE (evento inbox) compartilhada entre abas', async () => {
  const opened = [];
  const src = () => {
    const l = {};
    const s = { readyState: 1, addEventListener: (t, f) => { (l[t] ||= []).push(f); }, close() { s.readyState = 2; }, emit: (t, d) => (l[t] || []).forEach((f) => f({ data: JSON.stringify(d) })) };
    opened.push(s);
    return s;
  };
  const got = [];
  const share = createLogShare({ name: 'hub-inbox', event: 'inbox', openSource: src, onLine: (d) => got.push(d), channel: null, locks: null });
  share.start();
  assert.equal(opened.length, 1);
  opened[0].emit('log', { unread: 9 });
  opened[0].emit('inbox', { unread: 3 });
  assert.deepEqual(got, [{ unread: 3 }], 'só o evento pedido');
  share.stop();
});

test('tema escuro: o gerador está em dia e o texto escuro vira claro', () => {
  execFileSync(process.execPath, [path.join(platform, 'scripts/gen-dark-theme.mjs'), '--check'], { stdio: 'pipe' });
});
