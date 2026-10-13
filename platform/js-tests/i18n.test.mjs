// Onda 12 (2.8) — tradução no navegador (hub-i18n.js) com o mesmo catálogo do servidor
// (assets/i18n/catalog.json): textos exatos, moldes com partes variáveis e
// frases dentro de textos maiores; dois idiomas completos e com os mesmos marcadores.
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
  // frase conhecida dentro de um texto maior, com limite de palavra
  assert.equal(I18n.tr(en, 'Usuários (3)'), 'Users (3)');
  // desconhecido e dado do usuário passam intactos
  assert.equal(I18n.tr(en, 'Notebook Latitude 5440'), 'Notebook Latitude 5440');
  assert.equal(I18n.tr(null, 'Variável excluída.'), 'Variável excluída.');
});

test('todo molde do catálogo casa com o próprio texto preenchido', () => {
  const en = catalogFor(1);
  for (const r of rows) {
    const marks = r[0].match(MARK);
    if (!marks) continue;
    // moldes quase sem texto fixo ("{} de {}") não são usados na tradução
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
    // outro molde mais específico pode ganhar; basta não sobrar pt-BR literal do molde
    if (got !== expected) {
      assert.notEqual(got, filled, `sem tradução: ${r[0]}`);
    }
  }
});
