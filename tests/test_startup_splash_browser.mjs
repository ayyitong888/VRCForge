import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { createServer } from "vite";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
export const splash = "main[data-vrcforge-startup-shell]";
const statusEvent = "vrcforge-backend-start-status";

export async function createStartupBrowserFixture() {
  const server = await createServer({ server: { host: "127.0.0.1", port: 0, strictPort: false, hmr: false } });
  let browser;
  try {
    await server.listen();
    const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
    browser = await chromium.launch({
      headless: true,
      channel: process.env.VRCFORGE_BROWSER_CHANNEL || (process.platform === "win32" ? "msedge" : undefined),
    });

    async function openStartup({
      desktop = true,
      enabled = true,
      reducedMotion = "no-preference",
      listenerFails = false,
      debug,
      viewport = { width: 1120, height: 800 },
      beforeNavigate,
    } = {}) {
      const page = await browser.newPage({ viewport, reducedMotion });
      await page.route("**/*", (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      await page.addInitScript(({ desktop, enabled, splash, statusEvent, listenerFails }) => {
        localStorage.setItem("vrcforge_onboarded", "true");
        localStorage.setItem("vrcforge-locale", "en-US");
        if (!enabled) localStorage.setItem("vrcforge_startup_animation", "false");

        const replay = window.startupReplay = { removedAt: null, mountedAtRemoval: false, invoked: 0, phases: [] };
        const observer = new MutationObserver((records) => {
          for (const record of records) {
            if (record.attributeName === "data-startup-phase" && record.target.matches(splash)) {
              const phase = record.target.dataset.startupPhase;
              if (phase !== replay.phases.at(-1)?.phase) replay.phases.push({ phase, at: performance.now() });
            }
            const removedShell = [...record.removedNodes].find((node) => node instanceof Element && node.matches(splash));
            if (removedShell) {
              replay.removedAt = performance.now();
              replay.mountedAtRemoval = document.documentElement.dataset.vrcforgeShell === "ready";
              replay.sketch = removedShell.querySelector("canvas.startup-sketch");
              observer.disconnect();
            }
          }
        });
        observer.observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ["data-startup-phase"] });

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
              replay.invoked += 1;
              return { mode: "starting", started: true, endpoint: "http://127.0.0.1:8757", message: "starting" };
            }
            return undefined;
          },
        };
        replay.send = (payload) => {
          for (const [id, listener] of listeners) {
            if (listener.event === statusEvent) handlers.get(listener.handler)({ event: statusEvent, id, payload });
          }
        };
      }, { desktop, enabled, splash, statusEvent, listenerFails });
      await beforeNavigate?.(page);
      const url = debug === undefined ? origin : `${origin}/?startupDebug=${debug}`;
      await page.goto(url, { waitUntil: "domcontentloaded" });
      await page.waitForFunction(() => document.documentElement.dataset.vrcforgeShell === "ready");
      if (desktop && debug === undefined) await page.waitForFunction(() => window.startupReplay.invoked > 0);
      return page;
    }

    return {
      openStartup,
      async close() {
        await browser.close();
        await server.close();
      },
    };
  } catch (error) {
    await browser?.close();
    await server.close();
    throw error;
  }
}

async function expectPhase(page, phase, timeout = 7000) {
  await page.locator(`${splash}[data-startup-phase="${phase}"]`).waitFor({ state: "visible", timeout });
}

async function expectDismissed(page) {
  await page.locator(splash).waitFor({ state: "detached", timeout: 11000 });
  assert.equal(await page.locator("#root").evaluate((root) => root.inert), false);
  const receipt = await page.evaluate(() => ({
    removedAt: window.startupReplay.removedAt,
    mountedAtRemoval: window.startupReplay.mountedAtRemoval,
    paintedAt: window.__vrcforgeStartupMetrics.startupShellPaintedMs,
    mountedAt: window.__vrcforgeStartupMetrics.shellCommittedMs,
    phases: window.startupReplay.phases,
  }));
  assert.equal(receipt.mountedAtRemoval, true, "the React shell must be mounted before splash dismissal");
  return receipt;
}

function phaseTime(receipt, phase) {
  const at = receipt.phases.find((item) => item.phase === phase)?.at;
  assert.ok(at !== undefined, `missing phase ${phase} in ${JSON.stringify(receipt)}`);
  return at;
}

function assertDuration(actual, lower, upper, label) {
  assert.ok(actual >= lower && actual <= upper, `${label}: expected ${lower}–${upper} ms, got ${actual.toFixed(0)} ms`);
}

