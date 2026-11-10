import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React from "react";
    import { createRoot } from "react-dom/client";
    import { SettingsWorkspace } from "./src/components/settings/settings-workspace";
    import { MemoryReviewSettings } from "./src/components/settings/memory-review-settings";
    import { DEFAULT_THEME_CUSTOMIZATION } from "./src/lib/theme-customization";
    import { initializeI18n } from "./src/i18n";
    initializeI18n().then(() => createRoot(document.getElementById("root")).render(<>
      <SettingsWorkspace activeSection="general" currentLanguage="en-US" visionProvider=""
        themeCustomization={DEFAULT_THEME_CUSTOMIZATION} />
      <MemoryReviewSettings endpoint={location.origin} runtimeConnected selectedProjectPath="" />
    </>));
  ` },
  bundle: true, write: false, platform: "browser", format: "esm", jsx: "automatic",
});
const theme = await readFile("src/theme.css", "utf8");
const { css } = await postcss([tailwindcss("./tailwind.config.ts")])
  .process(theme + "\n@tailwind base; @tailwind components; @tailwind utilities;", { from: undefined });
const server = createServer((request, response) => {
  if (request.url.startsWith("/api/app/agent/memory/review")) {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({
      memoryEnabled: true, crossSessionEnabled: false, revision: 1,
      candidates: [], runStatus: { state: "idle" }, providerDisclosure: {},
    }));
    return;
  }
  response.setHeader("Content-Type", "text/html;charset=utf-8");
  response.end('<style>' + css + '</style><div id="root"></div><script type="module">' +
    bundle.outputFiles[0].text.replaceAll("</script", "<\\/script") + "</script>");
});

await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
let browser;
try {
  browser = await chromium.launch({ headless: true, channel: "msedge" });
  const page = await browser.newPage({ locale: "en-US", deviceScaleFactor: 1.5 });
  await page.goto("http://127.0.0.1:" + server.address().port);
  const misaligned = [];

  async function checkThumb(track, checked, label) {
    const screenshot = await track.screenshot({ animations: "disabled" });
    const gaps = await page.evaluate(async (png) => {
      const image = new Image();
      image.src = "data:image/png;base64," + png;
      await image.decode();
      const { width, height } = image;
      const canvas = new OffscreenCanvas(width, height);
      const ctx = canvas.getContext("2d");
      ctx.drawImage(image, 0, 0);
      const data = ctx.getImageData(0, 0, width, height).data;
      const isWhite = (x, y) => {
        const offset = (y * width + x) * 4;
        return data[offset] >= 250 && data[offset + 1] >= 250 && data[offset + 2] >= 250;
      };
      const thumbXs = Array.from({ length: width }, (_, x) => x)
        .filter((x) => isWhite(x, Math.floor(height / 2)));
      const left = thumbXs[0];
      const right = thumbXs.at(-1);
      const middle = Math.round((left + right) / 2);
      const thumbYs = Array.from({ length: height }, (_, y) => y).filter((y) => isWhite(middle, y));
      return { left, right: width - 1 - right, top: thumbYs[0], bottom: height - 1 - thumbYs.at(-1) };
    }, screenshot.toString("base64"));
    const expectedEdge = checked ? gaps.right : gaps.left;
    if (!Number.isFinite(expectedEdge) || !Number.isFinite(gaps.bottom) ||
        Math.abs(gaps.top - gaps.bottom) > 1 || Math.abs(expectedEdge - (gaps.top + gaps.bottom) / 2) > 1) {
      misaligned.push({ label, ...gaps });
    }
  }

  const startup = page.getByRole("switch", { name: "Startup animation" });
  const startupTrack = startup.locator("xpath=following-sibling::span");
  assert.equal(await startup.isChecked(), true);
  await checkThumb(startupTrack, true, "Startup on");
  await startup.press("Space");
  assert.equal(await startup.isChecked(), false);
  await checkThumb(startupTrack, false, "Startup off");

  const memory = page.locator('[data-memory-toggle="memory"]:enabled');
  const crossSession = page.locator('[data-memory-toggle="cross-session"]:enabled');
  assert.equal(await memory.getAttribute("aria-checked"), "true");
  assert.equal(await crossSession.getAttribute("aria-checked"), "false");
  await checkThumb(memory, true, "Memory on");
  await checkThumb(crossSession, false, "Cross-session memory off");
  assert.deepEqual(misaligned, [], "switch thumbs must have equal clearance at their top, bottom and outer edge");
  console.log("PASS settings switch thumbs are centered at 150% scaling");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
