import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createServer } from "vite";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const splash = "main[data-vrcforge-startup-shell]";
const statusEvent = "vrcforge-backend-start-status";
const server = await createServer({ server: { host: "127.0.0.1", port: 0, strictPort: false, hmr: false } });
let browser;

try {
  await server.listen();
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({
    headless: true,
    channel: process.env.VRCFORGE_BROWSER_CHANNEL || (process.platform === "win32" ? "msedge" : undefined),
  });

  async function openStartup({ desktop = true, enabled = true, reducedMotion = "no-preference", listenerFails = false } = {}) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, reducedMotion });
    await page.route("**/*", (route) => {
      const url = new URL(route.request().url());
      return url.origin === origin ? route.continue() : route.abort();
    });
    await page.addInitScript(({ desktop, enabled, splash, statusEvent, listenerFails }) => {
      if (!enabled) localStorage.setItem("vrcforge_startup_animation", "false");

      window.startupReplay = { removedAt: null, mountedAtRemoval: false, invoked: false };
      const observer = new MutationObserver((records) => {
        for (const record of records) {
          if ([...record.removedNodes].some((node) => node instanceof Element && node.matches(splash))) {
            window.startupReplay.removedAt = performance.now();
            window.startupReplay.mountedAtRemoval = document.documentElement.dataset.vrcforgeShell === "ready";
            observer.disconnect();
          }
        }
      });
      observer.observe(document, { subtree: true, childList: true });

      if (!desktop) return;
      const handlers = new Map();
      const listeners = new Map();
      let nextId = 0;
      window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener: (_event, id) => listeners.delete(id) };
      window.__TAURI_INTERNALS__ = {
        metadata: { currentWindow: { label: "main" }, currentWebview: { label: "main" } },
        transformCallback(callback) {
          handlers.set(++nextId, callback);
          return nextId;
        },
        async invoke(command, args) {
          if (command === "plugin:event|listen") {
            if (listenerFails && args.event === statusEvent) throw new Error("Fixture event registration failed");
            listeners.set(++nextId, args);
            return nextId;
          }
          if (command === "start_backend") {
            window.startupReplay.invoked = true;
            return { mode: "starting", started: true, endpoint: "http://127.0.0.1:8757", message: "starting" };
          }
          return undefined;
        },
      };
      window.startupReplay.send = (payload) => {
        for (const [id, listener] of listeners) {
          if (listener.event === statusEvent) handlers.get(listener.handler)({ event: statusEvent, id, payload });
        }
      };
    }, { desktop, enabled, splash, statusEvent, listenerFails });
    await page.goto(origin, { waitUntil: "domcontentloaded" });
    await page.waitForFunction((desktop) => document.documentElement.dataset.vrcforgeShell === "ready"
      && (!desktop || window.startupReplay.invoked), desktop);
    return page;
  }

  async function expectDismissed(page) {
    await page.locator(splash).waitFor({ state: "detached", timeout: 6500 });
    assert.equal(await page.locator("#root").evaluate((root) => root.inert), false);
    const receipt = await page.evaluate(() => ({
      removedAt: window.startupReplay.removedAt,
      mountedAtRemoval: window.startupReplay.mountedAtRemoval,
      paintedAt: window.__vrcforgeStartupMetrics.startupShellPaintedMs,
      mountedAt: window.__vrcforgeStartupMetrics.shellCommittedMs,
    }));
    assert.equal(receipt.mountedAtRemoval, true, "the React shell must be mounted before splash dismissal");
    return receipt;
  }

  const web = await openStartup({ desktop: false });
  const webReceipt = await expectDismissed(web);
  assert.ok(webReceipt.removedAt - webReceipt.mountedAt < 300, "web startup must not wait for CSS animations");
  await web.close();

  const deadline = await openStartup();
  assert.equal(await deadline.locator(splash).isVisible(), true);
  assert.equal(await deadline.locator("#root").evaluate((root) => root.inert), true);
  const animationEnd = await deadline.locator(splash).evaluate((node) => Math.max(
    ...node.getAnimations({ subtree: true }).map((animation) => animation.effect.getComputedTiming().endTime),
  ));
  const fallback = await expectDismissed(deadline);
  assert.equal(animationEnd, 4800, "the longest splash animation must end at 4.8 seconds");
  const visibleMs = fallback.removedAt - fallback.paintedAt;
  assert.ok(visibleMs >= 4500 && visibleMs < 5500, `pending runtime exposed App after ${visibleMs} ms`);
  await deadline.close();

  for (const payload of [{ ok: true, status: "ready" }, { ok: false, error: "Fixture startup error" }]) {
    const page = await openStartup();
    assert.equal(await page.locator(splash).isVisible(), true);
    const sentAt = await page.evaluate((payload) => {
      const timestamp = performance.now();
      window.startupReplay.send(payload);
      return timestamp;
    }, payload);
    const receipt = await expectDismissed(page);
    assert.ok(receipt.removedAt - sentAt < 300, "backend status should dismiss immediately");
    await page.close();
  }

  const listenerFailure = await openStartup({ listenerFails: true });
  assert.equal(await listenerFailure.locator(splash).isVisible(), true, "listener failure alone does not mean startup failed");
  await listenerFailure.locator(splash).evaluate((node) => {
    node.getAnimations({ subtree: true }).forEach((animation) => animation.finish());
  });
  await expectDismissed(listenerFailure);
  await listenerFailure.close();

  const reduced = await openStartup({ reducedMotion: "reduce" });
  const reducedReceipt = await expectDismissed(reduced);
  assert.ok(reducedReceipt.removedAt - reducedReceipt.mountedAt < 300, "reduced motion must not wait for the CSS deadline");
  await reduced.close();

  const disabled = await openStartup({ enabled: false });
  assert.equal(await disabled.locator(splash).isVisible(), false);
  assert.equal(await disabled.locator("#root").evaluate((root) => root.inert), false);
  assert.equal(await disabled.evaluate(() => window.__vrcforgeStartupMetrics.startupShellPaintedMs), undefined);
  await disabled.close();
  console.log(`Startup splash: CSS deadline ${Math.round(visibleMs)} ms. Web, backend status, failed listener, reduced motion and disabled preference passed.`);
} finally {
  await browser?.close();
  await server.close();
}