async function runTests() {
  const fixture = await createStartupBrowserFixture();
  const { openStartup } = fixture;
  try {
    const web = await openStartup({ desktop: false });
    const webReceipt = await expectDismissed(web);
    assert.ok(webReceipt.removedAt - webReceipt.mountedAt < 300, "web startup dismisses without the desktop animation");
    await web.close();

    const original = await openStartup({ debug: "0.2" });
    const early = await expectDismissed(original);
    assertDuration(phaseTime(early, "coloring") - phaseTime(early, "drawing"), 660, 1300, "0.2 s remaining line work is accelerated at most 3×");
    assertDuration(phaseTime(early, "holding") - phaseTime(early, "coloring"), 440, 650, "0.2 s ready colors for approximately half a second");
    assertDuration(phaseTime(early, "leaving") - early.paintedAt, 1170, 2400, "minimum visible duration precedes the crossfade");
    assertDuration(early.removedAt - phaseTime(early, "leaving"), 310, 550, "crossfade duration");
    const finishedCheek = await original.evaluate(() => [...window.startupReplay.sketch.getContext("2d").getImageData(558, 314, 1, 1).data]);
    assert.ok(finishedCheek[3] === 255 && finishedCheek[0] > finishedCheek[1], "the final portrait is fully colored before the crossfade");
    await original.close();

    const laterInk = await openStartup({ debug: "1.5" });
    const later = await expectDismissed(laterInk);
    assertDuration(phaseTime(later, "coloring") - phaseTime(later, "drawing"), 1610, 1990, "1.5 s readiness accelerates the remaining line work");
    assertDuration(phaseTime(later, "holding") - phaseTime(later, "coloring"), 440, 650, "1.5 s ready colors for approximately half a second");
    await laterInk.close();

    const coloring = await openStartup();
    await expectPhase(coloring, "coloring");
    await coloring.evaluate(() => window.startupReplay.send({ ok: true, status: "ready" }));
    const midColor = await expectDismissed(coloring);
    assertDuration(phaseTime(midColor, "holding") - phaseTime(midColor, "coloring"), 920, 1250, "readiness during coloring keeps the original one-second color pass");
    await coloring.close();

    const shortWait = await openStartup({ debug: "3.5" });
    const short = await expectDismissed(shortWait);
    assertDuration(phaseTime(short, "waiting") - phaseTime(short, "drawing"), 2900, 3250, "short delay begins idle once the drawing is complete");
    assertDuration(phaseTime(short, "leaving") - short.paintedAt, 3400, 4300, "3.5 s ready leaves from idle");
    await shortWait.close();

    const delayed = await openStartup();
    await delayed.evaluate(() => window.startupReplay.send({ status: "progress", step: "listen" }));
    await expectPhase(delayed, "waiting");
    await delayed.locator(splash).evaluate((node) => {
      const context = node.querySelector("canvas").getContext("2d");
      const samples = window.startupReplay.idleSamples = { eye: new Set(), lamp: new Set() };
      node.addEventListener("startup-frame", ({ detail }) => {
        if (detail.idle <= 0) return;
        samples.eye.add(context.getImageData(517, 278, 1, 1).data.join(","));
        samples.lamp.add(context.getImageData(727, 193, 1, 1).data.join(","));
      });
    });
    const status = delayed.locator(splash).getByRole("status");
    assert.equal(await status.isVisible(), false, "status text stays hidden before six seconds");
    await status.waitFor({ state: "visible", timeout: 5000 });
    const listenText = await status.innerText();
    assert.match(listenText, /listen|local|runtime/i);
    const idle = await delayed.evaluate(() => ({
      eye: window.startupReplay.idleSamples.eye.size,
      lamp: window.startupReplay.idleSamples.lamp.size,
    }));
    assert.ok(idle.eye > 1 && idle.lamp > 1, "the original portrait blinks and the headset light changes in idle");
    await delayed.evaluate(() => window.startupReplay.send({ status: "progress", step: "verify" }));
    await delayed.waitForFunction(({ splash, previous }) => {
      const current = document.querySelector(`${splash} [role="status"]`)?.textContent?.trim();
      return current && current !== previous.trim();
    }, { splash, previous: listenText });
    await delayed.evaluate(() => window.startupReplay.send({ ok: true, status: "ready" }));
    await expectDismissed(delayed);
    await delayed.close();

    const exited = await openStartup();
    const exitedAt = await exited.evaluate(() => {
      const at = performance.now();
      window.startupReplay.send({ ok: false, status: "exited", error: "Fixture process exited with code 7", exitCode: 7, logDir: "fixture/logs" });
      return at;
    });
    await expectPhase(exited, "error");
    assert.equal(await exited.locator(splash).getAttribute("data-startup-reason"), "exited");
    assert.match(await exited.locator(splash).innerText(), /code 7/);
    assertDuration(await exited.evaluate(() => window.startupReplay.phases.find(({ phase }) => phase === "error").at) - exitedAt,
      0, 350, "process exit surfaces immediately");
    const invoked = await exited.evaluate(() => window.startupReplay.invoked);
    await exited.locator(splash).getByRole("button", { name: /retry|重试/i }).click();
    await exited.waitForFunction((count) => window.startupReplay.invoked > count, invoked);
    await exited.evaluate(() => window.startupReplay.send({ ok: true, status: "ready" }));
    await expectDismissed(exited);
    await exited.close();

    const listenerFailure = await openStartup({ listenerFails: true });
    await expectPhase(listenerFailure, "waiting");
    await expectPhase(listenerFailure, "error", 22000);
    assert.equal(await listenerFailure.locator(splash).getAttribute("data-startup-reason"), "timeout");
    const timeoutElapsed = await listenerFailure.evaluate(() =>
      window.startupReplay.phases.find(({ phase }) => phase === "error").at
      - window.startupReplay.phases.find(({ phase }) => phase === "drawing").at);
    assertDuration(timeoutElapsed, 19700, 20700, "timeout follows its own 20-second deadline even if subscription fails");
    assert.equal(await listenerFailure.locator(splash).getByRole("button", { name: /retry|重试/i }).isVisible(), true);
    await listenerFailure.close();

    const reduced = await openStartup({ reducedMotion: "reduce", debug: "0.2" });
    const reducedReceipt = await expectDismissed(reduced);
    assertDuration(reducedReceipt.removedAt - reducedReceipt.paintedAt, 1170, 1800, "reduced motion shows still art for the minimum time");
    await reduced.close();

    const disabled = await openStartup({ enabled: false });
    assert.equal(await disabled.locator(splash).isVisible(), false);
    assert.equal(await disabled.locator("#root").evaluate((root) => root.inert), false);
    assert.equal(await disabled.evaluate(() => window.__vrcforgeStartupMetrics.startupShellPaintedMs), undefined);
    await disabled.close();
    console.log("Startup browser replay passed: early ink, original-speed coloring, idle, progress, process exit, retry, timeout, reduced motion, disabled preference and web.");
  } finally {
    await fixture.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await runTests();
