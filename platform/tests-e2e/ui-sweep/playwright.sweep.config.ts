import { defineConfig } from '@playwright/test';
import { HUB } from './common';

// UI sweep (@sweep) — use `node ui-sweep/run.mjs [--quick]` (joins and applies the allowlist).
export default defineConfig({
  testDir: '.',
  testMatch: /sweep\.spec\.ts/,
  workers: 2,
  fullyParallel: true,
  timeout: 30 * 60_000,
  retries: 0,
  globalSetup: './setup.ts',
  outputDir: '../test-results/ui-sweep-pw',
  reporter: [['list']],
  use: { baseURL: HUB, locale: 'pt-BR', actionTimeout: 5_000, navigationTimeout: 30_000 },
});
