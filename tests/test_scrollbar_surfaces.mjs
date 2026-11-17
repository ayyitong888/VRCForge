import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const root = resolve(import.meta.dirname, "..");
const themePath = process.argv[2] || process.env.VRCFORGE_SCROLLBAR_THEME_SOURCE || resolve(root, "src/theme.css");
const source = readFileSync(resolve(root, "src/styles.css"), "utf8")
  .replace('@import "./theme.css";', readFileSync(themePath, "utf8"));
const css = (await postcss([tailwindcss(resolve(root, "tailwind.config.ts"))]).process(source, {
  from: resolve(root, "src/styles.css"),
})).css;
const browser = await chromium.launch({
  headless: true,
  channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge",
  ignoreDefaultArgs: ["--hide-scrollbars"],
});

const html = `
  <style>${css}</style>
  <div id="root"><main><div id="surface-root">
    <div id="generic" class="surface overflow-auto" data-surface="generic"><div class="content"></div></div>
    <div id="app" class="app-scrollbar surface" data-surface="app"><div class="content"></div></div>
    <div id="sidebar" class="sidebar-scrollbar surface bg-sidebar" data-surface="sidebar"><div class="content"></div></div>
    <div id="quote" class="surface" data-surface="quote"><div class="content"></div></div>
    <pre id="code" class="surface"><code class="content"></code></pre>
    <textarea id="input" class="surface">line one\nline two\nline three\nline four\nline five\nline six\nline seven\nline eight\nline nine\nline ten</textarea>
  </div></main></div>`;

function surfaceSnapshot(page) {
  return page.evaluate(() => {
    const ids = ["generic", "app", "sidebar", "quote", "code", "input"];
    const read = (id) => {
      const element = document.getElementById(id);
      const style = getComputedStyle(element);
      return {
        track: getComputedStyle(element, "::-webkit-scrollbar-track").backgroundColor,
        corner: getComputedStyle(element, "::-webkit-scrollbar-corner").backgroundColor,
        button: getComputedStyle(element, "::-webkit-scrollbar-button").backgroundColor,
        thumbBorder: getComputedStyle(element, "::-webkit-scrollbar-thumb").borderTopColor,
        thumb: getComputedStyle(element, "::-webkit-scrollbar-thumb").backgroundColor,
        scrollbarColor: style.scrollbarColor,
        background: style.backgroundColor,
        overflowX: style.overflowX,
        overflowY: style.overflowY,
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
        scrollHeight: element.scrollHeight,
        clientHeight: element.clientHeight,
      };
    };
    return Object.fromEntries(ids.map((id) => [id, read(id)]));
  });
}

