import { request } from '@playwright/test';
import fs from 'node:fs';
import { AUTH_DIR, HUB, USERS, stateFile, type Role } from './helpers';

/** Waits for the hub to respond and saves each demo user's session. */
export default async function globalSetup() {
  const t0 = Date.now();
  for (;;) {
    try {
      const ctx = await request.newContext({ baseURL: HUB });
      const r = await ctx.get('/healthz');
      await ctx.dispose();
      if (r.ok()) break;
    } catch { /* still starting up */ }
    if (Date.now() - t0 > 180_000) throw new Error(`hub não respondeu em ${HUB}/healthz`);
    await new Promise((r) => setTimeout(r, 2000));
  }
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  if (process.env.SWEEP_DEV_HUB) {
    // hub dev: simulated identity on every request, no login.
    for (const role of Object.keys(USERS) as Role[]) fs.writeFileSync(stateFile(role), JSON.stringify({ cookies: [], origins: [] }));
    return;
  }
  for (const role of Object.keys(USERS) as Role[]) {
    const u = USERS[role];
    const ctx = await request.newContext({ baseURL: HUB, extraHTTPHeaders: { origin: HUB } });
    const r = await ctx.post('/login', { form: { email: u.email, password: u.password, next: '/' }, maxRedirects: 0 });
    if (r.status() !== 303) throw new Error(`login ${u.email}: HTTP ${r.status()}`);
    // Wave 9: the guided tour starts by itself on the first visit to Home — the old scripts
    // start from a user who has already seen it (the onboarding scenario resets it).
    await ctx.put('/api/me/onboarding', { data: { tour: 'done' } }).catch(() => undefined);
    await ctx.storageState({ path: stateFile(role) });
    await ctx.dispose();
  }
}
