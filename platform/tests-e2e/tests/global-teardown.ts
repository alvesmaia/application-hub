import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import { request } from '@playwright/test';
import { CP, HUB, stateFile } from './helpers';

/** Mark of the data created by the suite (names "… E2E …", "e2e-…", e-mails `e2e-…@`). */
const E2E = /(^|[\s-])e2e([\s-]|$)|^e2e-/i;

/**
 * Removes from the demo what the tests created (P2-23): API and personal tokens, AI clients
 * (OAuth) and users with the E2E mark. Only deletes what matches the mark; failures do not
 * bring the suite down (they are recorded).
 */
export async function cleanupE2eData() {
  if (!fs.existsSync(stateFile('admin'))) return;
  const api = await request.newContext({ baseURL: HUB, storageState: stateFile('admin'), extraHTTPHeaders: { origin: HUB } });
  const done: string[] = [];
  try {
    const toks = await api.get('/api/admin/tokens');
    if (toks.ok()) {
      for (const t of (await toks.json()) as { id: string; name: string; revoked_at?: number | null }[]) {
        if (!E2E.test(t.name)) continue;
        if (!t.revoked_at) await api.post(`/api/admin/tokens/${t.id}/revoke`, { data: {} });
        const r = await api.delete(`/api/admin/tokens/${t.id}`, { data: {} });
        if (r.ok()) done.push(`token ${t.name}`);
      }
    }
    const page = await api.get('/admin/tokens');
    if (page.ok()) {
      const html = await page.text();
      for (const m of html.matchAll(/<tr data-oauth-client-row="">([\s\S]*?)<\/tr>/g)) {
        const name = (m[1].match(/<strong>([^<]*)<\/strong>/) || [])[1] || '';
        const id = (m[1].match(/name="client" value="([^"]+)"/) || [])[1];
        if (!id || !E2E.test(name)) continue;
        const r = await api.post('/admin/oauth/remove-client', { form: { client: id }, maxRedirects: 0 });
        if (r.status() < 400) done.push(`cliente de IA ${name}`);
      }
    }
    const users = await api.get('/api/admin/users');
    if (users.ok()) {
      for (const u of (await users.json()) as { id: string; email: string; name: string; active: boolean }[]) {
        if (!/^e2e-/i.test(u.email) && !E2E.test(u.name)) continue;
        if (u.active) await api.put(`/api/admin/users/${u.id}`, { data: { active: false } });
        const r = await api.delete(`/api/admin/users/${u.id}`, { data: {} });
        if (r.ok()) done.push(`usuário ${u.email}`);
      }
    }
  } catch (e) {
    console.log(`[global-teardown] limpeza dos dados E2E incompleta: ${e}`);
  } finally {
    await api.dispose();
  }
  if (done.length) console.log(`[global-teardown] dados E2E removidos: ${done.join(', ')}`);
}

/**
 * Staging provisioning/blue-green (06-ops, 07-staging) brings up containers and networks
 * through the hub's runtime (`hub-app-<id>__staging`, `hub-deps-<id>__staging`, …) outside of
 * `docker compose` — so `docker compose down` does not remove them and they are left orphaned after the
 * suite (e.g. `hub-app-fornecedores__staging` + `hub-deps-fornecedores__staging`).
 * Cleans them here by the `__staging` suffix (never containers outside the project
 * nor any container/network without that suffix — we do not filter by image nor "kill by
 * name" generically).
 */
export default async function globalTeardown() {
  await cleanupE2eData();
  // Python hub (app-hub-*): the hub itself manages the `__staging` ones (supervisor); do not remove.
  if (CP.startsWith('app-hub-')) return;
  const docker = (args: string[]) => {
    try {
      return execFileSync('docker', args, { encoding: 'utf8' });
    } catch {
      return '';
    }
  };

  const containers = docker(['ps', '-a', '--format', '{{.Names}}'])
    .split('\n')
    .map((n) => n.trim())
    .filter((n) => new RegExp(`^${CP}(app|deps)-[^ ]+__staging(__(prev|next|swap))?$`).test(n));
  if (containers.length) {
    console.log(`[global-teardown] removendo containers de staging órfãos: ${containers.join(', ')}`);
    docker(['rm', '-f', ...containers]);
  }

  const networks = docker(['network', 'ls', '--format', '{{.Name}}'])
    .split('\n')
    .map((n) => n.trim())
    .filter((n) => new RegExp(`^${CP}(app|deps)-[^ ]+__staging$`).test(n));
  if (networks.length) {
    console.log(`[global-teardown] removendo redes de staging órfãs: ${networks.join(', ')}`);
    docker(['network', 'rm', ...networks]);
  }
}
