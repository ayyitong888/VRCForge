import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import { build } from "esbuild";
import jsonPatch from "fast-json-patch";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const css = (await postcss([tailwindcss("./tailwind.config.ts")]).process(
  (await readFile("src/theme.css", "utf8")) + "\n@tailwind base; @tailwind components; @tailwind utilities;",
  { from: undefined },
)).css;
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
import React from "react";
import { createRoot } from "react-dom/client";
import { ConversationCard } from "./src/components/chat/conversation-card";
import { useChatSessions } from "./src/hooks/use-chat-sessions";
import { useChatRunController } from "./src/hooks/use-chat-run-controller";
import { runtimeEventFromExactResponse } from "./src/hooks/use-runtime-turn-activity";
import { useRuntimeTurnContinuationDelivery } from "./src/hooks/use-runtime-turn-continuation";
import { RUNTIME_ACTIVITY_STREAM_PREFIX } from "./src/lib/runtime-stream-owner";
import { initializeI18n, setLocale } from "./src/i18n";

const noop = () => {};
const origin = location.origin;
const initial = { chats: [], activeChatId: "" };
const paths = ["C:/fixture/project"];
function Harness() {
  const sessions = useChatSessions({ endpoint:origin, activeView:"chat", runtimeConnected:true,
    projectPrefsReady:true, projectPaths:paths, customProjectPaths:paths, activeProjectPath:"",
    setActiveProjectPath:noop, setActiveView:noop, setError:message=>window.errors.push(message),
    expandProjectGroup:noop, initialChatState:initial });
  const controller = useChatRunController({
    ...sessions, endpoint:origin, runtimeConnected:true, sessionId:sessions.activeChat?.sessionId,
    activeRuntimeProjectPath:sessions.activeChat?.projectPath, startRuntime:async()=>origin,
    refresh:async()=>{}, refreshRuntimeRuns:async()=>{}, refreshBackgroundGoals:noop,
    handleRuntimeSessionFailure:noop, setError:message=>window.errors.push(message),
  });
  const deliver = useRuntimeTurnContinuationDelivery(sessions);
  window.errors ??= [];
  window.fixture = {
    ready:sessions.chatStorageReady,
    open:async id=>{ await sessions.loadChat(id); sessions.setActiveChatId(id); },
    submit:id=>controller.submitTurn({ id, text:id, attachments:[], provider:"fixture", providerLabel:"Fixture", model:"model" }),
    openStream:()=>sessions.appendToChat("project", {id:RUNTIME_ACTIVITY_STREAM_PREFIX+"project-stream", type:"streaming", clientTurnId:"project-use", text:"Planning..."}),
    deliver:response=>deliver(runtimeEventFromExactResponse({ok:true,status:"completed",...response,response}, response)),
    save:sessions.persistChatsNow, locale:setLocale,
  };
  return <main className="mx-auto max-w-xl space-y-4 p-4 text-foreground">{sessions.activeChat?.items.map(item => <section key={item.id} data-item={item.id}>
    <ConversationCard item={item} onCopyItem={noop} />
  </section>)}</main>;
}
await initializeI18n();
createRoot(document.getElementById("root")).render(<Harness />);
` },
  bundle: true, write: false, format: "esm", jsx: "automatic",
});

const projectPath = "C:/fixture/project";
const chatFor = (id, path) => ({
  id, sessionId: `session-${id}`, title: id, revision: 0, projectPath: path,
  projectType: path ? "unity" : "general", items: [],
  createdAt: "2026-11-18T10:00:00Z", updatedAt: "2026-11-18T10:00:00Z",
});
const chats = new Map([["ordinary", chatFor("ordinary", "")], ["project", chatFor("project", projectPath)]]);
const memoryUsage = [{ id: "locale", text: "界面语言使用简体中文。" }, { id: "layout", text: "偏好紧凑的布局。" }];
// This fixture has one chat per store. The client treats storage versions as opaque.
const versions = new Map();
const versionOf = id => (versions.get(id) ?? 0).toString(16).padStart(64, "0");
const sourceFor = path => ({
  storeId: path ? "chat.project" : "chat.app", scope: path ? "project" : "app",
  ...(path ? { projectPath: path } : {}), exists: true, status: "ok",
  digest: versionOf(path ? "project" : "ordinary"),
});
const sources = () => [sourceFor(""), sourceFor(projectPath)];
const shape = value => value && typeof value === "object" && !Array.isArray(value)
  ? Object.fromEntries(Object.entries(value).map(([key, child]) => [key, shape(child)])) : true;
const metadataFor = ({ items, ...metadata }) => metadata;
const projections = chat => ({
  chatDigests: { [chat.id]: versionOf(chat.id) },
  chatMetadataShapes: { [chat.id]: shape(metadataFor(chat)) },
});
const replyFor = (id, sessionId, memories) => ({
  ok: true, status: "completed", sessionId, clientTurnId: id, turnId: `reply-${id}`,
  plan: { reply: "已根据你的偏好整理好这份清单。", summary: "", planner: "fixture", shellNeeded: false, nextStep: "done" },
  observe: {}, ...(memories ? { memoryUsage: memories } : {}),
});
const server = createServer(async (request, response) => {
  const url = new URL(request.url, "http://localhost");
  if (url.pathname === "/") {
    response.setHeader("Content-Type", "text/html; charset=utf-8");
    return response.end(`<!doctype html><style>${css}</style><div id="root"></div><script type="module" src="/fixture.js"></script>`);
  }
  if (url.pathname === "/fixture.js") {
    response.setHeader("Content-Type", "text/javascript");
    return response.end(bundle.outputFiles[0].text);
  }
  response.setHeader("Content-Type", "application/json");
  const send = value => response.end(JSON.stringify(value));
  if (url.pathname === "/api/app/chats" && url.searchParams.get("catalog") === "1") {
    return send({ ok:true, sources:sources(), recoveries:[], writeBlocked:false, pendingChatIds:[],
      entries:[...chats.values()].map(chat=>({
        ...metadataFor(chat), sourceProjectPath:chat.projectPath, sourceStoreId:sourceFor(chat.projectPath).storeId,
      })),
    });
  }
  if (url.pathname === "/api/app/chats" && url.searchParams.has("chatId")) {
    const chat = chats.get(url.searchParams.get("chatId"));
    return send({
      ok:true, chats:[chat], sourceRevision:sourceFor(chat.projectPath), hasOlder:false, nextBefore:null, ...projections(chat),
    });
  }
  if (request.method === "POST") {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw);
    if (url.pathname === "/api/app/agent/message") {
      return send(replyFor(body.clientTurnId, body.session_id, body.clientTurnId === "ordinary-use" ? memoryUsage : undefined));
    }
    if (url.pathname === "/api/app/chats/single") {
      const before = chats.get(body.chatId);
      const patch = body.chatPatch;
      const items = new Map(before.items.map(item => [item.id, item]));
      const chat = body.chat ?? {
        ...jsonPatch.applyPatch(metadataFor(before), patch.metadataPatch, true, false).newDocument,
        items: patch.itemOrder.map(id => patch.newItems[id] ??
          jsonPatch.applyPatch(items.get(id), patch.itemPatches[id] ?? [], true, false).newDocument),
      };
      chats.set(chat.id, chat);
      versions.set(chat.id, (versions.get(chat.id) ?? 0) + 1);
      return send({ ok:true, sourceRevisions:sources(), ...projections(chat) });
    }
  }
  return send({ ok:true, items:[], questions:[] });
});

let browser;
try {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ headless:true, channel:process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage({ locale:"zh-CN" });
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const origin = `http://127.0.0.1:${server.address().port}`;
  await page.route("**/*", route => route.request().url().startsWith(origin) ? route.continue() : route.abort());
  await page.goto(origin);
  const open = async id => {
    await page.waitForFunction(() => window.fixture?.ready);
    await page.evaluate(id => window.fixture.open(id), id);
  };
  const checkUsage = async (id, label, memories, key, screenshotPath) => {
    const card = page.locator(`[data-item="${id}"]`);
    const actions = card.locator("[data-chat-message-actions]");
    const button = actions.getByRole("button", { name:label });
    await button.waitFor();
    assert.equal(await actions.locator("button").count(), 2, "the memory icon shares the toolbar with Copy");
    assert.equal(await button.getAttribute("aria-expanded"), "false");
    const panel = actions.locator("div[hidden]");
    assert.equal(await panel.count(), 1);
    assert.equal(await card.getByText(memories[0].text, { exact:true }).count(), 0);
    if (key) await button.press(key);
    else await button.click();
    assert.equal(await button.getAttribute("aria-expanded"), "true");
    assert.deepEqual(await actions.getByRole("listitem").allTextContents(), memories.map(memory => memory.text));
    if (screenshotPath) await card.screenshot({ path:screenshotPath });
    await button.click();
    assert.equal(await button.getAttribute("aria-expanded"), "false");
  };
  await open("ordinary");
  assert.equal(await page.evaluate(() => window.fixture.submit("ordinary-use")), "started");
  await checkUsage("reply-ordinary-use", "使用了 2 条记忆", memoryUsage, "Enter", process.env.VRCFORGE_MEMORY_USAGE_SCREENSHOT);
  assert.equal(await page.evaluate(() => window.fixture.submit("ordinary-none")), "started");
  const noMemory = page.locator('[data-item="reply-ordinary-none"]');
  await noMemory.getByRole("button", { name:"复制" }).waitFor();
  assert.equal(await noMemory.locator("[data-chat-message-actions] button").count(), 1);
  await page.evaluate(() => window.fixture.save());
  assert.deepEqual(chats.get("ordinary").items.find(item => item.id === "reply-ordinary-use").response.memoryUsage, memoryUsage);

  await open("project");
  await page.evaluate(() => window.fixture.openStream());
  const stream = page.locator('[data-item="runtime-activity:project-stream"]');
  await stream.waitFor();
  assert.equal(await stream.getByRole("button", { name:/使用了.*记忆/ }).count(), 0);
  const recovered = replyFor("project-use", "session-project", memoryUsage.slice(0, 1));
  assert.equal(await page.evaluate(response => window.fixture.deliver(response), recovered), true);
  await checkUsage("runtime-reply-project-use", "使用了 1 条记忆", recovered.memoryUsage);
  assert.equal(await stream.count(), 0);
  await page.evaluate(() => window.fixture.save());
  const recoveredId = chats.get("project").items.find(item => item.type === "agent").id;
  await page.reload();
  await open("project");
  for (const [locale, label] of [["en-US", "Memories used (1)"], ["ja-JP", "1 件のメモリを使用"], ["zh-TW", "使用了 1 條記憶"]]) {
    await page.evaluate(locale => window.fixture.locale(locale), locale);
    await checkUsage(recoveredId, label, recovered.memoryUsage, "Space");
  }
  await page.evaluate(() => window.fixture.locale("en-US"));
  await open("ordinary");
  await checkUsage("reply-ordinary-use", "Memories used (2)", memoryUsage);
  assert.deepEqual(errors, []);
  assert.deepEqual(await page.evaluate(() => window.errors), []);
  console.log("PASS memory disclosure beside Copy, foreground and recovered turns, project and ordinary reload, four locales");
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
