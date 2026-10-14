// One-time cleanup of the production state (hub STOPPED): node scripts/prod_cleanup.mjs <copy-of-/data-folder>
// Removes test leftovers (E2E/probes) without touching the audit, the databases or the real accounts.
// Prints only counts and names of test items — never values.
import fs from 'node:fs';
import path from 'node:path';

const dir = process.argv[2];
if (!dir) throw new Error('uso: node prod_cleanup.mjs <pasta>');
const rd = (f) => JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
const wr = (f, d) => fs.writeFileSync(path.join(dir, f), JSON.stringify(d));
const log = (...a) => console.log(...a);

const TEST_TOKEN = /^(f7a-|f4b-|mcp-client-check|step-up mcp|e2e)/i;
const TEST_ENV = /^(E2E_|HUB_DEMO_E2E_HOSTILE$)/;

// auth.json: test tokens
const auth = rd('auth.json');
const before = auth.tokens.length;
auth.tokens = auth.tokens.filter((t) => !TEST_TOKEN.test(t.name || ''));
log(`tokens removidos: ${before - auth.tokens.length} (restam ${auth.tokens.length})`);
wr('auth.json', auth);

// admin.json: variables, pending restart, incidents and test flags
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

// ops.json: test script approvals
const ops = rd('ops.json');
const sa = ops.script_approvals.length;
ops.script_approvals = ops.script_approvals.filter((a) => !/(^|\/)e2e-/.test(a.path));
log(`aprovações de script removidas: ${sa - ops.script_approvals.length}`);
wr('ops.json', ops);

// inbox.json: archives (bit 2) for all users the items of the removed app (contratos) and of the MCP test clients
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