try {
  const page = await browser.newPage({ viewport: { width: 720, height: 640 } });
  await page.setContent(html);
  await page.addStyleTag({ content: `
    body { margin: 0; }
    #surface-root { display: grid; grid-template-columns: repeat(2, 260px); gap: 12px; padding: 12px; }
    .surface { width: 260px; height: 150px; overflow: auto; background: hsl(var(--card)); }
    .content { display: block; width: 520px; height: 420px; background: hsl(var(--card)); }
    #sidebar { background: hsl(var(--sidebar)); }
  ` });
  const check = async (mode) => {
    if (mode.endsWith("dark")) {
      await page.evaluate(() => { document.documentElement.classList.add("dark"); document.documentElement.dataset.theme = "dark"; });
    } else {
      await page.evaluate(() => { document.documentElement.classList.remove("dark"); document.documentElement.dataset.theme = "light"; });
    }
    await page.evaluate((palette) => {
      if (palette === "default") delete document.documentElement.dataset.vrcforgePalette;
      else document.documentElement.dataset.vrcforgePalette = palette;
      document.documentElement.style.setProperty("--vrcforge-custom-accent-h", "143");
      document.documentElement.style.setProperty("--vrcforge-custom-accent-s", "70%");
      document.documentElement.style.setProperty("--vrcforge-custom-accent-light-l", "42%");
      document.documentElement.style.setProperty("--vrcforge-custom-accent-dark-l", "62%");
    }, mode.split("-")[0]);
    const snapshot = await surfaceSnapshot(page);
    const expectedThumb = await page.evaluate(() => {
      const probe = document.createElement("span");
      probe.style.color = "color-mix(in srgb, hsl(var(--primary)) 55%, transparent)";
      document.body.append(probe);
      const color = getComputedStyle(probe).color;
      probe.remove();
      return color;
    });
    for (const [surface, style] of Object.entries(snapshot)) {
      assert.equal(style.thumb, expectedThumb, `${mode}: ${surface} thumb follows the theme accent`);
    }
    assert.equal(snapshot.generic.track, "rgba(0, 0, 0, 0)", `${mode}: generic track follows surface`);
    assert.equal(snapshot.generic.corner, "rgba(0, 0, 0, 0)", `${mode}: generic corner follows surface`);
    assert.equal(snapshot.generic.scrollbarColor, "auto", `${mode}: Chromium uses the shared thumb hover styling`);
    assert.equal(snapshot.app.track, "rgba(0, 0, 0, 0)", `${mode}: app track follows surface`);
    assert.equal(snapshot.app.corner, "rgba(0, 0, 0, 0)", `${mode}: app corner follows surface`);
    assert.equal(snapshot.quote.track, "rgba(0, 0, 0, 0)", `${mode}: quote track follows overlay surface`);
    assert.equal(snapshot.quote.corner, "rgba(0, 0, 0, 0)", `${mode}: quote corner follows overlay surface`);
    assert.equal(snapshot.generic.button, "rgba(0, 0, 0, 0)", `${mode}: generic button is transparent`);
    assert.equal(snapshot.generic.thumbBorder, "rgba(0, 0, 0, 0)", `${mode}: generic thumb border is transparent`);
    assert.equal(snapshot.sidebar.track, "rgba(0, 0, 0, 0)", `${mode}: sidebar track follows its surface`);
    assert.equal(snapshot.sidebar.corner, "rgba(0, 0, 0, 0)", `${mode}: sidebar corner follows its surface`);
    assert.equal(snapshot.sidebar.button, "rgba(0, 0, 0, 0)", `${mode}: sidebar button is transparent`);
    assert.equal(snapshot.sidebar.thumbBorder, "rgba(0, 0, 0, 0)", `${mode}: sidebar thumb border is transparent`);
    assert.ok(snapshot.generic.scrollWidth > snapshot.generic.clientWidth, `${mode}: generic horizontal overflow exists`);
    assert.ok(snapshot.generic.scrollHeight > snapshot.generic.clientHeight, `${mode}: generic vertical overflow exists`);
    return snapshot;
  };
  const snapshots = {};
  for (const mode of ["default-light", "default-dark", "sunset-light", "sunset-dark", "custom-light", "custom-dark"]) {
    snapshots[mode] = await check(mode);
  }
  assert.notEqual(snapshots["default-light"].generic.thumb, snapshots["sunset-light"].generic.thumb);
  assert.notEqual(snapshots["sunset-light"].generic.thumb, snapshots["custom-light"].generic.thumb);
  await check("sunset-light");
  const generic = page.locator("#generic");
  const widthWithOverflow = await generic.evaluate((element) => element.clientWidth);
  await generic.evaluate((element) => { element.firstElementChild.hidden = true; });
  assert.equal(await generic.evaluate((element) => element.clientWidth), widthWithOverflow,
    "the content width stays put when the scrollbar appears or disappears");
  await generic.evaluate((element) => { element.firstElementChild.hidden = false; });
  const bounds = await generic.boundingBox();
  const gutterX = bounds.x + bounds.width - 10;
  const middleY = bounds.y + 22;
  const paintedThumb = async (y) => {
    const clip = { x: gutterX, y, width: 10, height: 1 };
    const data = (await page.screenshot({ clip })).toString("base64");
    return page.evaluate(async (base64) => {
      const image = new Image();
      image.src = "data:image/png;base64," + base64;
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = image.width;
      canvas.height = 1;
      const context = canvas.getContext("2d");
      context.drawImage(image, 0, 0);
      const pixels = context.getImageData(0, 0, image.width, 1).data;
      const shades = Array.from({ length: image.width }, (_, x) => Array.from(pixels.slice(x * 4, x * 4 + 3)).join(","));
      return { width: shades.filter((pixel) => pixel !== shades[0]).length, color: shades[5] };
    }, data);
  };
  const normal = await paintedThumb(middleY);
  await page.mouse.move(gutterX + 5, middleY);
  const hovered = await paintedThumb(middleY);
  assert.ok(normal.width > 0 && normal.width < hovered.width, "hover thickens the painted thumb");
  assert.notEqual(normal.color, hovered.color, "hover paints the exact accent instead of blending with the surface");
  assert.equal(await generic.evaluate((element) => element.clientWidth), widthWithOverflow, "hover keeps the same gutter");
  await page.mouse.down();
  await page.mouse.move(gutterX - 20, middleY + 18);
  const dragged = await paintedThumb(middleY + 18);
  await page.mouse.up();
  assert.ok(await generic.evaluate((element) => element.scrollTop > 0), "the thumb remains draggable");
  assert.deepEqual(dragged, hovered, "drag keeps the same solid, thick appearance after leaving the gutter");
  await page.evaluate(() => {
    document.documentElement.dataset.vrcforgeWallpaper = "active";
    document.documentElement.dataset.vrcforgeWallpaperScope = "app";
  });
  const wallpaper = await surfaceSnapshot(page);
  assert.equal(wallpaper.sidebar.background, "rgba(0, 0, 0, 0)", "app wallpaper makes the nested sidebar surface transparent");
  assert.equal(wallpaper.sidebar.track, "rgba(0, 0, 0, 0)", "app wallpaper nested sidebar keeps transparent track");
  assert.equal(wallpaper.sidebar.corner, "rgba(0, 0, 0, 0)", "app wallpaper nested sidebar keeps transparent corner");
  const dashboardCss = readFileSync(resolve(root, "dashboard/styles.css"), "utf8").replace(/^@import .+;\r?\n/, "");
  await page.setContent(`<style>${dashboardCss}</style><pre id="code" style="height:20px;overflow:auto">one\ntwo\nthree</pre>`);
  const dashboard = await page.locator("#code").evaluate((element) => {
    element.style.color = "color-mix(in srgb, var(--md-primary) 55%, transparent)";
    return {
      thumb: getComputedStyle(element, "::-webkit-scrollbar-thumb").backgroundColor,
      expectedThumb: getComputedStyle(element).color,
      track: getComputedStyle(element, "::-webkit-scrollbar-track").backgroundColor,
      overflow: element.scrollHeight > element.clientHeight,
    };
  });
  assert.equal(dashboard.thumb, dashboard.expectedThumb, "the dashboard uses its own theme accent");
  assert.equal(dashboard.track, "rgba(0, 0, 0, 0)", "the dashboard track follows its surface");
  assert.ok(dashboard.overflow, "the dashboard code block scrolls");
  console.log("PASS unified scrollbars on desktop surfaces, inputs, code, and the dashboard");
} finally {
  await browser.close();
}
