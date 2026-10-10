// Regressão encontrada pelo E2E da onda 6: o modal "Nova variável" (admin.js) não
// enviava nada — `$(sel).map` (querySelector) lançava TypeError ao ler os vínculos.
// Executa o admin.js real num DOM (jsdom de demo-apps/tools) e confere o POST.
//   node --test platform/js-tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const platform = path.resolve(here, '..');
const require = createRequire(import.meta.url);
const jsdomPath = path.join(platform, 'demo-apps/tools/node_modules/jsdom');
const hasJsdom = fs.existsSync(jsdomPath);
const adminJs = fs.readFileSync(path.join(platform, 'assets/web/admin.js'), 'utf8');
const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));

test('admin.js: "Nova variável" envia nome, tipo, valor e os vínculos marcados', { skip: !hasJsdom && 'jsdom ausente' }, async () => {
  const { JSDOM } = require(jsdomPath);
  const meta = { apps: [{ id: 'ativos', name: 'Gestão de Ativos', short: 'Ativos' }, { id: 'fornecedores', name: 'Rede de Fornecedores', short: 'Fornecedores' }], permissions: [] };
  const html = `<!DOCTYPE html><html><body><div id="admin-meta" hidden data-admin-meta='${JSON.stringify(meta)}'></div><button type="button" data-admin-add="env">Nova variável</button><div id="toast"></div></body></html>`;
  // location.reload() do jsdom só registra "not implemented" no console virtual (silenciado).
  const { VirtualConsole } = require(jsdomPath);
  const dom = new JSDOM(html, { url: 'http://hub.test/admin/env', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: new VirtualConsole() });
  const w = dom.window;
  const posts = [];
  w.fetch = async (url, init) => {
    posts.push({ url: String(url), method: init.method, body: JSON.parse(init.body) });
    return { ok: true, status: 201, text: async () => '{}' };
  };
  const errors = [];
  w.addEventListener('error', (e) => errors.push(e.message));
  w.eval(adminJs);
  w.document.querySelector('[data-admin-add="env"]').click();
  const modal = w.document.querySelector('.overlay .modal');
  assert.ok(modal, 'modal aberto');
  modal.querySelector('[data-field="name"]').value = 'MINHA_FLAG';
  modal.querySelector('[data-field="value"]').value = 'on';
  modal.querySelector('[data-field-binding][data-app="fornecedores"][data-env="staging"]').checked = true;
  modal.querySelector('[data-field-binding][data-app="ativos"][data-env="producao"]').checked = true;
  modal.querySelector('form').dispatchEvent(new w.Event('submit', { cancelable: true, bubbles: true }));
  await tick(20);
  assert.deepEqual(errors, []);
  assert.equal(posts.length, 1, 'um POST');
  assert.equal(posts[0].url, '/api/admin/env');
  assert.equal(posts[0].method, 'POST');
  assert.equal(posts[0].body.name, 'MINHA_FLAG');
  assert.equal(posts[0].body.kind, 'publica');
  assert.equal(posts[0].body.inject_as, null);
  assert.deepEqual(posts[0].body.bindings, [{ app: 'ativos', env: 'producao' }, { app: 'fornecedores', env: 'staging' }]);
  assert.equal(w.document.querySelector('.overlay'), null, 'modal fechado após salvar');
});
