import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import { build } from "esbuild";
import postcss from "postcss";
import tailwind from "tailwindcss";

const css = (await postcss([tailwind("tailwind.config.ts")]).process(
  "@tailwind base; @tailwind utilities;\n" + await readFile("src/theme.css", "utf8"),
  { from: undefined },
)).css;
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React, { useState } from "react";
    import { createRoot } from "react-dom/client";
    import { Composer } from "./src/components/chat/composer";
    import { initializeI18n } from "./src/i18n";
    window.submissions = 0;
    function Fixture() {
      const [input, setInput] = useState("");
      const [layout, setLayout] = useState({ questionPromptVisible: true, providerLabel: "ChatGPT", model: "gpt-6.1-sol" });
      window.setComposerLayout = (patch) => setLayout(current => ({ ...current, ...patch }));
      return <Composer input={input} setInput={setInput} sending={false} {...layout}
        onSubmit={event => { event?.preventDefault(); window.submissions += 1; }} onSwitchMode={() => {}} />;
    }
    void initializeI18n().then(() => createRoot(document.getElementById("root")).render(<Fixture />));
  ` },
  bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
});

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const browser = await chromium.launch({ headless: true, channel: "msedge" });
try {
  const page = await browser.newPage({ viewport: { width: 800, height: 600 } });
  await page.setContent('<main id="root"></main>');
  await page.addStyleTag({ content: css });
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  const input = page.locator("textarea");
  await input.waitFor();
  const geometry = () => input.evaluate((element) => {
    const centerY = (node) => {
      const rect = node.getBoundingClientRect();
      return rect.top + rect.height / 2;
    };
    const style = getComputedStyle(element);
    const lineHeight = parseFloat(style.lineHeight);
    const lines = element.value.split("\n").length;
    const textCenter = element.getBoundingClientRect().top + parseFloat(style.paddingTop) + lines * lineHeight / 2;
    const form = element.closest("form");
    return {
      textCenter, lineHeight, height: element.offsetHeight,
      plusCenter: centerY(form.querySelector("[data-composer-action-menu] svg")),
      modelCenter: centerY(form.querySelector('[title="ChatGPT · gpt-6.1-sol"]')),
    };
  });
  const assertCentered = async (label) => {
    const { textCenter, plusCenter, modelCenter, ...size } = await geometry();
    assert.ok(Math.abs(textCenter - plusCenter) <= 1, `${label} is ${textCenter - plusCenter}px from the add icon center`);
    assert.ok(Math.abs(textCenter - modelCenter) <= 1, `${label} is ${textCenter - modelCenter}px from the model label center`);
    return size;
  };
  const oneLine = await assertCentered("placeholder");
  await input.fill("Short message");
  assert.equal((await assertCentered("single-line draft")).height, oneLine.height);
  await input.fill("First line\nSecond line");
  const twoLines = await assertCentered("two-line draft");
  assert.equal(twoLines.height, oneLine.height + twoLines.lineHeight, "the textarea still grows by one line");
  await input.fill("");
  await page.evaluate(() => window.setComposerLayout({ questionPromptVisible: false }));
  const regularHeight = () => input.evaluate((element) => element.offsetHeight);
  await page.waitForFunction(() => !document.querySelector("textarea").className.includes("row-start-1"));
  assert.equal(await regularHeight(), oneLine.height, "an empty ordinary composer uses one line");
  await input.fill("First line\nSecond line");
  assert.equal(await regularHeight(), twoLines.height, "ordinary drafts grow with their contents");
  await input.fill(Array.from({ length: 30 }, () => "Long draft").join("\n"));
  assert.equal(await regularHeight(), 128, "ordinary composer stops growing and scrolls");
  await input.fill("");
  assert.equal(await regularHeight(), oneLine.height, "clearing a draft restores the compact height");
  console.log("question and ordinary composers fit their drafts and keep compact empty states");
  console.log("compact question composer centers placeholder and drafts with its controls");

  await page.setViewportSize({ width: 1360, height: 900 });
  await test("long model label cannot cover Send in a narrow desktop content pane", async () => {
    const providerLabel = "A deliberately long provider display name";
    const model = "example-model-with-long-provider-qualified-identifier-20261116";
    await page.evaluate(({ providerLabel, model }) => {
      document.getElementById("root").style.cssText = "width:370px;margin:440px 0 0 440px";
      window.setComposerLayout({ questionPromptVisible: false, providerLabel, model });
    }, { providerLabel, model });
    await page.locator(`span[title="${providerLabel} · ${model}"]`).waitFor();
    await input.fill("send from narrow pane");
    const send = page.locator("[data-composer-send]");
    const geometry = await send.evaluate(button => {
      const rect = button.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
      const form = button.closest("form");
      return { reachable: button === hit || button.contains(hit), overflow: form.scrollWidth - form.clientWidth };
    });
    assert.ok(geometry.reachable, `Send is covered; composer overflow=${geometry.overflow}px`);
    assert.ok(geometry.overflow <= 1, `model label overflows by ${geometry.overflow}px`);
    await send.click();
    assert.equal(await page.evaluate(() => window.submissions), 1);
  });

  await test("pending-question permission menu opens toward the available content", async () => {
    await page.evaluate(() => {
      document.getElementById("root").style.cssText = "width:360px;margin:440px 0 0 440px";
      window.setComposerLayout({ questionPromptVisible: true, providerLabel: "ChatGPT", model: "gpt-6.1-sol" });
    });
    await page.locator('span[title="ChatGPT · gpt-6.1-sol"]').waitFor();
    const permission = page.locator("button:has(svg.lucide-shield)");
    await permission.click();
    const menu = permission.locator("..").locator("div.absolute");
    const menuRect = await menu.boundingBox();
    const formRect = await page.locator("form").boundingBox();
    assert.ok(menuRect.x >= formRect.x - 1, "permission menu must remain inside the content's left edge");
    assert.ok(menuRect.x + menuRect.width <= formRect.x + formRect.width + 1,
      `permission menu overflows content by ${menuRect.x + menuRect.width - formRect.x - formRect.width}px`);
    assert.ok(menuRect.y >= 0 && menuRect.y + menuRect.height <= page.viewportSize().height,
      "permission menu must remain visible vertically");
    assert.equal(await menu.locator("button").count(), 3);
  });
} finally {
  await browser.close();
}
