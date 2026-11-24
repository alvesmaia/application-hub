import { test, expect } from '@playwright/test';
import { stateFile } from './helpers';

// The app's sidebar collapses on the desktop (icons only), the choice survives navigation and reload,
// and phones keep the drawer (no collapse button).
test.use({ storageState: stateFile('admin'), viewport: { width: 1366, height: 800 } });

test('barra lateral do app: recolher no desktop, mantém ao navegar e recarregar, volta ao expandir @core', async ({ page }) => {
  await page.goto('/apps/inventory');
  const side = page.locator('#app-root .app-sidebar');
  const toggle = side.locator('[data-hub-menu="collapse"]');
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  const open = (await side.boundingBox())!.width;
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect.poll(async () => (await side.boundingBox())!.width).toBeLessThan(open / 2);
  // labels stay for screen readers and as a hint
  const item = side.locator('.nav-item').nth(1);
  await expect(item).toHaveAttribute('title', /.+/);
  await item.click();
  await expect(side.locator('[data-hub-menu="collapse"]')).toHaveAttribute('aria-expanded', 'false');
  await page.reload();
  await expect(page.locator('#app-root .app-sidebar [data-hub-menu="collapse"]')).toHaveAttribute('aria-expanded', 'false');
  await page.locator('#app-root .app-sidebar [data-hub-menu="collapse"]').click();
  await expect.poll(async () => (await page.locator('#app-root .app-sidebar').boundingBox())!.width).toBeCloseTo(open, 0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('#app-root .app-sidebar [data-hub-menu="collapse"]')).toBeHidden();
});
