import assert from "node:assert/strict";
import { createServer } from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright";

const bundle = await build({ stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
  import React from "react";
  import { createRoot } from "react-dom/client";
  import { ChatMarkdown, ChatResourceImageContext } from "./src/components/chat/chat-markdown";
  import { OpenChatLinkContext } from "./src/lib/chat-deep-link";
  import { installDesktopDropGuard } from "./src/lib/desktop-drop-guard";
  import { initializeI18n } from "./src/i18n";
  window.calls = [];
  if (!location.search) window.__TAURI_INTERNALS__ = { invoke: async (command, payload) => { window.calls.push({ command, payload }); } };
  installDesktopDropGuard();
  const body = [
    "[Browser reference](" + location.origin + "/reference)",
    "[Relative file](/reference)",
    "[Jump to note](#note)",
    '<span id="note">Local note</span>',
    "[PDF report](file:///D:/project/report.pdf)",
    "[Local chat](vrcforge://chat/fixture-chat)",
    "[External Codex chat](codex://threads/019f052c-5b66-7ad3-829b-825371654321)",
  ].join("\\n\\n");
  initializeI18n().then(() => createRoot(document.getElementById("root")).render(
    <OpenChatLinkContext.Provider value={id => window.calls.push({ command: "open-local-chat", id })}>
      <ChatResourceImageContext.Provider value={{ endpoint: location.origin, chatId: "source-chat", projectPath: "D:/project", sessionId: "session", clientTurnId: "turn" }}>
        <ChatMarkdown text={body} />
      </ChatResourceImageContext.Provider>
    </OpenChatLinkContext.Provider>
  ));
` }, bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic" });

const server = createServer((request, response) => {
  response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  response.end(request.url.startsWith("/reference") ? "Reference" : `<div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
});
let browser;
try {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ channel: process.platform === "win32" ? "msedge" : undefined, headless: true });
  const page = await browser.newPage();
  const popups = [];
  page.on("popup", popup => popups.push(popup));
  await page.goto(origin);
  const link = page.getByRole("link", { name: "Browser reference" });
  await link.evaluate(node => node.click());
  assert.deepEqual(await page.evaluate(() => window.calls), [], "a synthetic click cannot open the external browser");
  for (const options of [{}, { button: "middle" }, { modifiers: ["Control"] }]) await link.click(options);
  await page.getByRole("link", { name: "Local chat" }).click({ button: "middle" });
  await page.getByRole("link", { name: "External Codex chat" }).click();
  await page.getByRole("link", { name: "PDF report" }).click();
  assert.equal(await page.getByRole("link", { name: "Relative file" }).count(), 0);
  assert.deepEqual(await page.evaluate(() => window.calls), [
    ...Array.from({ length: 3 }, () => ({ command: "open_chat_web_link", payload: { url: origin + "/reference" } })),
    { command: "open-local-chat", id: "fixture-chat" },
    { command: "open_codex_chat_url", payload: { url: "codex://threads/019f052c-5b66-7ad3-829b-825371654321" } },
    { command: "open_chat_file", payload: { request: {
      path: "D:/project/report.pdf", chatId: "source-chat", projectPath: "D:/project", sessionId: "session", clientTurnId: "turn",
    } } },
  ]);
  assert.equal(popups.length, 0, "desktop links must never create a WebView popup");
  assert.equal(page.url(), origin + "/");
  const jump = page.getByRole("link", { name: "Jump to note" });
  await jump.click({ button: "middle" });
  await jump.click();
  assert.equal(new URL(page.url()).hash, await jump.getAttribute("href"));
  assert.equal(popups.length, 0);

  const transfers = await page.evaluate(() => {
    const target = document.querySelector("#root");
    return ["text/uri-list", "Files", "text/plain"].map(type => {
      const dataTransfer = new DataTransfer();
      if (type === "Files") dataTransfer.items.add(new File(["fixture"], "report.pdf", { type: "application/pdf" }));
      else dataTransfer.setData(type, location.origin + "/reference");
      return ["dragover", "drop"].map(name => {
        const event = new DragEvent(name, { dataTransfer, bubbles: true, cancelable: true });
        target.dispatchEvent(event);
        return event.defaultPrevented;
      });
    });
  });
  assert.deepEqual(transfers, [[true, true], [true, true], [false, false]]);

  await page.goto(origin + "/?browser");
  const opened = page.waitForEvent("popup");
  await page.getByRole("link", { name: "Browser reference" }).click();
  assert.equal((await opened).url(), origin + "/reference", "browser mode retains ordinary external hyperlinks");
  console.log("PASS desktop Markdown URLs use system dispatch; deep links, fragments, PDF viewer and file/URL drop defaults retain their intended routing.");
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
