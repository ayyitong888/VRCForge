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
      <div id="quote-menu"><button id="quote-action">Add to chat</button></div>
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

  await getQuote("#user-message", 0, 4);
  await page.evaluate(() => {
    const menu = document.querySelector("#quote-menu");
    window.VRCForgeQuote.dismissConversationQuoteOnOutsidePointerDown?.(menu, () => {
      window.getSelection().removeAllRanges();
      menu.hidden = true;
    });
    document.querySelector("#action").addEventListener("pointerdown", () => {
      document.body.dataset.selectionOnPointerDown = window.getSelection().toString();
    });
    document.querySelector("#quote-action").addEventListener("click", () => {
      menu.dataset.actionInvoked = "true";
    });
  });
  await page.locator("#quote-action").click();
  assert.equal(await page.locator("#quote-menu").getAttribute("data-action-invoked"), "true");
  await getQuote("#user-message", 0, 4);
  await page.locator("#action").click();
  assert.equal(await page.locator("body").getAttribute("data-selection-on-pointer-down"), "",
    "the first press outside clears the quote before the clicked control runs");
  assert.equal(await page.locator("#quote-menu").isVisible(), false);

  for (const [primary, expected] of [["22 100% 50%", "rgb(255, 94, 0)"], ["120 100% 25%", "rgb(0, 128, 0)"]]) {
    const colors = await page.evaluate((accent) => {
      document.documentElement.style.setProperty("--primary", accent);
      document.documentElement.style.setProperty("--primary-foreground", "0 0% 100%");
      const message = getComputedStyle(document.querySelector("#user-message"), "::selection");
      const code = getComputedStyle(document.querySelector("#code-body"), "::selection");
      return [message.backgroundColor, message.color, code.backgroundColor];
    }, primary);
    assert.deepEqual(colors, [expected, "rgb(255, 255, 255)", expected]);
  }
  console.log("PASS quote selection, first outside press, toolbar action, and theme-following highlights");
} finally {
  await browser.close();
  await fs.rm(temp, { recursive: true, force: true });
}
