import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtempSync, writeFileSync, readFileSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, sep } from "node:path";
import { build } from "esbuild";
import { createServer } from "node:http";

const root = resolve(import.meta.dirname, "..");
const evidenceDir = process.env.VRCFORGE_MARKDOWN_EVIDENCE_DIR || resolve(root, "local-review", "markdown-code-block-test");
mkdirSync(evidenceDir, { recursive: true });
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const temp = mkdtempSync(resolve(tmpdir(), "vrcforge-markdown-code-"));
const entry = resolve(temp, "entry.tsx");
const knownCode = JSON.stringify("const answer = 42;\n");
const diffCode = JSON.stringify("+ added\n- removed\n");
const unknownCode = JSON.stringify("raw <b>text</b>\n");
const mermaidCode = JSON.stringify("graph TD\n A-->B");
const badCode = JSON.stringify("graph TD\n A-->");
writeFileSync(entry, `import React,{useState} from "react";import{createRoot}from"react-dom/client";import{MarkdownCodeBlock}from${JSON.stringify(resolve(root,"src/components/chat/markdown-code-block.tsx"))};function App(){const[s,setS]=useState({code:${knownCode},language:"typescript"});return <main><section data-case="known"><MarkdownCodeBlock code={s.code} language={s.language}/><button data-update onClick={()=>setS({code:${diffCode},language:"diff"})}>update</button></section><section data-case="unknown"><MarkdownCodeBlock code={${unknownCode}} language="not-a-real-language"/></section><section data-case="mermaid"><MarkdownCodeBlock code={${mermaidCode}} language="mermaid"/></section><section data-case="bad"><MarkdownCodeBlock code={${badCode}} language="mermaid"/></section></main>}createRoot(document.getElementById("root")).render(<App/>);`);
const outdir = resolve(temp, "out");
await build({ absWorkingDir: root, nodePaths: [resolve(root, "node_modules")], entryPoints: [entry], bundle: true, splitting: true, format: "esm", platform: "browser", outdir, jsx: "automatic" });
writeFileSync(resolve(temp, "index.html"), `<!doctype html><meta charset="utf-8"><style>${readFileSync(resolve(root,"src/components/chat/markdown-code-block.css"),"utf8")}</style><div id="root"></div><script type="module" src="/entry.js"></script>`);
const server = createServer((req, res) => {
  const file = req.url === "/" ? resolve(temp, "index.html") : resolve(outdir, (req.url || "").slice(1));
  if (!file.startsWith(`${temp}${sep}`)) { res.writeHead(403); res.end(); return; }
  try { const data = readFileSync(file); res.writeHead(200, { "content-type": file.endsWith(".js") ? "text/javascript" : "text/html" }); res.end(data); }
  catch { res.writeHead(404); res.end(); }
});
await new Promise((done) => server.listen(0, "127.0.0.1", done));
const browser = await chromium.launch({ headless: true, ...(process.env.VRCFORGE_BROWSER_EXECUTABLE ? { executablePath: process.env.VRCFORGE_BROWSER_EXECUTABLE } : { channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" }) });
try {
  const context = await browser.newContext({ permissions: ["clipboard-read", "clipboard-write"] });
  const page = await context.newPage(); await page.goto(`http://127.0.0.1:${server.address().port}/`, { waitUntil: "networkidle" });
  const known = page.locator('[data-case="known"] .markdown-code-block'); assert.equal(await known.locator("code").textContent(), "const answer = 42;\n"); assert.ok(await known.locator(".hljs-keyword").count()); await known.getByRole("button", { name: "复制代码" }).click(); assert.equal(await page.evaluate(() => navigator.clipboard.readText()), "const answer = 42;\r\n");
  await page.locator("[data-update]").click(); await page.waitForTimeout(250); assert.equal(await known.locator("code").textContent(), "+ added\n- removed\n"); assert.ok(await known.locator(".hljs-addition").count() && await known.locator(".hljs-deletion").count());
  const unknown = page.locator('[data-case="unknown"] .markdown-code-block code'); assert.equal(await unknown.textContent(), "raw <b>text</b>\n"); assert.equal(await unknown.locator("span").count(), 0);
  const mermaid = page.locator('[data-case="mermaid"] [aria-label="Mermaid diagram"] svg'); await mermaid.waitFor({ state: "visible", timeout: 8000 }); assert.deepEqual(await mermaid.locator("text").allTextContents(), ["A", "B"]); const marker = await mermaid.locator("[marker-end]").first().getAttribute("marker-end"); assert.ok(marker?.startsWith("url(#")); assert.equal(await mermaid.locator(`#${marker.slice(5, -1)}`).count(), 1); assert.equal(await mermaid.locator("a").count(), 0);
  const bad = page.locator('[data-case="bad"] .markdown-code-block'); await bad.getByText(/预览不可用/).waitFor({ state: "visible", timeout: 8000 }); assert.equal(await bad.locator("code").textContent(), "graph TD\n A-->");
  const diagramGeometry = await mermaid.evaluate((svg) => {
    const frame = svg.getBoundingClientRect();
    return [...svg.querySelectorAll("text")].map((text) => {
      const box = text.getBoundingClientRect();
      return { text: text.textContent, width: box.width, height: box.height, inFrame: box.left >= frame.left && box.right <= frame.right && box.top >= frame.top && box.bottom <= frame.bottom };
    });
  });
  assert.ok(diagramGeometry.every((node) => node.width > 0 && node.height > 0 && node.inFrame), "diagram labels must be visibly laid out inside the SVG, not merely present in DOM");
  await page.evaluate(() => Object.defineProperty(navigator.clipboard, "writeText", { configurable: true, value: async () => { throw new Error("denied"); } }));
  await known.getByRole("button", { name: "复制代码" }).click();
  assert.match(await known.textContent(), /复制失败/);
  await page.screenshot({ path: resolve(evidenceDir, "browser.png"), fullPage: true }); writeFileSync(resolve(evidenceDir, "result.json"), JSON.stringify({ knownHighlighted: true, copied: "const answer = 42;\\r\\n", copyFailureVisible: true, diffUpdated: true, unknownPlain: true, mermaidNodes: ["A", "B"], diagramGeometry, mermaidMarker: marker, badGraphSourceVisible: true }, null, 2));
  console.log("markdown code block browser test passed");
} finally {
  await browser.close();
  await new Promise((done) => server.close(done));
  assert.ok(resolve(temp).startsWith(`${resolve(tmpdir())}${sep}vrcforge-markdown-code-`));
  rmSync(temp, { recursive: true, force: true });
}
