import { defineConfig } from '@playwright/test';

// Compose no ar com os usuários de demonstração (HUB_DEMO_USERS=1):
//   cd platform && docker compose up -d && cd tests-e2e && npm ci && npx playwright install chromium && npm run e2e
// HUB (padrão http://localhost:8080) e HUB_STAGING (padrão http://staging.localhost:8080).
export const HUB = process.env.HUB || 'http://localhost:8080';

export default defineConfig({
  testDir: './tests',
  // Um só worker: os cenários mudam estado compartilhado (implantações, backups, variáveis).
  workers: 1,
  fullyParallel: false,
  timeout: 180_000,
  expect: { timeout: 20_000 },
  retries: process.env.CI ? 1 : 0,
  globalSetup: './tests/global-setup.ts',
  globalTeardown: './tests/global-teardown.ts',
  outputDir: 'test-results',
  reporter: [
    ['list'],
    ['html', { outputFolder: 'reports/html', open: 'never' }],
    ['json', { outputFile: 'reports/results.json' }],
    ['junit', { outputFile: 'reports/junit.xml' }],
  ],
  use: {
    baseURL: HUB,
    viewport: { width: 1440, height: 900 },
    locale: 'pt-BR',
    acceptDownloads: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 20_000,
    navigationTimeout: 45_000,
  },
  projects: [
    { name: 'demo', testMatch: /\d\d-.*\.spec\.ts/, testIgnore: /multitab/ },
    { name: 'multitab', testMatch: /multitab\.spec\.ts/ },
  ],
});
