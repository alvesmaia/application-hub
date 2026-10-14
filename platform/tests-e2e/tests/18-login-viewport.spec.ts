import { test, expect } from '@playwright/test';

// The login screen fits in a viewport (no scrollbar) at common sizes.
const SIZES = [
  [1280, 720],
  [1366, 768],
  [1440, 900],
  [1920, 1080],
  [390, 844],
] as const;

for (const [width, height] of SIZES) {
  test(`login sem rolagem em ${width}x${height} @core`, async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width, height } });
    const page = await ctx.newPage();
    await page.goto('/login');
    await expect(page.locator('button.auth-submit')).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const m = await page.evaluate(() => ({
      sh: document.documentElement.scrollHeight,
      sw: document.documentElement.scrollWidth,
      ih: innerHeight,
      iw: innerWidth,
    }));
    expect(m.sh, 'altura do documento').toBeLessThanOrEqual(m.ih);
    expect(m.sw, 'largura do documento').toBeLessThanOrEqual(m.iw);
    await ctx.close();
  });
}
