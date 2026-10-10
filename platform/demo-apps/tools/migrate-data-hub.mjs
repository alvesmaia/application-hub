// Onda 12 (2.1): migra os fragmentos estáticos dos apps de demonstração do JS próprio
// (interact.js/chart.js) para os atributos declarativos data-hub-* do hub-kit.js.
import fs from 'node:fs';
import path from 'node:path';

export const MODAL_ASSET = '<template id="modal-asset" data-hub-modal-labelledby="modal-title"><div class="modal-header"><h2 id="modal-title">Cadastrar ativo</h2><button type="button" class="modal-close" aria-label="Fechar" data-hub-close="">×</button></div><p class="modal-copy">Adicione um equipamento ao inventário da organização.</p><form data-hub-dismiss="" data-hub-toast="Ativo cadastrado com sucesso."><div class="form-field"><label>Nome do ativo</label><input placeholder="Digite aqui..." required=""></div><div class="form-field"><label>Categoria</label><select><option>Selecione uma opção</option><option>Tecnologia</option><option>Equipamentos</option><option>Software</option><option>Operações</option></select></div><div class="form-field"><label>Responsável</label><textarea placeholder="Adicione detalhes..."></textarea></div><div class="modal-actions"><button type="button" class="secondary-button modal-cancel" data-hub-close="">Cancelar</button><button type="submit" class="primary-button">Enviar solicitação</button></div></form></template>';

const escAttr = (s) => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

export function migrate(html) {
  let out = html
    .replace(/\sdata-toast="/g, ' data-hub-toast="')
    .replace(/\sdata-mobile-menu=""/g, ' data-hub-menu="open"')
    .replace(/\sdata-mobile-close=""/g, ' data-hub-menu="close"')
    .replace(/\sdata-table-search=""/g, ' data-hub-filter=""')
    .replace(/\sdata-kb-search=""/g, ' data-hub-filter="#kb-page" data-hub-filter-empty="#kb-empty"')
    .replace('<div class="app3-page app3-knowledge-page">', '<div class="app3-page app3-knowledge-page" id="kb-page">')
    .replace('<p class="app3-kb-empty" hidden="">', '<p class="app3-kb-empty" id="kb-empty" hidden="">')
    .replace(/\sdata-kb-item=""/g, ' data-hub-filter-item=""')
    .replace(/\sdata-kb-searchable="/g, ' data-hub-filter-text="');
  let modal = false;
  out = out.replace(/\sdata-modal="asset"/g, () => { modal = true; return ' data-hub-modal="#modal-asset"'; });
  out = out.replace(/<script type="application\/json" data-hub-charts>([\s\S]*?)<\/script>/g, (_, json) => {
    const raw = json.replace(/\\u003c/g, '<');
    JSON.parse(raw);
    return `<div hidden="" data-hub-charts="${escAttr(raw)}"></div>`;
  });
  if (modal && !out.includes('id="modal-asset"')) out = out.replace(/\n?$/, `${MODAL_ASSET}\n`);
  return out;
}

// Uso direto: node migrate-data-hub.mjs ../fragments
if (process.argv[2] && (process.argv[1] || '').endsWith('migrate-data-hub.mjs')) {
  const root = process.argv[2];
  for (const app of fs.readdirSync(root)) {
    const dir = path.join(root, app);
    for (const f of fs.readdirSync(dir).filter((n) => n.endsWith('.html'))) {
      const p = path.join(dir, f);
      const a = fs.readFileSync(p, 'utf8');
      const b = migrate(a);
      if (a !== b) { fs.writeFileSync(p, b); console.log('migrado', p); }
    }
  }
}
