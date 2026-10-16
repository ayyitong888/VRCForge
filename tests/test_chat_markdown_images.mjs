import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createRequire } from "node:module";

const root = resolve(import.meta.dirname, "..");
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const current = JSON.parse(readFileSync(resolve(root, "src-tauri/tauri.conf.json"))).app.security.csp;
const old = current.replace(/img-src[^;]+;/, "img-src 'self' asset: http://asset.localhost data: blob: http://127.0.0.1:8757;");
const browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
try {
  for (const [csp, expected] of [[old, 0], [current, 1]]) {
    const page = await browser.newPage();
    await page.route("https://markdown-image.example/pixel.png", (route) => route.fulfill({
      contentType: "image/png",
      body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lUXYkwAAAABJRU5ErkJggg==", "base64"),
    }));
    await page.setContent(`<meta http-equiv="Content-Security-Policy" content="${csp}"><img src="https://markdown-image.example/pixel.png"><script>window.unsafeMarkdownExecuted=true</script>`);
    await page.waitForFunction(() => document.querySelector("img").complete);
    assert.equal(await page.locator("img").evaluate((img) => img.naturalWidth), expected);
    assert.equal(await page.evaluate(() => window.unsafeMarkdownExecuted), undefined, "inline scripts remain blocked");
    await page.close();
  }
} finally {
  await browser.close();
}
console.log("Markdown remote images: old CSP RED, current CSP GREEN; inline scripts blocked");
