import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const html = await readFile(new URL("../index.html", import.meta.url), "utf8");
const url = "http://vrcforge-startup-fixture.test/";
const browser = await chromium.launch({
  headless: true,
  channel: process.env.VRCFORGE_BROWSER_CHANNEL || (process.platform === "win32" ? "msedge" : undefined),
});

try {
  for (const desktop of [true, false]) {
    const page = await browser.newPage();
    await page.route("**/*", route => route.request().url() === url
      ? route.fulfill({ contentType: "text/html", body: html })
      : route.abort());
    if (desktop) await page.addInitScript(() => { window.__TAURI_INTERNALS__ = {}; });
    await page.goto(url, { waitUntil: "domcontentloaded" });
    const state = await page.evaluate(() => {
      const target = document.getElementById("root");
      let deliveredContextActions = 0;
      target.addEventListener("contextmenu", () => ++deliveredContextActions);
      const cancelled = (type, button) => !target.dispatchEvent(new MouseEvent(type, { button, bubbles: true, cancelable: true }));
      return {
        contextCancelled: cancelled("contextmenu", 2),
        historyCancelled: [3, 4].every(button => ["mousedown", "mouseup", "auxclick"].every(type => cancelled(type, button))),
        middleClickCancelled: cancelled("auxclick", 1),
        deliveredContextActions,
      };
    });
    assert.deepEqual(state, {
      contextCancelled: desktop,
      historyCancelled: desktop,
      middleClickCancelled: false,
      deliveredContextActions: 1,
    }, "the first document suppresses desktop browser controls while leaving the app context action reachable");
    await page.close();
  }
  console.log("startup browser controls are suppressed in the app before any React code loads");
} finally {
  await browser.close();
}
