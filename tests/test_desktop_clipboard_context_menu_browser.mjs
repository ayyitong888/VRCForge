import assert from "node:assert/strict";
import { createServer } from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright";

const fixture = `
  import React, { useState } from "react";
  import { createRoot } from "react-dom/client";
  import i18n from "i18next";
  import { initReactI18next } from "react-i18next";
  import en from "./src/locales/en-US.json";
  import { TextEditContextMenu } from "./src/components/common/text-edit-context-menu";
  import { Composer } from "./src/components/chat/composer";
  await i18n.use(initReactI18next).init({ lng: "en-US", resources: { "en-US": { translation: en } } });
  const probe = window.probe = { webReads: 0, webWrites: 0, native: [], attached: [] };
  const clipboard = navigator.clipboard;
  window.seedClipboard = async (type) => {
    if (type === "text") return clipboard.writeText("粘贴文字");
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const png = await new Promise(resolve => canvas.toBlob(resolve));
    return clipboard.write([new ClipboardItem({ "image/png": png })]);
  };
  Object.defineProperty(navigator, "clipboard", { value: {
    readText: async () => { probe.webReads++; throw new DOMException("User denied clipboard read", "NotAllowedError"); },
    writeText: async () => { probe.webWrites++; },
  } });
  document.execCommand = () => false;
  window.isTauri = true;
  window.__TAURI_INTERNALS__ = {
    metadata: { currentWindow: { label: "main" }, currentWebview: { label: "main" } },
    transformCallback: () => 0,
    async invoke(command, args) {
      if (command.startsWith("plugin:event|")) return 1;
      assertCommand(command);
      const field = document.activeElement;
      probe.native.push([args.action, field.tagName]);
      if (!window.replayFilePaste || args.action !== "paste") {
        await window.dispatchEditCommand(args.action);
        return;
      }
      const clipboardData = new DataTransfer();
      clipboardData.items.add(new File(["file"], "notes.txt", { type: "text/plain" }));
      field.dispatchEvent(new ClipboardEvent("paste", { clipboardData, bubbles: true, cancelable: true }));
    },
  };
  function assertCommand(command) {
    if (command !== "edit_focused_webview") throw new Error("Unexpected command: " + command);
  }
  function Fixture() {
    const [input, setInput] = useState("Original draft");
    return <><TextEditContextMenu /><Composer input={input} setInput={setInput} attachments={[]}
      sending={false} onSubmit={() => {}} onSwitchMode={() => {}}
      onAttachFiles={files => probe.attached.push(...files.map(file => [file.name, file.type]))} /></>;
  }
  createRoot(document.getElementById("root")).render(<Fixture />);
`;

const { outputFiles } = await build({
  stdin: { contents: fixture, resolveDir: process.cwd(), loader: "tsx" },
  bundle: true,
  format: "esm",
  write: false,
});
const server = createServer((request, response) => {
  if (request.url === "/fixture.js") {
    response.setHeader("Content-Type", "application/javascript");
    response.end(outputFiles[0].text);
  } else {
    response.setHeader("Content-Type", "text/html");
    response.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
  }
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));

let browser;
try {
  // Chromium headless shell supplies a private in-memory clipboard.
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.setDefaultTimeout(5000);
  const cdp = await page.context().newCDPSession(page);
  await page.exposeFunction("dispatchEditCommand", (action) => {
    const [command, code, key] = { paste: ["Paste", "KeyV", "v"], copy: ["Copy", "KeyC", "c"], cut: ["Cut", "KeyX", "x"] }[action];
    return cdp.send("Input.dispatchKeyEvent", {
      type: "rawKeyDown", modifiers: 2, code, key,
      windowsVirtualKeyCode: key.toUpperCase().charCodeAt(0), commands: [command],
    });
  });
  const origin = `http://127.0.0.1:${server.address().port}`;
  await page.context().grantPermissions(["clipboard-write"], { origin });
  await page.goto(origin);
  const input = page.locator("textarea");
  await input.waitFor();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const choose = async (action) => {
    await input.click({ button: "right" });
    await page.getByRole("menu").getByText(action, { exact: true }).click({ timeout: 5000 });
    await page.getByRole("menu").waitFor({ state: "hidden" });
  };
  const seed = async (type) => {
    const seeded = await cdp.send("Runtime.evaluate", {
      expression: `window.seedClipboard(${JSON.stringify(type)})`, userGesture: true, awaitPromise: true,
    });
    assert.equal(seeded.exceptionDetails, undefined);
  };
  await seed("text");
  await choose("Paste");
  assert.deepEqual(await page.evaluate(() => window.probe), {
    webReads: 0, webWrites: 0, native: [["paste", "TEXTAREA"]], attached: [],
  }, "desktop context menu must dispatch a focused editing action without a web clipboard read");
  await page.waitForFunction(() => /粘贴文字/.test(document.querySelector("textarea").value));

  await seed("image");
  await choose("Paste");
  await page.waitForFunction(() => window.probe.attached.length > 0).catch(async error => {
    throw new Error(JSON.stringify(await page.evaluate(() => window.probe)), { cause: error });
  });
  await page.evaluate(() => { window.replayFilePaste = true; });
  await choose("Paste");
  assert.deepEqual(await page.evaluate(() => window.probe.attached.map(([name, mime]) => [name.endsWith(".png"), mime])), [
    [true, "image/png"], [false, "text/plain"],
  ]);
  await page.evaluate(() => { window.replayFilePaste = false; });
  for (const action of ["Copy", "Cut"]) {
    await input.click({ button: "right" });
    await page.getByRole("menu").getByText("Select all", { exact: true }).click();
    await choose(action);
  }
  assert.deepEqual(await page.evaluate(() => window.probe.native.slice(-2)), [["copy", "TEXTAREA"], ["cut", "TEXTAREA"]]);
  await page.waitForFunction(() => document.querySelector("textarea").value === "");
  await choose("Paste");
  await page.waitForFunction(() => /粘贴文字/.test(document.querySelector("textarea").value));
  assert.equal(await page.evaluate(() => window.probe.webWrites), 0);
  console.log("Desktop context-menu text, image, file, copy and cut replay passed");
} finally {
  await browser?.close();
  server.close();
}
