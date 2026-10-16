import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { mkdtempSync } from "node:fs";
import { resolve } from "node:path";
import { tmpdir } from "node:os";
import { createRequire } from "node:module";
import Module from "node:module";
import { build } from "esbuild";
import { execFileSync } from "node:child_process";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const root = resolve(import.meta.dirname, "..");
const originalReplyPath = process.env.VRCFORGE_MARKDOWN_REPLY_FIXTURE;
const evidenceDir = process.env.VRCFORGE_MARKDOWN_EVIDENCE_DIR;
const defaultReply = `给你三个不同风格的名字：

1. **霜墨**｜清冷优雅${"  "}
   白如霜、黑如墨，适合气质冷淡。

2. **昼夜之间**｜神秘梦幻${"  "}
   把黑白化作昼夜的交界。

我个人最推荐 **「霜墨」**。`;

const fixtureSource = `
  import React from "react";
  import { renderToStaticMarkup } from "react-dom/server";
  import { ChatMarkdown } from "./src/components/chat/chat-markdown";
  export function render(text, variant) {
    return renderToStaticMarkup(React.createElement(ChatMarkdown, {text, variant}));
  }
`;

async function renderMarkup(replyOverride) {
  const compiled = await build({
    stdin: { resolveDir: root, loader: "tsx", contents: fixtureSource },
    bundle: true,
    write: false,
    platform: "node",
    format: "cjs",
    external: ["react", "react-dom/server", "react/jsx-runtime", "i18next", "react-i18next"],
    jsx: "automatic",
    loader: { ".css": "empty" },
  });
  const filename = resolve(root, "tests", "chat-markdown-spacing-fixture.cjs");
  const fixture = new Module(filename);
  fixture.paths = Module._nodeModulePaths(root);
  fixture._compile(compiled.outputFiles[0].text, filename);
  const reply = replyOverride ?? (originalReplyPath ? readFileSync(originalReplyPath, "utf8") : defaultReply);
  return { agent: fixture.exports.render(reply, "agent"), user: fixture.exports.render(reply, "user") };
}

const markup = await renderMarkup();
assert.match(markup.agent, /<br\/?>(?:\n|\s)*白如霜/, "the reply must exercise a Markdown hard break");
assert.match(markup.agent, /chat-markdown-agent/, "the real ChatMarkdown component must render");

const browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
try {
  const page = await browser.newPage({ viewport: { width: 900, height: 700 }, deviceScaleFactor: 1 });
  const tempParent = resolve(tmpdir());
  const tempDir = mkdtempSync(resolve(tempParent, "vrcforge-markdown-spacing-css-"));
  const cssPath = resolve(tempDir, "app.css");
  let css;
  try {
    execFileSync(process.execPath, [resolve(root, "node_modules/tailwindcss/lib/cli.js"), "-i", resolve(root, "src/styles.css"), "-o", cssPath], { cwd: root, stdio: "ignore" });
    css = readFileSync(cssPath, "utf8");
  } finally {
    const target = resolve(tempDir);
    if (target !== tempParent && target.startsWith(`${tempParent}${process.platform === "win32" ? "\\" : "/"}`)) {
      rmSync(target, { recursive: true, force: true });
    }
  }
  await page.setContent(`<style>${css}</style><style>* { box-sizing: border-box; } body { margin: 0; background: white; } main { width: 640px; }</style><main id="agent" class="text-sm">${markup.agent}</main><main id="user" class="text-sm">${markup.user}</main>`);
  const geometry = await page.evaluate(() => {
    const measure = (selector) => {
      const root = document.querySelector(selector);
      const strong = [...root.querySelectorAll("strong")].find((node) => node.textContent?.includes("霜墨"));
      const textNode = [...root.querySelectorAll("p")].flatMap((p) => [...p.childNodes]).find((node) => node.nodeType === Node.TEXT_NODE && node.textContent?.includes("白如霜"));
      if (!strong || !textNode) throw new Error(`fixture anchors missing for ${selector}`);
      const titleRange = document.createRange(); titleRange.selectNodeContents(strong);
      const nextRange = document.createRange(); const start = textNode.textContent.indexOf("白如霜"); nextRange.setStart(textNode, start); nextRange.setEnd(textNode, start + 3);
      const title = titleRange.getBoundingClientRect();
      const next = nextRange.getBoundingClientRect();
      return { titleTop: title.top, nextTop: next.top, lineDelta: next.top - title.top, lineHeight: parseFloat(getComputedStyle(root.querySelector("p")).lineHeight), html: root.innerHTML };
    };
    return { agent: measure("#agent"), user: measure("#user") };
  });
  assert.equal(geometry.agent.lineDelta, geometry.agent.lineHeight, "agent hard break must occupy exactly one line");
    const softlineMarkup = await renderMarkup("soft one\nsoft two");
    await page.setContent(`<style>${css}</style><style>* { box-sizing: border-box; } body { margin: 0; } main { width: 640px; }</style><main id="agent" class="text-sm">${softlineMarkup.agent}</main><main id="user" class="text-sm">${softlineMarkup.user}</main>`);
    const softline = await page.evaluate(() => ({
      agent: document.querySelector("#agent p").getBoundingClientRect().height,
      user: document.querySelector("#user p").getBoundingClientRect().height,
    }));
    assert.ok(softline.user > softline.agent, `user soft newlines remain visible while agent soft newlines collapse (${softline.user} vs ${softline.agent})`);
    const codeMarkup = (await renderMarkup("```\nline 1\nline 2\n```")).agent;
    await page.setContent(`<style>${css}</style><main id="code" class="text-sm">${codeMarkup}</main>`);
    assert.equal(await page.locator("#code pre").textContent(), "line 1\nline 2\n", "code block preserves exact text whitespace");
    const listMarkup = (await renderMarkup("1. one\n2. two\n\n- alpha\n- beta")).agent;
    await page.setContent(`<style>${css}</style><main id="lists" class="text-sm">${listMarkup}</main>`);
    const listStyles = await page.evaluate(() => ({ ordered: getComputedStyle(document.querySelector("ol")).listStyleType, unordered: getComputedStyle(document.querySelector("ul")).listStyleType }));
    assert.equal(listStyles.ordered, "decimal", "ordered list numbering remains decimal");
    assert.equal(listStyles.unordered, "disc", "unordered list markers remain disc");

  await page.setContent(`<style>${css}</style><style>* { box-sizing: border-box; } body { margin: 0; background: white; } main { width: 640px; }</style><main id="agent" class="text-sm">${markup.agent}</main><main id="user" class="text-sm">${markup.user}</main>`);
  if (!evidenceDir) {
    console.log(`chat markdown spacing green: agent ${geometry.agent.lineDelta}px (no evidence directory requested)`);
  } else {
    mkdirSync(evidenceDir, { recursive: true });
    const mode = "green";
    writeFileSync(resolve(evidenceDir, `geometry-${mode}.json`), JSON.stringify({ mode, originalReplyPath: originalReplyPath || null, css: "generated from src/styles.css via Tailwind CLI", ...geometry }, null, 2) + "\n");
    writeFileSync(resolve(evidenceDir, `rendered-${mode}.html`), `<style>${css}</style>${markup.agent}\n`);
    await page.screenshot({ path: resolve(evidenceDir, `screenshot-${mode}.png`), fullPage: true });
    console.log(`chat markdown spacing ${mode}: agent ${geometry.agent.lineDelta}px, user ${geometry.user.lineDelta}px`);
  }
} finally {
  await browser.close();
}
