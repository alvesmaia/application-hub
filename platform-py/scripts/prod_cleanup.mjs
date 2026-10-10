// Limpeza única do estado de produção (hub PARADO): node scripts/prod_cleanup.mjs <pasta-copia-de-/data>
// Remove lixo de teste (E2E/probes) sem tocar na auditoria, nos bancos nem nas contas reais.
// Imprime só contagens e nomes de itens de teste — nunca valores.
import fs from 'node:fs';
import path from 'node:path';

const dir = process.argv[2];
if (!dir) throw new Error('uso: node prod_cleanup.mjs <pasta>');
const rd = (f) => JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
const wr = (f, d) => fs.writeFileSync(path.join(dir, f), JSON.stringify(d));
const log = (...a) => console.log(...a);

const TEST_TOKEN = /^(f7a-|f4b-|mcp-client-check|step-up mcp|e2e)/i;
const TEST_ENV = /^(E2E_|HUB_DEMO_E2E_HOSTILE$)/;

// auth.json: tokens de teste
const auth = rd('auth.json');
const before = auth.tokens.length;
auth.tokens = auth.tokens.filter((t) => !TEST_TOKEN.test(t.name || ''));
log(`tokens removidos: ${before - auth.tokens.length} (restam ${auth.tokens.length})`);
wr('auth.json', auth);

// admin.json: variáveis, reinício pendente, incidentes e flags de teste
const admin = rd('admin.json');
const envOut = admin.env.filter((e) => TEST_ENV.test(e.name)).map((e) => e.name);
admin.env = admin.env.filter((e) => !TEST_ENV.test(e.name));
log(`variáveis removidas: ${envOut.join(', ')}`);
log(`reinícios pendentes descartados: ${Object.keys(admin.pending_restart || {}).join(', ') || '-'}`);
admin.pending_restart = {};
const inc = admin.incidents.length;
admin.incidents = admin.incidents.filter((i) => !/e2e/i.test(i.title || i.name || ''));
log(`incidentes removidos: ${inc - admin.incidents.length}`);
const fl = admin.flags.length;
admin.flags = admin.flags.filter((f) => !/e2e|teste|probe/i.test(`${f.key} ${f.description || ''}`));
log(`flags removidas: ${fl - admin.flags.length}`);
wr('admin.json', admin);

// ops.json: aprovações de script de teste
const ops = rd('ops.json');
const sa = ops.script_approvals.length;
ops.script_approvals = ops.script_approvals.filter((a) => !/(^|\/)e2e-/.test(a.path));
log(`aprovações de script removidas: ${sa - ops.script_approvals.length}`);
wr('ops.json', ops);

// inbox.json: arquiva (bit 2) para todos os usuários os itens do app removido (contratos) e dos clientes de teste do MCP
const inbox = rd('inbox.json');
const uids = auth.users.map((u) => u.id);
let n = 0;
for (const it of inbox.items) {
  const test = it.app === 'contratos' || /hub-mcp-client-check|Verificação MCP \(SDK oficial\)/.test(`${it.title} ${it.body}`);
  if (!test) continue;
  n++;
  for (const uid of uids) {
    const mine = (inbox.states[uid] ||= {});
    mine[String(it.id)] = (mine[String(it.id)] || 0) | 2;
  }
}
log(`itens do Inbox arquivados: ${n}`);
wr('inbox.json', inbox);
