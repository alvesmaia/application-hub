import { test, expect } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { HUB } from './helpers';

// An app's CSS framework inside the hub (CONTRATO-APP §29): the same native controls styled by plain HTML,
// Tailwind (with and without preflight) and Bootstrap 5, measured outside and inside the app area. Fails on
// what the hub breaks: rem sizes, the app's own border/color/radius overridden, a double select arrow with
// `hub-select-plain`, borders or text that pass outside and fail inside. Report: .work/compat-report.md.
test('frameworks de CSS dos apps não são quebrados pelo hub @core', async () => {
  test.setTimeout(240_000);
  const run = spawnSync(process.execPath, [path.join(__dirname, '..', 'compat', 'run.mjs')], { env: { ...process.env, HUB }, encoding: 'utf8' });
  expect(run.status, `${run.stdout}${run.stderr}`).toBe(0);
});
