// Isolated component acceptance: synthetic quota/files only; never a live backend.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React, { useState } from "react";
    import { createRoot } from "react-dom/client";
    import i18n from "i18next";
    import { initReactI18next } from "react-i18next";
    import en from "./src/locales/en-US.json";
    import { CheckpointQuotaNotice } from "./src/components/settings/checkpoint-quota-notice";
    import { CheckpointStoragePanel } from "./src/components/settings/checkpoint-storage-panel";
    i18n.use(initReactI18next).init({ lng: "en", resources: { en: { translation: en } }, interpolation: { escapeValue: false } });
    window.deleted = []; window.managed = 0;
    function Fixture() {
      const [revision, setRevision] = useState(0);
      const [archives, setArchives] = useState([
        { checkpointId: "latest1", sizeBytes: 16, protected: false, autoCleanupProtected: true, protectionReason: "recent" },
        { checkpointId: "latest2", sizeBytes: 16, protected: false, autoCleanupProtected: true, protectionReason: "recent" },
        { checkpointId: "recovering", sizeBytes: 16, protected: true, autoCleanupProtected: true, protectionReason: "active_recovery" },
      ]);
      const status = { gateway: { checkpointArchiveMaxSizeMb: 1, checkpointArchiveUsage: { directory: "/fixture", archives, sizeBytes: archives.length * 16 } } };
      return <>
        <button onClick={() => setRevision(v => v + 1)}>Refresh fixture</button>
        <CheckpointQuotaNotice endpoint={location.origin} connected={true} refreshTrigger={revision} onManage={() => window.managed++} />
        <CheckpointStoragePanel status={status} loading={false} isDesktop={false} limitInput="1"
          onLimitInputChange={() => {}} onSaveLimit={() => {}} onOpenFolder={() => {}} onPickDirectory={async () => ""}
          onRelocate={() => {}} onDeleteSelected={ids => { window.deleted.push(ids); setArchives(v => v.filter(a => !ids.includes(a.checkpointId))); }} />
      </>;
    }
    createRoot(document.getElementById("root")).render(<Fixture />);
  ` },
  bundle: true, write: false, format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"test"' },
});

let used = 2_097_152;
let limit = 1;
let reads = 0;
// Loopback-only, fixture data without secrets; server/browser live only in this test.
const server = createServer((request, response) => {
  if (request.url === "/api/app/checkpoint-archive-usage") {
    reads++;
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({ ok: true, sizeBytes: used, maxSizeMb: limit, directory: "/fixture" }));
  } else if (request.url === "/fixture.js") {
    response.setHeader("Content-Type", "text/javascript");
    response.end(bundle.outputFiles[0].text);
  } else if (request.url === "/") {
    response.setHeader("Content-Type", "text/html");
    response.end('<!doctype html><div id="root"></div><script src="/fixture.js"></script>');
  } else { response.statusCode = 404; response.end(); }
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  const dialog = page.getByRole("dialog");
  await dialog.waitFor({ state: "visible" });
  assert.match(await dialog.innerText(), /Actual usage is 2 MB/);
  await page.getByRole("button", { name: "Later", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  async function refresh() {
    const previous = reads;
    await page.getByRole("button", { name: "Refresh fixture" }).click();
    for (let index = 0; reads === previous && index < 100; index++) await new Promise(r => setTimeout(r, 10));
    assert.ok(reads > previous);
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
  }
  await refresh();
  assert.equal(await dialog.isVisible(), false, "unchanged overage must not reopen");
  used++;
  await refresh();
  await dialog.waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Manage checkpoints" }).click();
  assert.equal(await page.evaluate(() => window.managed), 1);
  assert.deepEqual(await page.evaluate(() => window.deleted), [], "quota warning cannot delete archives");

  limit = 0;
  await refresh();
  assert.equal(await dialog.isVisible(), false, "unlimited disables warning");
  const boxes = page.getByRole("checkbox");
  assert.equal(await boxes.count(), 3);
  assert.equal(await boxes.nth(0).isEnabled(), true);
  assert.equal(await boxes.nth(1).isEnabled(), true);
  assert.equal(await boxes.nth(2).isEnabled(), false);
  await boxes.nth(0).check();
  await boxes.nth(1).check();
  await page.getByRole("button", { name: "Delete selected", exact: true }).click();
  assert.deepEqual(await page.evaluate(() => window.deleted), []);
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  assert.deepEqual(await page.evaluate(() => window.deleted), []);
  await page.getByRole("button", { name: "Delete selected", exact: true }).click();
  await page.getByRole("button", { name: /Delete 2 selected archive/ }).click();
  assert.deepEqual(await page.evaluate(() => window.deleted), [["latest1", "latest2"]]);
  assert.equal(await boxes.count(), 1, "list refreshes after deletion");
  assert.deepEqual(errors, []);
  console.log("PASS browser fixture: over-limit popup, acknowledgement, growth, unlimited, manage action, latest-two selection, cancel, confirmed delete, list refresh");
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
