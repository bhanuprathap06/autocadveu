// Run from apps/web (uses its Playwright install):  node ../../tools/kernel-bench/run-browser-bench.mjs
import path from "node:path";
const { chromium } = await import(path.join(process.cwd(), "node_modules", "playwright", "index.mjs"));
const browser = await chromium.launch();
const page = await browser.newPage();
page.on("pageerror", (e) => console.log("pageerror", e.message));
await page.goto("http://127.0.0.1:8765/bench/browser-bench.html");
await page.waitForFunction(() => window.__done, null, { timeout: 600000 });
console.log(JSON.stringify(await page.evaluate(() => window.__done), null, 1));
await browser.close();
