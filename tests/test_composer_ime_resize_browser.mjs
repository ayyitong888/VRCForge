import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const baselinePath = process.env.VRCFORGE_IME_BASELINE;
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import { useRef, useState } from "react";
    import { createRoot } from "react-dom/client";
    import { ComposerLinkInput } from "./src/components/chat/composer-link-input";
    if (!location.search.includes("browser")) window.__TAURI_INTERNALS__ ??= {};
    function Fixture() {
      const input = useRef(null);
      const [value, setValue] = useState("");
      return <><button type="button">Elsewhere</button><main style={{position: "fixed", bottom: 80, left: 80, width: "70vw"}}>
        <ComposerLinkInput inputRef={input} value={value} onChange={setValue} placeholder="随心输入" rows={1} />
      </main></>;
    }
    createRoot(document.getElementById("root")).render(<Fixture />);
  ` },
  bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
  plugins: baselinePath ? [{ name: "ime-baseline", setup(plugin) {
    plugin.onLoad({ filter: /composer-link-input\.tsx$/ }, async () => ({
      loader: "tsx", contents: await readFile(baselinePath, "utf8"),
    }));
  } }] : [],
});
const html = '<meta charset="utf-8"><div id="root"></div><script>'
  + bundle.outputFiles[0].text.replaceAll("</script", "<\\/script") + "</script>";
if (process.env.VRCFORGE_IME_FIXTURE_PATH) await writeFile(process.env.VRCFORGE_IME_FIXTURE_PATH, html);

const server = createServer((_req, res) => res.setHeader("Content-Type", "text/html;charset=utf-8").end(html));
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const browser = await chromium.launch({ headless: true, channel: "msedge" });
try {
  const page = await browser.newPage({ userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" });
  const url = `http://127.0.0.1:${server.address().port}`;
  const input = page.locator("textarea");
  const prepare = async () => {
    await input.fill("保留选区和草稿");
    await input.evaluate(node => {
      node.setSelectionRange(1, 4, "backward");
      window.imeBlurs = 0;
      node.addEventListener("blur", () => window.imeBlurs++);
    });
  };
  const resize = (phase) => page.evaluate(phase => window.dispatchEvent(new Event(`vrcforge-native-resize-${phase}`)), phase);
  const flushTimers = () => page.evaluate(() => new Promise(resolve => setTimeout(resolve, 100)));
  const readFocus = () => input.evaluate(node => ({
    focused: node === document.activeElement, blurs: window.imeBlurs,
    value: node.value, selection: [node.selectionStart, node.selectionEnd, node.selectionDirection],
  }));

  await page.goto(url);
  await prepare();
  await resize("start");
  await flushTimers();
  await resize("end");
  assert.deepEqual(await readFocus(), {
    focused: true, blurs: 1, value: "保留选区和草稿", selection: [1, 4, "backward"],
  }, "a desktop resize renews text focus once and preserves the draft and selection");

  await input.dispatchEvent("compositionstart", { bubbles: true });
  await resize("start");
  await resize("end");
  await flushTimers();
  assert.equal((await readFocus()).blurs, 1, "resizing during composition must not interrupt it");
  await input.dispatchEvent("compositionend", { bubbles: true });
  await flushTimers();
  assert.equal((await readFocus()).blurs, 2, "the pending reposition runs after composition ends");
  await resize("start");
  await page.getByRole("button", { name: "Elsewhere" }).click();
  await resize("end");
  await flushTimers();
  assert.equal((await readFocus()).focused, false, "resizing does not steal focus after the user clicks away");

  await page.goto(url + "?browser");
  await prepare();
  await resize("start");
  await resize("end");
  await flushTimers();
  assert.equal((await readFocus()).blurs, 0, "ordinary browsers do not use the WebView2 workaround");
  console.log("PASS Windows composer IME resize focus, composition, and selection");
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
