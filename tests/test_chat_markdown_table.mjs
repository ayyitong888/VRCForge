import assert from "node:assert/strict";
import Module, { createRequire } from "node:module";
import { readFileSync, writeFileSync, mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, sep } from "node:path";
import { execFileSync } from "node:child_process";
import { build } from "esbuild";

const root = resolve(import.meta.dirname, "..");
const text = process.env.VRCFORGE_MARKDOWN_TABLE_FIXTURE
  ? readFileSync(process.env.VRCFORGE_MARKDOWN_TABLE_FIXTURE, "utf8").split("\n").filter((line) => line.startsWith("|")).join("\n")
  : "| Item | First | Second | Judgment |\n|---|---|---|---|\n| Version | Current release | Older release | Different version |\n| Tools | Available | Available | Same |\n| Notes | Longer description | Shorter description | Documentation differs |";
const bundle = await build({ stdin: { contents: 'import React from "react"; import {renderToStaticMarkup} from "react-dom/server"; import {ChatMarkdown} from "./src/components/chat/chat-markdown"; export const render=(text)=>renderToStaticMarkup(React.createElement(ChatMarkdown,{text}));', loader: "tsx", resolveDir: root }, bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom/server", "react/jsx-runtime"] });
const filename = resolve(root, "tests/table-render.cjs");
const fixture = new Module(filename);
fixture.paths = Module._nodeModulePaths(root);
fixture._compile(bundle.outputFiles[0].text, filename);
const markup = fixture.exports.render(text);
const temp = mkdtempSync(resolve(tmpdir(), "vrcforge-table-"));
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
let browser;
try {
  const cssPath = resolve(temp, "app.css");
  execFileSync(process.execPath, [resolve(root, "node_modules/tailwindcss/lib/cli.js"), "-i", resolve(root, "src/styles.css"), "-o", cssPath], { cwd: root, stdio: "ignore" });
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
  await page.setContent(`<style>${readFileSync(cssPath, "utf8")}</style><main style="width:900px;margin:24px;font-size:14px">${markup}</main>`);
  const rows = await page.locator("tbody tr").evaluateAll((rows) => rows.map((row) => [...row.cells].map((cell) => ({ border: getComputedStyle(cell).borderBottomWidth, width: cell.getBoundingClientRect().width }))));
  const evidence = process.env.VRCFORGE_MARKDOWN_EVIDENCE_DIR;
  if (evidence) {
    mkdirSync(evidence, { recursive: true });
    writeFileSync(resolve(evidence, "table-borders.json"), JSON.stringify(rows, null, 2));
    await page.screenshot({ path: resolve(evidence, "table.png"), fullPage: true });
  }
  assert.ok(rows.length >= 3 && rows.every((row) => row.length === 4));
  for (const row of rows.slice(0, -1)) {
    assert.ok(row.every((cell) => cell.border === "1px"), "every column, including the last, needs a divider between rows");
  }
  assert.ok(rows.at(-1).every((cell) => cell.border === "0px"), "only the final table row omits its bottom cell borders");
  assert.ok(rows.every((row) => row.every((cell) => cell.width > 0)));
  console.log("Markdown table: every row divider spans all four columns");
} finally {
  await browser?.close();
  assert.ok(resolve(temp).startsWith(`${resolve(tmpdir())}${sep}vrcforge-table-`));
  rmSync(temp, { recursive: true, force: true });
}
