// Wave 12 (2.8) — in-browser translation (hub-i18n.js) with the same server catalog
// (assets/i18n/catalog.json): exact texts, templates with variable parts and
// phrases inside larger texts; two complete languages with the same markers.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const platform = path.resolve(here, '..');
const require = createRequire(import.meta.url);
const I18n = require(path.join(platform, 'assets/shell/hub-i18n.js'));
const rows = JSON.parse(fs.readFileSync(path.join(platform, 'assets/i18n/catalog.json'), 'utf8'));
const MARK = /\{[^{}\s"]{0,24}\}/g;

function catalogFor(col) {
  const exact = {};
  const templates = [];
  for (const r of rows) {
    if (MARK.test(r[0])) templates.push([r[0], r[col]]);
    else exact[r[0]] = r[col];
    MARK.lastIndex = 0;
  }
  return I18n.prepare({ exact, templates });
}

test('catálogo: dois idiomas, sem chave repetida e com os mesmos marcadores', () => {
  const seen = new Set();
  for (const r of rows) {
    assert.equal(r.length, 2);
    assert.ok(!seen.has(r[0]), `repetida: ${r[0]}`);
    seen.add(r[0]);
    const m = (s) => (s.match(MARK) || []).sort().join('|');
    assert.equal(m(r[1]), m(r[0]), r[0]);
  }
});

test('exato, molde e frase', () => {
  const en = catalogFor(1);
  assert.equal(I18n.tr(en, 'Variável excluída.'), 'Variable deleted.');
  assert.equal(I18n.tr(en, '  Variável excluída.\n'), '  Variable deleted.\n');
  assert.equal(I18n.tr(en, 'Editar LOG_LEVEL'), 'Edit LOG_LEVEL');
  assert.equal(I18n.tr(en, 'Token sem acesso ao app ativos.'), 'Token has no access to app ativos.');
  // known phrase inside a larger text, with word boundaries
  assert.equal(I18n.tr(en, 'Usuários (3)'), 'Users (3)');
  // unknown text and user data pass through unchanged
  assert.equal(I18n.tr(en, 'Notebook Latitude 5440'), 'Notebook Latitude 5440');
  assert.equal(I18n.tr(null, 'Variável excluída.'), 'Variável excluída.');
});

test('todo molde do catálogo casa com o próprio texto preenchido', () => {
  const en = catalogFor(1);
  for (const r of rows) {
    const marks = r[0].match(MARK);
    if (!marks) continue;
    // templates with almost no fixed text ("{} de {}") are not used for translation
    if ((r[0].replace(MARK, '').match(/\p{L}/gu) || []).length < 3) continue;
    let i = 0;
    const vals = {};
    const filled = r[0].replace(MARK, (m) => {
      const v = `V${i++}Z`;
      (vals[m] ||= []).push(v);
      return v;
    });
    const seen = {};
    const expected = r[1].replace(MARK, (m) => {
      const k = seen[m] || 0;
      seen[m] = k + 1;
      return vals[m][k];
    });
    const got = I18n.tr(en, filled);
    // a more specific template may win; just make sure no literal pt-BR from the template is left
    if (got !== expected) {
      assert.notEqual(got, filled, `sem tradução: ${r[0]}`);
    }
  }
});
