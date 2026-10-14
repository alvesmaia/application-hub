import { defineConfig } from '@playwright/test';

// Visual regression (wave 8, design tokens): screenshots of the key pages compared
// with the reference recorded BEFORE the refactoring to tokens (small tolerance for
// relative times and antialiasing). Outside CI (the references are per platform):
//   bash ../scripts/demo.sh   (native hub, fresh data)   and then
//   npx playwright test -c playwright.visual.config.ts [--update-snapshots]
export const HUB = process.env.HUB || 'http://localhost:8080';

export default defineConfig({
  testDir: './visual',
  workers: 1,
  timeout: 120_000,
  globalSetup: './tests/global-setup.ts',
  outputDir: 'test-results-visual',
  snapshotPathTemplate: '{testDir}/__screenshots__/{arg}-{platform}{ext}',
  reporter: [['list']],
  expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: 'disabled', caret: 'hide' } },
  use: { baseURL: HUB, viewport: { width: 1440, height: 900 }, locale: 'pt-BR', timezoneId: 'America/Sao_Paulo' },
});
