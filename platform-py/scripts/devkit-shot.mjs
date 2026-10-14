// Screenshot of `hub dev` (proof for the developer kit).
// Usage: node platform-py/scripts/devkit-shot.mjs <url> <file.png>  (playwright from platform/tests-e2e)
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const e2e = process.env.DEVKIT_E2E_DIR || path.join(here, "../../platform/tests-e2e");
const require = createRequire(path.resolve(e2e, "package.json"));
const { chromium } = require("@playwright/test");
const [url, out] = process.argv.slice(2);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
await page.goto(url, { waitUntil: "load" });
await page.waitForSelector(".workspace-content .page-title", { timeout: 15000 });
await page.screenshot({ path: out });
console.log(JSON.stringify({ title: await page.locator(".page-title").first().textContent(), errors }));
await browser.close();
