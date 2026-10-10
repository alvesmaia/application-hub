import { defineConfig } from '@playwright/test';

// Regressão visual (onda 8, design tokens): screenshots das páginas-chave comparados
// com a referência gravada ANTES da refatoração para tokens (tolerância pequena para
// horários relativos e antialiasing). Fora do CI (as referências são por plataforma):
//   bash ../scripts/demo.sh   (hub nativo, dados novos)   e depois
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
