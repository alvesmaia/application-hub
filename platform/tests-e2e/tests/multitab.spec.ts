import { test, expect, type Page, type Request } from '@playwright/test';
import { apiAs, json, openApp, stateFile, uniq } from './helpers';

// Várias abas no MESMO navegador (um contexto, 5 páginas): apps/seções diferentes,
// recarregar, deep links sem loader, UMA conexão SSE de logs para todas as abas (contada
// no servidor por /api/admin/logs/stats e nos eventos de rede) com failover quando a aba
// líder fecha, e edição concorrente → 409 na outra aba.
test.use({ storageState: stateFile('admin') });

async function subscribers(): Promise<number> {
  const api = await apiAs('admin');
  const s = await json(await api.get('/api/admin/logs/stats'));
  await api.dispose();
  return s.subscribers;
}

test('5 abas: apps diferentes, deep links, SSE compartilhado com failover e 409 entre abas', async ({ browser }) => {
  test.setTimeout(300_000);
  const ctx = await browser.newContext({ storageState: stateFile('admin') });
  const streams: { page: Page; req: Request }[] = [];
  const pages: Page[] = [];
  const newTab = async () => {
    const p = await ctx.newPage();
    p.on('request', (req) => { if (req.url().includes('/api/admin/logs/stream')) streams.push({ page: p, req }); });
    pages.push(p);
    return p;
  };
  const base = await subscribers();

  // Abas 1–3: três apps diferentes, abertas direto numa seção (deep link → sem loader).
  const [t1, t2, t3] = [await newTab(), await newTab(), await newTab()];
  await t1.goto('/apps/ativos/assets?q=MacBook');
  await t2.goto('/apps/fornecedores/indicators');
  await t3.goto('/apps/servicos/requests');
  for (const t of [t1, t2, t3]) {
    await expect(t.locator('#app-root .app-sidebar')).toBeVisible({ timeout: 20_000 });
  }
  // Recarregar uma aba não afeta as outras.
  await t2.reload();
  await expect(t2.locator('#app-root .app-sidebar')).toBeVisible();
  await expect(t1.locator('#app-root')).toContainText('MacBook');

  // Abas 4–5 + a aba 3 (navegada): console de logs em "Tempo real" em três abas.
  const [t4, t5] = [await newTab(), await newTab()];
  const live = [t3, t4, t5];
  for (const t of live) {
    await t.goto('/admin/logs');
    await t.click('[data-log-live]');
  }
  for (const t of live) await expect(t.locator('[data-log-status]')).toContainText('Conectado', { timeout: 20_000 });
  await expect.poll(() => subscribers(), { timeout: 30_000 }).toBe(base + 1);
  expect(streams.length, 'uma requisição de stream em todo o navegador').toBe(1);
  const leader = streams[0].page;
  const followers = live.filter((t) => t !== leader);
  test.info().annotations.push({ type: 'sse', description: `assinantes no servidor: ${base} → ${base + 1} com 3 abas em tempo real; líder = aba ${pages.indexOf(leader) + 1}` });

  // Todas as abas recebem a mesma linha (a líder repassa por BroadcastChannel).
  const marker = `multitab-${uniq().toLowerCase()}`;
  await expect.poll(async () => {
    await t1.request.get(`/_frag/servicos/${marker}`);
    const texts = await Promise.all(live.map((t) => t.locator('[data-log-terminal]').textContent()));
    return texts.every((x) => (x || '').includes(marker));
  }, { timeout: 30_000 }).toBe(true);

  // Failover: fecha a líder → outra aba assume, continua uma conexão só.
  await leader.close();
  await expect.poll(() => streams.length, { timeout: 30_000 }).toBe(2);
  const newLeader = streams[1].page;
  expect(followers).toContain(newLeader);
  await expect.poll(() => subscribers(), { timeout: 45_000 }).toBe(base + 1);
  const marker2 = `failover-${uniq().toLowerCase()}`;
  await expect.poll(async () => {
    await t1.request.get(`/_frag/servicos/${marker2}`);
    const texts = await Promise.all(followers.map((t) => t.locator('[data-log-terminal]').textContent()));
    return texts.every((x) => (x || '').includes(marker2));
  }, { timeout: 30_000 }).toBe(true);
  test.info().annotations.push({ type: 'failover', description: `líder fechada → aba ${pages.indexOf(newLeader) + 1} assumiu; assinantes ${base + 1}` });

  // Edição concorrente do mesmo ativo em duas abas da mesma sessão → 409 na segunda.
  const id = 46;
  const [e1, e2] = [t1, t2];
  await openApp(e1, `/apps/ativos/assets?edit=${id}`);
  await openApp(e2, `/apps/ativos/assets?edit=${id}`);
  const form = (p: Page) => p.locator(`form[action="/apps/ativos/itens/${id}"]`).first();
  await form(e1).locator('input[name="name"]').fill(`Aba 1 ${uniq()}`);
  const s1 = e1.waitForResponse((r) => r.url().endsWith(`/_frag/ativos/itens/${id}`));
  await form(e1).locator('button[type="submit"]').click();
  expect((await s1).status()).toBe(200);
  await form(e2).locator('input[name="name"]').fill('Aba 2');
  const s2 = e2.waitForResponse((r) => r.url().endsWith(`/_frag/ativos/itens/${id}`));
  await form(e2).locator('button[type="submit"]').click();
  expect((await s2).status()).toBe(409);
  await expect(e2.locator('#app-root')).toContainText('Este ativo foi alterado por outra pessoa');

  // Todas as abas fechadas → nenhuma conexão sobra no servidor.
  await ctx.close();
  await expect.poll(() => subscribers(), { timeout: 45_000 }).toBe(base);
});
