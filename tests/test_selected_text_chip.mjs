// Real Composer + production Tailwind styles. Screenshot quote replay; no app/provider calls.
import assert from "node:assert/strict";
import { readFileSync, mkdirSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const root = resolve(import.meta.dirname, "..");
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const quote = `给你三个不同风格的名字，都适合黑白配色的模型：

霜墨 | 清冷优雅
白如霜、黑如墨，适合气质冷淡、带一点神秘感的模型。

奥利奥 | 甜酷可爱
好记又有黑白联想，适合俏皮、软萌或街头风的模型。

昼夜之间 | 神秘梦幻
把黑白化作昼夜的交界，适合有幻想感、双面设定的模型。

我个人最推荐「霜墨」，简洁、有画面感，也比较耐听。`;
const quote2 = quote;
const compiled = await build({
  stdin: {
    resolveDir: root,
    loader: "tsx",
    contents: `
      import React, {useState} from 'react';
      import {createRoot} from 'react-dom/client';
      import {Composer,AttachmentStrip} from './src/components/chat/composer';
      import i18n from './src/i18n';
      import {initReactI18next} from 'react-i18next';
      import messages from './src/locales/en-US.json';
      i18n.use(initReactI18next).init({lng:'en-US',resources:{'en-US':{translation:messages}},interpolation:{escapeValue:false}});
      const selected = {id:'selection-bound-1',name:'Selected text',size:${quote.length},type:'text/plain',text:${JSON.stringify(quote)},payloadKind:'text'};
      const selected2 = {id:'selection-bound-2',name:'Selected text',size:${quote2.length},type:'text/plain',text:${JSON.stringify(quote2)},payloadKind:'text'};
      const regular = {id:'file-bound-1',name:'notes.txt',size:12,type:'text/plain',text:'notes',payloadKind:'text'};
      window.removed = [];
      window.edited = [];
      window.originalQuote = selected.text;
      function Fixture() {
        const [variant,setVariant] = useState('composer');
        window.setVariant = setVariant;
        if (variant !== 'composer') return <main data-history-quote style={{padding:'600px 16px 16px'}}><AttachmentStrip attachments={[selected]} compact={variant === 'compact'} /></main>;
        return <main style={{width:'100%', padding:'220px 16px 16px'}}><div data-chat-composer-dock className="bg-workspace/95"><Composer input="draft" setInput={()=>{}} sending={false}
          onSubmit={()=>{}} onSwitchMode={()=>{}} attachments={[selected,selected2,regular]}
          onRemoveAttachment={id=>window.removed.push(id)} /></div></main>;
      }
      createRoot(document.getElementById('root')).render(<Fixture/>);
    `,
  },
  bundle: true,
  write: false,
  format: "iife",
  platform: "browser",
  jsx: "automatic",
});
const css = (await postcss([tailwindcss(resolve(root, "tailwind.config.ts"))]).process(
  readFileSync(resolve(root, "src/styles.css"), "utf8").replace("@import \"./theme.css\";", readFileSync(resolve(root, "src/theme.css"), "utf8")),
  { from: resolve(root, "src/styles.css") },
)).css;
const server = createServer((req, res) => {
  if (req.url !== "/") { res.writeHead(404); res.end(); return; }
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.end(`<style>${css}</style><div id="root"></div><script>${compiled.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
});
let browser;
try {
  await new Promise(done => server.listen(0, "127.0.0.1", done));
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 375, height: 900 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const chip = page.locator("[data-composer-selected-text-chip]");
  const textarea = page.locator("textarea");
  const regular = page.getByText("notes.txt");
  await page.waitForTimeout(250);
  if (!await chip.count()) throw new Error(`Composer fixture did not mount: ${errors.join(" | ")} HTML=${await page.locator("#root").innerHTML()}`);
  const normalDockBackground = await page.locator("[data-chat-composer-dock]").evaluate((element) => getComputedStyle(element).backgroundColor);
  assert.notEqual(normalDockBackground, "rgba(0, 0, 0, 0)", "without wallpaper the composer dock keeps its normal themed surface");
  const wallpaperSurface = await page.evaluate(() => {
    document.documentElement.dataset.vrcforgeWallpaper = "active";
    document.documentElement.dataset.vrcforgeWallpaperScope = "workspace";
    const dock = document.querySelector("[data-chat-composer-dock]");
    const dropzone = document.querySelector("[data-vrcforge-composer-dropzone]");
    const card = dropzone?.querySelector(":scope > .rounded-3xl");
    return {
      dropzoneMatched: Boolean(dropzone),
      cardMatched: Boolean(card),
      light: card ? getComputedStyle(card).backgroundColor : "",
      rootTheme: document.documentElement.dataset.theme || "light",
      palette: document.documentElement.dataset.vrcforgePalette || "default",
      cardSelector: Boolean(card?.matches("[data-vrcforge-composer-dropzone] > .rounded-3xl")),
      cardClasses: card?.className || "",
      dock: dock ? getComputedStyle(dock).backgroundColor : "",
    };
  });
  assert.deepEqual(wallpaperSurface.dropzoneMatched && wallpaperSurface.cardMatched, true, "wallpaper composer selectors match the real DOM");
  assert.equal(wallpaperSurface.cardSelector, true, "wallpaper card selector matches the real child node");
  assert.equal(wallpaperSurface.palette, "default", "default theme variables are active in the fixture");
  assert.equal(wallpaperSurface.dock, "rgba(0, 0, 0, 0)", "wallpaper composer dock is transparent so it does not form an outer rectangle");
  assert.notEqual(wallpaperSurface.light, "rgba(0, 0, 0, 0)", "light wallpaper composer card retains a readable translucent surface");
  await page.evaluate(() => {
    document.documentElement.dataset.theme = "dark";
    document.documentElement.classList.add("dark");
    document.documentElement.dataset.vrcforgePalette = "sunset";
  });
  // The real Composer transitions its surface color over 150 ms.
  await page.waitForTimeout(250);
  const darkSurface = await page.evaluate(() => {
    const dock = document.querySelector("[data-chat-composer-dock]");
    const dropzone = document.querySelector("[data-vrcforge-composer-dropzone]");
    const card = document.querySelector("[data-vrcforge-composer-dropzone] > .rounded-3xl");
    return {
      rootClass: document.documentElement.className,
      theme: document.documentElement.dataset.theme,
      workspace: getComputedStyle(document.documentElement).getPropertyValue("--workspace").trim(),
      dock: dock ? getComputedStyle(dock).backgroundColor : "",
      dropzone: dropzone ? getComputedStyle(dropzone).backgroundColor : "",
      card: card ? getComputedStyle(card).backgroundColor : "",
    };
  });
  assert.match(darkSurface.rootClass, /dark/);
  assert.equal(darkSurface.theme, "dark");
  assert.ok(darkSurface.workspace, "dark theme workspace variable is resolved");
  assert.equal(darkSurface.dock, "rgba(0, 0, 0, 0)", "dark wallpaper dock is transparent so it does not form an outer rectangle");
  assert.notEqual(darkSurface.dropzone, "rgba(0, 0, 0, 0)", "dark wallpaper dropzone has a computed surface");
  assert.notEqual(darkSurface.card, "rgba(0, 0, 0, 0)", "dark wallpaper composer card retains a readable translucent surface");
  assert.notEqual(darkSurface.card, wallpaperSurface.light, "dark palette changes the card surface after its transition");
  console.log(JSON.stringify({ wallpaperSurface, darkSurface }));
  const darkScreenshot = process.env.VRCFORGE_SELECTED_TEXT_CHIP_DARK_SCREENSHOT;
  if (darkScreenshot) {
    mkdirSync(resolve(darkScreenshot, ".."), { recursive: true });
    await page.screenshot({ path: darkScreenshot });
  }
  await page.evaluate(() => {
    document.documentElement.classList.remove('dark');
    document.documentElement.dataset.theme = 'light';
    delete document.documentElement.dataset.vrcforgePalette;
    delete document.documentElement.dataset.vrcforgeWallpaper;
    delete document.documentElement.dataset.vrcforgeWallpaperScope;
  });
  await page.waitForTimeout(250);
  assert.equal(await regular.count(), 1, "regular attachment remains in the attachment strip");
  for (const width of [375, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    const geometry = await chip.evaluate(element => {
      const rect = element.getBoundingClientRect();
      const trigger = element.querySelector("[data-composer-selected-text-trigger]");
      const triggerRect = trigger.getBoundingClientRect();
      return {
        aboveTextarea: rect.bottom <= document.querySelector("textarea").getBoundingClientRect().top,
        oneLine: triggerRect.height < 42 && trigger.scrollHeight <= trigger.clientHeight + 1,
        fitsViewport: rect.right <= window.innerWidth + 1,
      };
    });
    assert.deepEqual(geometry, { aboveTextarea: true, oneLine: true, fitsViewport: true }, `selected text chip layout at ${width}px`);
  }
  const label = page.locator("[data-composer-selected-text-trigger]");
  await label.hover();
  const preview = chip.locator('[aria-hidden="true"]');
  const combinedQuote = `${quote}\n\n${quote2}`;
  // Move from the chip through the gap and onto its preview using real pointer events.
  const anchorBox = await label.boundingBox();
  const previewBox = await preview.boundingBox();
  const pointerX = anchorBox.x + 20;
  const below = previewBox.y >= anchorBox.y + anchorBox.height;
  await page.mouse.move(pointerX, below ? anchorBox.y + anchorBox.height + 4 : anchorBox.y - 4);
  await page.mouse.move(pointerX, below ? previewBox.y + 24 : previewBox.y + previewBox.height - 24, { steps: 8 });
  assert.equal(await preview.count(), 1, "entering the quote preview must not dismiss it");
  assert.equal(await preview.innerText(), combinedQuote, "both complete original quotes remain readable");
  const visibleBox = await preview.boundingBox();
  assert.ok(visibleBox.y >= 8 && visibleBox.y + visibleBox.height <= 892, "long preview stays inside the viewport");
  await page.mouse.wheel(0, 180);
  await page.waitForTimeout(150);
  assert.ok(await preview.evaluate(el => el.scrollTop) > 0, "wheel scrolls the read-only preview");
  const scrollMetrics = await preview.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
    overflowY: getComputedStyle(element).overflowY,
    track: getComputedStyle(element, "::-webkit-scrollbar-track").backgroundColor,
  }));
  assert.equal(scrollMetrics.overflowY, "auto", "preview owns vertical scrolling");
  assert.ok(scrollMetrics.scrollHeight > scrollMetrics.clientHeight, "long selected text creates a scrollable preview");
  assert.equal(scrollMetrics.track, "rgba(0, 0, 0, 0)", "preview scrollbar track follows its card surface");
  await label.click();
  assert.equal(await chip.locator('button').count(), 1, "only the remove control is a button");
  assert.equal(await chip.locator('textarea, input, [contenteditable="true"], [role="dialog"]').count(), 0, "clicking a quote never opens an editor");
  assert.equal(await label.evaluate(el => el.tabIndex), -1, "quote label is not a clickable keyboard control");
  assert.equal(await preview.evaluate(el => getComputedStyle(el).pointerEvents), "auto", "read-only preview accepts pointer scrolling");
  await preview.click();
  assert.equal(await chip.locator('textarea, input, [contenteditable="true"], [role="dialog"]').count(), 0, "preview text has no edit or navigation action");
  assert.equal(await page.evaluate(() => window.originalQuote), quote, "source binding retains the original quote");
  assert.deepEqual(await page.evaluate(() => window.edited), [], "quote remains unchanged");
  const screenshot = process.env.VRCFORGE_SELECTED_TEXT_CHIP_SCREENSHOT;
  if (screenshot) {
    mkdirSync(resolve(screenshot, ".."), { recursive: true });
    await page.screenshot({ path: screenshot });
  }
  await page.mouse.move(760, 850);
  assert.equal(await preview.count(), 0, "moving away dismisses the preview");
  await page.locator("[data-composer-selected-text-remove]").click();
  assert.deepEqual(await page.evaluate(() => window.removed), ["selection-bound-1", "selection-bound-2"], "inline remove targets both selected-text attachments");
  for (const variant of ['compact', 'full']) {
    await page.evaluate(variant => window.setVariant(variant), variant);
    const historyLabel = page.locator('[data-history-quote] .inline-flex').first();
    await historyLabel.hover();
    const historyPreview = page.locator('[data-history-quote] [aria-hidden="true"]');
    const anchor = await historyLabel.boundingBox();
    const box = await historyPreview.boundingBox();
    await page.mouse.move(anchor.x + 20, anchor.y - 4);
    await page.mouse.move(anchor.x + 20, box.y + box.height - 20, {steps: 6});
    assert.equal(await historyPreview.count(), 1, `${variant}: historical quote remains open when entered`);
    assert.equal(await historyPreview.innerText(), quote, `${variant}: complete historical quote is preserved`);
    await page.mouse.wheel(0, 160);
    await page.waitForTimeout(150);
    assert.ok(await historyPreview.evaluate(el => el.scrollTop) > 0, `${variant}: historical quote scrolls`);
    await page.mouse.move(760, 850);
    assert.equal(await historyPreview.count(), 0, `${variant}: leaving preview dismisses it`);
  }
  assert.deepEqual(errors, []);
  console.log("PASS: selected-text chip at 375/768px; inert label, hover preview, unchanged quote and remove-only interaction verified.");
} finally {
  await browser?.close();
  await new Promise(done => server.close(done));
}
