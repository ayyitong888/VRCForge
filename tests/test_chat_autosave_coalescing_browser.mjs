import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { build } from "esbuild";

const { chromium } = createRequire(import.meta.url)(
  process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright",
);
const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
import React from "react";
import { createRoot } from "react-dom/client";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { useChatSessions } from "./src/hooks/use-chat-sessions";
await i18n.use(initReactI18next).init({ lng: "en", resources: { en: { translation: {} } } });
const noop = () => {};
function Harness() {
  const sessions = useChatSessions({
    endpoint: location.origin, activeView: "chat", runtimeConnected: true, projectPrefsReady: true,
    projectPaths: [], customProjectPaths: [], activeProjectPath: "", setActiveProjectPath: noop,
    setActiveView: noop, setError: noop, expandProjectGroup: noop,
    initialChatState: { chats: [], activeChatId: "" },
  });
  window.sessions = sessions;
  return <div id="mounted" />;
}
window.__storageTrace = [];
const originalDebug = console.debug;
console.debug = (...args) => {
  if (args[0] === "[vrcforge:chat-storage]") window.__storageTrace.push(args[1]);
  else originalDebug(...args);
};
createRoot(document.getElementById("root")).render(<Harness />);
`,
  },
  bundle: true,
  write: false,
  format: "esm",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"test"' },
  plugins: [{
    name: "chat-autosave-baseline-control",
    setup(builder) {
      builder.onLoad({ filter: /use-chat-sessions\.ts$/ }, async (args) => {
        let contents = await readFile(args.path, "utf8");
        if (process.env.VRCFORGE_TEST_LEGACY_AUTOSAVE === "1") {
          contents = contents.replace(
            "      enqueueDebouncedChatSave();",
            '      void enqueueChatSave(true).catch((cause) => setChatSaveError(cause, t("chat.sessionSaveBlocked")));',
          );
        }
        if (process.env.VRCFORGE_TEST_LEGACY_AUTOSAVE_INTERVAL === "1") {
          // Restore just the pre-fix timer to isolate continuous-edit starvation.
          contents = contents
            .replace("const timer = window.setInterval(() => {", "const timer = window.setTimeout(() => {")
            .replace("return () => window.clearInterval(timer);", "return () => window.clearTimeout(timer);")
            .replace("}, [runtimeConnected, endpoint]);", "}, [chats, runtimeConnected, endpoint]);");
        }
        return { contents, loader: "tsx" };
      });
    },
  }],
});

let chats = [{
  id: "chat-fixture", sessionId: "session-fixture", title: "start", projectPath: "", projectType: "general",
  revision: 0, createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z", items: [],
}];
let postCount = 0;
const persistedTitles = [];
let releaseFirstPost;
let firstPostHeld;
const firstPostStarted = new Promise((resolve) => { firstPostHeld = resolve; });
const digest = () => Object.fromEntries(chats.map((chat) => [
  chat.id,
  createHash("sha256").update(JSON.stringify(chat)).digest("hex"),
]));
const shapeOf = (value) => value && typeof value === "object" && !Array.isArray(value)
  ? Object.fromEntries(Object.entries(value).filter(([key]) => key !== "items").map(([key, child]) => [key, shapeOf(child)]))
  : true;
const server = createServer(async (request, response) => {
  if (request.url === "/fixture.js") {
    response.setHeader("Content-Type", "text/javascript");
    response.end(bundle.outputFiles[0].text);
    return;
  }
  if (request.url?.startsWith("/api/app/chats") && request.method === "GET") {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({
      ok: true, chats, count: chats.length, sources: [], recoveries: [], writeBlocked: false,
      chatDigests: digest(), chatMetadataShapes: Object.fromEntries(chats.map((chat) => [chat.id, shapeOf(chat)])),
    }));
    return;
  }
  if (request.url === "/api/app/chats" && request.method === "POST") {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw);
    postCount += 1;
    if (postCount === 1) {
      firstPostHeld();
      await new Promise((resolve) => { releaseFirstPost = resolve; });
    }
    chats = body.chats;
    persistedTitles.push(chats[0]?.title);
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({
      ok: true, sourceRevisions: [], chatDigests: digest(),
      chatMetadataShapes: Object.fromEntries(chats.map((chat) => [chat.id, shapeOf(chat)])),
    }));
    return;
  }
  response.setHeader("Content-Type", "text/html");
  response.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
});

let browser;
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  const origin = `http://127.0.0.1:${server.address().port}`;
  await page.addInitScript(() => localStorage.setItem("vrcforge_developer_options_enabled", "true"));
  await page.route("**/*", (route) => route.request().url().startsWith(origin) ? route.continue() : route.abort());
  await page.goto(origin);
  await page.waitForFunction(() => window.sessions?.chats.length === 1);
  await page.evaluate(() => window.sessions.updateChat("chat-fixture", (chat) => ({ ...chat, title: "v1" })));
  await firstPostStarted;
  for (let index = 2; index <= 5; index += 1) {
    await page.evaluate((title) => window.sessions.updateChat("chat-fixture", (chat) => ({ ...chat, title })), `v${index}`);
    await page.waitForTimeout(900);
  }
  assert.equal(postCount, 1, "stream-like updates during a slow save do not append one autosave per debounce interval");
  const saveStages = await page.evaluate(() => window.__storageTrace
    .filter((event) => event.kind === "save")
    .map((event) => ({ stage: event.stage, queueDepth: event.queueDepth })));
  assert.ok(saveStages.some((event) => event.stage === "enqueued"));
  assert.ok(saveStages.every((event) => event.queueDepth <= 1), `only the active save and one coalesced latest snapshot are retained: ${JSON.stringify(saveStages)}`);
  await page.evaluate(() => {
    window.barrierSettled = false;
    window.sessions.persistChatsNow().then(() => { window.barrierSettled = true; });
  });
  await page.waitForTimeout(100);
  assert.equal(await page.evaluate(() => window.barrierSettled), false, "explicit persistence remains pending behind the active write");
  releaseFirstPost();
  await page.waitForFunction(() => window.barrierSettled === true);
  assert.ok(postCount >= 2 && postCount <= 3, "the explicit barrier resolves only after its queued write reaches storage");
  assert.deepEqual(persistedTitles.slice(0, 2), ["v1", "v5"], "the barrier persisted every mutation made before it was requested");
  await page.waitForFunction(() => window.__storageTrace.filter((event) => event.kind === "save" && event.stage === "resolved").length >= 3);
  await page.waitForTimeout(150);
  assert.equal(postCount, 3, "the explicit barrier and the pending autosave each retain their own write operation");
  assert.deepEqual(persistedTitles, ["v1", "v5", "v5"], "the trailing autosave remains current after the explicit barrier");
  assert.equal(chats[0].title, "v5", "the coalesced write contains the newest chat snapshot");
  await page.waitForTimeout(1000);
  await page.evaluate(() => window.sessions.persistChatsNow());
  await page.waitForTimeout(200);

  // A continuous stream must still produce an interval autosave while edits
  // keep arriving. A trailing debounce would postpone every write until the
  // stream stops, leaving the latest messages vulnerable to a tray kill.
  const streamStartPostCount = postCount;
  let streamObservedPost = false;
  const streamStartedAt = Date.now();
  let streamIndex = 6;
  while (Date.now() - streamStartedAt < 2200) {
    await page.evaluate((title) => window.sessions.updateChat("chat-fixture", (chat) => ({ ...chat, title })), `v${streamIndex}`);
    streamIndex += 1;
    await page.waitForTimeout(80);
    if (postCount > streamStartPostCount) streamObservedPost = true;
  }
  assert.equal(streamObservedPost, true, "continuous edits receive a save before the stream stops");
  await page.waitForTimeout(900);
  assert.ok(postCount > streamStartPostCount, "the stream's latest snapshot reaches storage");
  console.log("chat autosave slow-write coalescing replay: ok");
} finally {
  releaseFirstPost?.();
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
