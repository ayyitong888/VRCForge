import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { build } from "esbuild";

const root = process.cwd();
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundled = await build({
  stdin: {
    resolveDir: root,
    loader: "tsx",
    contents: `
      import React, { useState } from 'react';
      import { createRoot } from 'react-dom/client';
      import { initReactI18next } from 'react-i18next';
      import i18n from './src/i18n';
      import messages from './src/locales/en-US.json';
      import { Composer } from './src/components/chat/composer';
      i18n.use(initReactI18next).init({ lng: 'en-US', resources: { 'en-US': { translation: messages } }, interpolation: { escapeValue: false } });
      window.submitCount = 0;
      window.queueCount = 0;
      window.steerCount = 0;
      window.stopCount = 0;
      function Fixture() {
        const [state, setState] = useState({ input: '', sending: false, queueAllowed: false, attachments: [] });
        window.setComposerState = (patch) => setState(current => ({ ...current, ...patch }));
        return <Composer
          input={state.input} setInput={value => setState(current => ({ ...current, input: value }))}
          sending={state.sending} queueAllowed={state.queueAllowed} attachments={state.attachments}
          onSubmit={event => { event?.preventDefault(); window.submitCount += 1; }}
          onQueue={() => { window.queueCount += 1; }}
          onSteerNow={() => { window.steerCount += 1; }}
          onStop={() => { window.stopCount += 1; }}
          onSwitchMode={() => {}} actions={[]} commands={[]}
        />;
      }
      createRoot(document.getElementById('root')).render(<Fixture />);
    `,
  },
  bundle: true,
  write: false,
  format: "iife",
  platform: "browser",
  jsx: "automatic",
  plugins: [{
    name: "tauri-test-stubs",
    setup(plugin) {
      plugin.onResolve({ filter: /^@tauri-apps\/api\/(webview|core)$/ }, ({ path }) => ({ path, namespace: "tauri-stub" }));
      plugin.onLoad({ filter: /.*/, namespace: "tauri-stub" }, ({ path }) => ({
        loader: "js",
        contents: path.endsWith("/webview")
          ? "export const getCurrentWebview = () => ({ onDragDropEvent: async () => () => {} });"
          : "export const convertFileSrc = (path) => path; export const invoke = async () => undefined;",
      }));
    },
  }],
});

const browser = await chromium.launch({ channel: "msedge", headless: true });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setContent('<div id="root"></div>');
  await page.addScriptTag({ content: bundled.outputFiles[0].text });
  const primary = page.locator("[data-composer-primary-action]");
  await primary.waitFor();

  assert.equal(await primary.count(), 1, "composer exposes one primary action");
  assert.equal(await primary.getAttribute("data-composer-primary-action"), "send");
  assert.equal(await primary.isDisabled(), true, "empty idle composer cannot send");
  await page.locator("textarea").fill("idle message");
  assert.equal(await primary.getAttribute("aria-label"), "Send");
  assert.equal(await primary.getAttribute("title"), "Send");
  assert.match(await primary.locator("svg").getAttribute("class"), /lucide-arrow-up/);
  await primary.click();
  assert.equal(await page.evaluate(() => window.submitCount), 1, "idle action sends through onSubmit");
  await page.locator("textarea").press("Enter");
  assert.equal(await page.evaluate(() => window.submitCount), 2, "idle Enter sends through onSubmit");
  await page.locator("textarea").press("Shift+Enter");
  assert.equal(await page.evaluate(() => window.submitCount), 2, "idle Shift+Enter remains a newline");

  await page.evaluate(() => window.setComposerState({ input: "", attachments: [{ id: "image", name: "image.png", type: "image/png", size: 8 }] }));
  assert.equal(await primary.getAttribute("data-composer-primary-action"), "send");
  assert.equal(await primary.isDisabled(), false, "attachment-only content is sendable");
  await primary.click();
  assert.equal(await page.evaluate(() => window.submitCount), 3, "attachment-only send uses onSubmit");

  await page.evaluate(() => window.setComposerState({ input: "follow-up", attachments: [], sending: true, queueAllowed: true }));
  assert.equal(await primary.getAttribute("data-composer-primary-action"), "steer");
  assert.equal(await primary.getAttribute("aria-label"), "Steer");
  assert.equal(await primary.getAttribute("title"), "Steer");
  assert.match(await primary.locator("svg").getAttribute("class"), /lucide-arrow-up/);
  assert.equal(await page.locator("[data-composer-send], [data-composer-stop]").count(), 0, "running steer replaces adjacent Send/Stop controls");
  assert.equal((await primary.locator("svg").count()), 1);
  await primary.click();
  assert.equal(await page.evaluate(() => window.submitCount), 4, "running follow-up uses the existing submit/steer path");
  await page.locator("textarea").fill("enter steer");
  await page.locator("textarea").press("Enter");
  assert.equal(await page.evaluate(() => window.queueCount), 1, "Enter queues while a run is active");
  assert.equal(await page.evaluate(() => window.submitCount), 4, "Enter does not submit or steer while running");
  await page.locator("textarea").press("Control+Enter");
  assert.equal(await page.evaluate(() => window.steerCount), 1, "Ctrl+Enter immediately steers while a run is active");
  await page.locator("textarea").press("Shift+Enter");
  assert.equal(await page.evaluate(() => window.queueCount), 1, "Shift+Enter remains a newline, not a queue action");
  assert.equal(await page.evaluate(() => window.steerCount), 1, "Shift+Enter remains a newline, not a steer action");

  await page.evaluate(() => window.setComposerState({ input: "", attachments: [{ id: "image", name: "image.png", type: "image/png", size: 8 }] }));
  assert.equal(await primary.getAttribute("data-composer-primary-action"), "steer", "attachment-only content steers an active run");
  await primary.click();
  assert.equal(await page.evaluate(() => window.submitCount), 5, "attachment steer uses onSubmit");
  await page.locator("textarea").press("Enter");
  assert.equal(await page.evaluate(() => window.queueCount), 2, "attachment-only Enter queues while a run is active");

  await page.evaluate(() => window.setComposerState({ input: "", attachments: [], sending: true, queueAllowed: true }));
  await page.waitForFunction(() => document.querySelector("[data-composer-primary-action]")?.getAttribute("data-composer-primary-action") === "stop");
  assert.equal(await primary.getAttribute("data-composer-primary-action"), "stop");
  assert.equal(await primary.getAttribute("aria-label"), "Stop");
  assert.equal(await primary.getAttribute("title"), "Stop");
  assert.equal(await primary.getAttribute("type"), "button");
  assert.match(await primary.locator("svg").getAttribute("class"), /lucide-square/);
  assert.match(await primary.locator("svg").getAttribute("class"), /fill-current/);
  await primary.click();
  assert.equal(await page.evaluate(() => window.stopCount), 1, "empty running composer keeps the existing onStop/cancel behavior");

  await page.evaluate(() => window.setComposerState({ input: "cannot steer while stopping", sending: true, queueAllowed: false }));
  assert.equal(await primary.getAttribute("data-composer-primary-action"), "stop", "a stopping run cannot accept a steer");
  assert.equal(await page.evaluate(() => window.submitCount), 5);
  assert.deepEqual(errors, []);
  console.log("composer primary action and keyboard: idle send, running queue, Ctrl+Enter steer, Shift+Enter newline, attachment content, and stop passed");
} finally {
  await browser.close();
}
