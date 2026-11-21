import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { build } from "esbuild";
import postcss from "postcss";
import tailwind from "tailwindcss";

const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import { useState } from "react";
    import { flushSync } from "react-dom";
    import { createRoot } from "react-dom/client";
    import { initializeI18n } from "./src/i18n";
    import { Composer } from "./src/components/chat/composer";
    import { useComposerDraft } from "./src/hooks/use-composer-draft";

    const image = { id: "image", name: "screenshot.png", type: "image/png", size: 68, payloadKind: "data_url",
      dataUrl: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j9t0AAAAASUVORK5CYII=" };
    const file = { id: "file", name: "notes.zip", type: "application/zip", size: 10, payloadKind: "vault_file" };
    const quote = id => ({ id: "selection-" + id, name: "Selected text", type: "text/plain", size: 1, payloadKind: "text", text: id });
    function Fixture() {
      const draft = useComposerDraft("order-fixture", "", true);
      const [questionPromptVisible, setQuestion] = useState(false);
      window.populateInOrder = question => flushSync(() => {
        setQuestion(question);
        draft.setAttachments([image, quote("first"), file, quote("last")]);
      });
      window.populateOutOfOrder = () => flushSync(() => {
        draft.setAttachments([]);
        const appendImage = draft.reserveAttachmentAppend();
        draft.reserveAttachmentAppend()(() => [quote("first")]);
        const appendFile = draft.reserveAttachmentAppend();
        draft.reserveAttachmentAppend()(() => [quote("last")]);
        appendFile(() => [file]);
        appendImage(() => [image]);
      });
      return <Composer input={draft.input} setInput={draft.setInput} attachments={draft.attachments}
        questionPromptVisible={questionPromptVisible} providerLabel="ChatGPT" model="Fixture model"
        sending={false} onSubmit={() => {}} onSwitchMode={() => {}}
        onRemoveAttachment={id => draft.setAttachments(current => current.filter(attachment => attachment.id !== id))} />;
    }
    void initializeI18n().then(() => createRoot(document.getElementById("root")).render(<Fixture />));
  ` },
  bundle: true, write: false, format: "iife", jsx: "automatic",
});
const css = await postcss([tailwind("tailwind.config.ts")]).process("@tailwind base; @tailwind utilities;", { from: undefined });
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
try {
  const page = await browser.newPage({ locale: "en-US" });
  await page.setContent('<main id="root"></main>');
  await page.addStyleTag({ content: css.css });
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  await page.waitForFunction(() => typeof window.populateInOrder === "function");
  const attachmentOrder = () => page.locator("form").evaluate(form => {
    const previews = Array.from(form.querySelectorAll('img[alt="screenshot.png"], [data-composer-selected-text-chip], [data-app-tooltip^="notes.zip ·"]'));
    const input = form.querySelector("textarea").getBoundingClientRect();
    const plus = form.querySelector("[data-composer-action-menu]").getBoundingClientRect();
    return {
      order: previews.map(node => node.matches("img") ? "image" : node.matches("[data-composer-selected-text-chip]") ? "quote" : "file"),
      aboveInput: previews.every(node => node.getBoundingClientRect().bottom <= input.top),
      controlsAlongsideInput: plus.top < input.bottom && plus.bottom > input.top,
    };
  });
  for (const [width, question] of [[900, false], [375, false], [900, true], [375, true]]) {
    await page.setViewportSize({ width, height: 600 });
    await page.evaluate(question => window.populateInOrder(question), question);
    const layout = await attachmentOrder();
    assert.deepEqual(layout.order, ["image", "quote", "file", "quote"]);
    assert.equal(layout.aboveInput, true, `all previews are above the input at ${width}px, question=${question}`);
    if (question) assert.equal(layout.controlsAlongsideInput, true);
  }
  await page.evaluate(() => window.populateOutOfOrder());
  assert.deepEqual((await attachmentOrder()).order, ["image", "quote", "file", "quote"], "slow reads retain the order in which items were added");
  await page.locator("[data-composer-selected-text-remove]").first().click();
  assert.deepEqual((await attachmentOrder()).order, ["image", "file", "quote"], "removing an earlier quote preserves later items");
  console.log("PASS composer attachment order, position, late reads and quote removal");
} finally {
  await browser.close();
}
