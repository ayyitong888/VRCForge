import assert from "node:assert/strict";
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
    function Fixture() {
      const [input, setInput] = useState("");
      return <Composer input={input} setInput={setInput} sending={false} questionPromptVisible
        providerLabel="ChatGPT" model="gpt-6.1-sol" onSubmit={() => {}} onSwitchMode={() => {}} />;
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
  console.log("compact question composer centers placeholder and drafts with its controls");
} finally {
  await browser.close();
}
