import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const temp = path.join(root, "tests/.tmp-chat-selection-quote");
const bundle = path.join(temp, "selection-quote.js");
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");

await fs.rm(temp, { recursive: true, force: true });
await fs.mkdir(temp, { recursive: true });
await build({
  entryPoints: [process.env.VRCFORGE_QUOTE_SOURCE || path.join(root, "src/lib/chat-selection-quote.ts")],
  outfile: bundle,
  bundle: true,
  format: "iife",
  globalName: "VRCForgeQuote",
  platform: "browser",
});

const browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
try {
  const page = await browser.newPage();
  await page.setContent(`
    <main>
      <article id="user-message" data-user-message-bubble data-chat-quote-content>Keep this exact sentence.</article>
      <article id="assistant-message"><div data-chat-quote-content>Assistant answer remains quotable.</div></article>
      <article id="assistant-process-message" data-agent-turn-process-group>
        <div id="assistant-commentary" data-chat-quote-content>Assistant commentary remains quotable.</div>
        <div id="tool-group" data-agent-tool-call-group><span id="tool-output">Tool output is not quotable.</span></div>
        <span id="process-status" data-chat-current-activity>Waiting for model</span>
      </article>
      <div id="activity" data-chat-current-activity role="status">正在等待模型</div>
      <div id="tool-status" data-agent-turn-process-group><span>正在读取素材</span></div>
      <button id="action">Stop run</button>
      <nav id="navigation">Settings</nav>
      <article data-chat-quote-content>
        <div id="code-toolbar" data-chat-selection-excluded>typescript<button id="nested-action">Copy code</button></div>
        <pre id="code-body">const answer = 42;</pre>
      </article>
      <textarea id="draft">Editable draft</textarea>
    </main>`);
  await page.addStyleTag({ content: await fs.readFile(process.env.VRCFORGE_QUOTE_STYLE_SOURCE || path.join(root, "src/styles.css"), "utf8") });
  await page.addScriptTag({ path: bundle });
  const getQuote = (selector, start, end) => page.evaluate(({ selector, start, end }) => {
    const element = document.querySelector(selector);
    const textNode = element?.firstChild;
    if (!textNode || textNode.nodeType !== Node.TEXT_NODE) throw new Error(`missing fixture text: ${selector}`);
    const range = document.createRange();
    range.setStart(textNode, start);
    range.setEnd(textNode, end);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    return window.VRCForgeQuote.conversationQuoteFromSelection(selection);
  }, { selector, start, end });

  const status = await getQuote("#activity", 0, "正在等待模型".length);
  const tool = await getQuote("#tool-status span", 0, "正在读取素材".length);
  const button = await getQuote("#action", 0, "Stop run".length);
  const navigation = await getQuote("#navigation", 0, "Settings".length);
  assert.equal(status, null, "live model state must not open the quote menu");
  assert.equal(tool, null, "tool status must not open the quote menu");
  assert.equal(button, null, "button labels must not open the quote menu");
  assert.equal(navigation, null, "navigation labels must not open the quote menu");
  assert.equal(await getQuote("#nested-action", 0, "Copy code".length), null, "controls nested in message prose remain excluded");
  assert.equal(await getQuote("#code-toolbar", 0, "typescript".length), null, "code toolbar labels are not message prose");
  const selectionStyles = await page.evaluate(() => Object.fromEntries(
    ["#navigation", "#activity", "#code-toolbar", "#nested-action", "#code-body", "#draft", "#user-message"]
      .map((selector) => [selector, getComputedStyle(document.querySelector(selector)).userSelect]),
  ));
  for (const selector of ["#navigation", "#activity", "#code-toolbar", "#nested-action"]) {
    assert.equal(selectionStyles[selector], "none", `${selector} must not be selectable UI text`);
  }
  for (const selector of ["#code-body", "#draft", "#user-message"]) {
    assert.equal(selectionStyles[selector], "text", `${selector} remains normally selectable`);
  }

  const userQuote = await getQuote("#user-message", 5, 9);
  const assistantQuote = await getQuote("#assistant-message [data-chat-quote-content]", 10, 16);
  const assistantCommentaryQuote = await getQuote("#assistant-commentary", 0, "Assistant commentary".length);
  const toolOutput = await getQuote("#tool-output", 0, "Tool output".length);
  const processStatus = await getQuote("#process-status", 0, "Waiting for model".length);
  assert.equal(userQuote.text, "this", "user message text remains selectable for quoting");
  assert.equal(assistantQuote.text, "answer", "assistant answer text remains selectable for quoting");
  assert.equal(assistantCommentaryQuote.text, "Assistant commentary", "assistant prose inside the process timeline remains quotable");
  assert.equal(toolOutput, null, "tool output inside the process group remains excluded");
  assert.equal(processStatus, null, "process status inside the process group remains excluded");

  const crossBoundary = await page.evaluate(() => {
    const start = document.querySelector("#user-message").firstChild;
    const end = document.querySelector("#assistant-message [data-chat-quote-content]").firstChild;
    const range = document.createRange();
    range.setStart(start, 0);
    range.setEnd(end, end.textContent.length);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    return window.VRCForgeQuote.conversationQuoteFromSelection(selection);
  });
  assert.equal(crossBoundary, null, "selection spanning multiple messages is not treated as a quote");
  console.log(JSON.stringify({ statusIgnored: true, toolIgnored: true, controlsIgnored: true, bodyQuotesPreserved: true, assistantCommentaryAllowed: true, nestedStatusIgnored: true, crossMessageRejected: true }));
} finally {
  await browser.close();
  await fs.rm(temp, { recursive: true, force: true });
}
