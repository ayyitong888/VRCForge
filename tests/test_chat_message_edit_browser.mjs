import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { build } from "esbuild";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React, { useState } from "react";
    import { createRoot } from "react-dom/client";
    import { ConversationCard } from "./src/components/chat/conversation-card";
    import { initializeI18n } from "./src/i18n";
    const item = { id: "message", type: "user", text: "Original" };
    window.saved = [];
    function Fixture() {
      const [draft, setDraft] = useState(item.text);
      const [busy, setBusy] = useState(false);
      const [editing, setEditing] = useState(true);
      window.setBusy = setBusy;
      return <ConversationCard item={item} editing={editing} editingText={draft}
        editSaveDisabledReason={busy ? "A task is running" : ""} onEditTextChange={setDraft}
        onEditItemSave={() => window.saved.push(draft)} onEditItemCancel={() => setEditing(false)} />;
    }
    void initializeI18n().then(() => createRoot(document.getElementById("root")).render(<Fixture />));
  ` },
  bundle: true, write: false, format: "iife", platform: "browser", jsx: "automatic",
});

const browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
try {
  const page = await browser.newPage();
  await page.setContent('<div id="root" style="width:700px"></div>');
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  const editor = page.locator("textarea");
  await editor.waitFor();
  const cancel = page.getByRole("button", { name: "Cancel", exact: true });
  const save = page.getByRole("button", { name: "Save", exact: true });
  assert.ok((await cancel.boundingBox()).x < (await save.boundingBox()).x, "Cancel is left of Save");

  await editor.fill("Edited");
  await editor.press("Shift+Enter");
  assert.equal(await editor.inputValue(), "Edited\n");
  await editor.evaluate(node => node.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true })));
  assert.deepEqual(await page.evaluate(() => window.saved), [], "IME confirmation does not send");
  await editor.press("Enter");
  assert.deepEqual(await page.evaluate(() => window.saved), ["Edited\n"]);

  await page.evaluate(() => window.setBusy(true));
  await page.waitForFunction(() => document.querySelector("button[disabled]"));
  await editor.press("Enter");
  assert.deepEqual(await page.evaluate(() => window.saved), ["Edited\n"], "busy history cannot be resubmitted by keyboard");
  await cancel.click();
  assert.equal(await editor.count(), 0);
  console.log("PASS message editor: Cancel before Save, Enter sends, Shift+Enter and IME stay in the editor");
} finally { await browser.close(); }
