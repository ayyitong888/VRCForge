import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
let fixture;
let reply;
let uri;
let expiredReply;
const realRecordReplay = Boolean(process.env.VRCFORGE_CHAT_IMAGE_RECORD && process.env.VRCFORGE_CHAT_IMAGE_FIXTURE);
if (realRecordReplay) {
  const chat = JSON.parse(await readFile(process.env.VRCFORGE_CHAT_IMAGE_RECORD, "utf8"));
  const agentItem = [...chat.items].reverse().find((item) => item.type === "agent" && String(item.response?.plan?.reply || "").includes("vrcforge://scene-capture-preview/"));
  assert.ok(agentItem, "record contains a final assistant reply with an authenticated scene capture Resource URI");
  fixture = JSON.parse(await readFile(process.env.VRCFORGE_CHAT_IMAGE_FIXTURE, "utf8"));
  const originalUri = agentItem.response.plan.reply.match(/!\[[^\]]*\]\((vrcforge:\/\/scene-capture-preview\/[^)]+)\)/)?.[1];
  assert.ok(originalUri, "real chat record has the original expired Resource URI");
  reply = fixture.reply;
  uri = fixture.uri;
  fixture.originalUri = originalUri;
  expiredReply = agentItem.response.plan.reply;
} else {
  const captureId = "8d886299-9be1-4baf-8e1f-e205f3a8e5fc";
  uri = `vrcforge://scene-capture-preview/${captureId}?context=runtime%3Asession-fixture&revision=1&store=${"1".repeat(32)}`;
  fixture = {
    uri,
    originalUri: `vrcforge://scene-capture-preview/0d886299-9be1-4baf-8e1f-e205f3a8e5fc?context=runtime%3Asession-fixture&revision=1&store=${"2".repeat(32)}`,
    reply: `Preview:\n\n![Fixture image](${uri})`,
    context: { chatId: "chat-fixture", projectPath: "C:/fixture-project", sessionId: "session-fixture", clientTurnId: "turn-fixture" },
    resourceResponse: {
      ok: true,
      uri,
      mimeType: "image/png",
      sha256: "a".repeat(64),
      pngBase64: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jQWQAAAAASUVORK5CYII=",
    },
    previewSize: { width: 1, height: 1 },
  };
  reply = fixture.reply;
  expiredReply = `![Fixture image](${fixture.originalUri})`;
}

const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
import React from "react";
import { createRoot } from "react-dom/client";
import { ChatMarkdown, ChatResourceImageContext } from "./src/components/chat/chat-markdown";
import { initializeI18n } from "./src/i18n";
const root = createRoot(document.getElementById("root"));
window.mountChatReply = async (text, context) => {
  await initializeI18n();
  root.render(<ChatResourceImageContext.Provider value={context}><ChatMarkdown text={text} /></ChatResourceImageContext.Provider>);
};
`,
  },
  bundle: true,
  write: false,
  format: "iife",
  platform: "browser",
  jsx: "automatic",
});

const server = createServer((_request, response) => {
  response.setHeader("Content-Type", "text/html; charset=utf-8");
  response.end(`<div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
});
let browser;
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  const calls = [];
  page.on("pageerror", (error) => calls.push({ error: error.message }));
  await page.route("**/api/app/agent/resource-image", async (route) => {
    const request = route.request().postDataJSON();
    calls.push({ request });
    if (request.uri === fixture.originalUri) {
      await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Chat image Resource is unavailable." }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(fixture.resourceResponse) });
  });
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const context = { ...fixture.context, endpoint: `http://127.0.0.1:${server.address().port}` };
  await page.evaluate(({ text, resourceContext }) => window.mountChatReply(text, resourceContext), { text: reply, resourceContext: context });
  const image = page.locator("img");
  await image.waitFor({ state: "visible" });
  await page.waitForFunction(() => (document.querySelector("img")?.naturalWidth || 0) > 0);
  assert.equal(await image.count(), 1, "actual final markdown renders a single image");
  assert.equal(await image.getAttribute("src").then((src) => src.startsWith("data:image/png;base64,")), true, "browser receives verified bytes, never navigates directly to the internal URI");
  assert.deepEqual(await image.evaluate((element) => ({ width: element.naturalWidth, height: element.naturalHeight })), fixture.previewSize, "browser decodes the real retained capture preview dimensions");
  assert.deepEqual(calls.filter((entry) => entry.request).map((entry) => entry.request), [{
    uri,
    chatId: context.chatId,
    projectPath: context.projectPath,
    sessionId: context.sessionId,
    clientTurnId: context.clientTurnId,
  }], "fetch is bound to the exact source chat, project, session, and turn");
  assert.deepEqual(calls.filter((entry) => entry.error), [], "browser render has no runtime exceptions");
  await page.evaluate(({ text, resourceContext }) => window.mountChatReply(text, resourceContext), { text: expiredReply, resourceContext: context });
  await page.getByRole("status").waitFor();
  const unavailableText = await page.getByRole("status").innerText();
  assert.match(unavailableText, /unavailable|不可用|無法使用/i, "stale original URI remains a visible localized image-unavailable error");
  assert.equal(await page.locator("img").count(), 0, "expired resource does not silently leave a broken image element");
  console.log(realRecordReplay
    ? "PASS: retained final capture rendered in isolation; expired original URI shows an explicit unavailable status and was not revived."
    : "PASS: authenticated Resource image renders and stale Resource handles show an explicit unavailable status.");
} finally {
  await browser?.close();
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
}
