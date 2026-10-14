import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { stateFile } from './helpers';

// Wave 13 — application catalog: each app's stack (manifest `stack`) appears only on the card's "i" button (hover, keyboard focus, tap); hovering the card shows nothing.
test.use({ storageState: stateFile('admin') });

test('aplicações: balão da stack só no "i" (hover, foco, toque; Esc fecha) @core', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/applications');
  const card = page.locator('.app-card[data-app="ativos"]');
  const stack = card.locator('.hub-stack');
  const t = card.locator('.hub-stack-toggle');
  await expect(stack).toBeHidden();
  // hovering the card shows nothing: the balloon belongs only to the "i"
  await card.hover({ position: { x: 20, y: 60 } });
  await expect(stack).toBeHidden();
  await t.hover();
  await expect(stack).toBeVisible();
  await expect(stack).toContainText('Python 3.12 · FastAPI');
  await expect(stack).toContainText('hub-sdk-python 1.0');
  await expect(stack).toContainText('PostgreSQL 16');
  await expect(stack.locator('.hub-stack-chip')).toContainText(['python-calamine']);
  await expect(stack).toContainText(/API ligada · MCP ligado/);
  await expect(stack).toContainText(/v\d+\.\d+\.\d+/);
  await expect(card.locator('.open-link')).toBeVisible();
  const coletor = page.locator('.app-card[data-app="coletor"]');
  if (await coletor.count()) {
    await coletor.locator('.hub-stack-toggle').hover();
    await expect(coletor.locator('.hub-stack')).toContainText('Python 3.12 · FastAPI');
    await expect(coletor.locator('.hub-stack-chip')).toContainText(['python-barcode', 'segno']);
    await page.waitForTimeout(300); // end of the transition
    fs.mkdirSync(path.join(__dirname, '..', '..', 'dist'), { recursive: true });
    await page.screenshot({ path: path.join(__dirname, '..', '..', 'dist', 'applications-hover.png') });
  }
  // keyboard: focus on the "i" shows the stack
  await page.mouse.move(0, 0);
  await expect(stack).toBeHidden();
  await t.focus();
  await expect(stack).toBeVisible();
  // tap: the button toggles and reports the state; Esc closes
  await page.locator('h1').first().click();
  await expect(t).toHaveAttribute('aria-expanded', 'false');
  await expect(t).toHaveAttribute('aria-label', /Gestão de Ativos/);
  await t.click();
  await expect(t).toHaveAttribute('aria-expanded', 'true');
  await expect(card).toHaveClass(/hub-stack-open/);
  await page.keyboard.press('Escape');
  await expect(card).not.toHaveClass(/hub-stack-open/);
});
