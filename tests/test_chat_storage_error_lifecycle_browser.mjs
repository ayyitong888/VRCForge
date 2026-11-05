import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
      import React, {useState} from "react";
      import {createRoot} from "react-dom/client";
      import i18n from "i18next";
      import {initReactI18next} from "react-i18next";
      import {useChatSessions} from "./src/hooks/use-chat-sessions";
      import {WorkspaceHeader} from "./src/components/workspace/workspace-header";
      await i18n.use(initReactI18next).init({lng:"en",resources:{en:{translation:{
        chat:{sessionSaveBlocked:"SAVE_BLOCKED",sessionSaveConflict:"CHAT_SAVE_CONFLICT",sessionConcurrentConflict:"CHAT_CONCURRENT_CONFLICT",sessionRecoveryBlocked:"CHAT_RECOVERY_BLOCKED"},
        header:{newTask:"New task",reconnect:"Reconnect",reconnecting:"Reconnecting"},
        doctor:{retry:"Retry",retrying:"Retrying"},
        common:{dismiss:"Dismiss"},sidebar:{doctor:"Doctor",optimization:"Optimization",skills:"Skills",settings:"Settings"},encryption:{protection:"Protection"}
      }}}});
      const noop=()=>{};
      function Fixture(){
        const [error,setError]=useState("");
        const sessions=useChatSessions({endpoint:location.origin,activeView:"chat",runtimeConnected:true,projectPrefsReady:true,projectPaths:[],customProjectPaths:[],activeProjectPath:"",setActiveProjectPath:noop,setActiveView:noop,setError,expandProjectGroup:noop,initialChatState:{chats:[],activeChatId:""}});
        window.testApi={sessions,setError};
        return <><WorkspaceHeader activeProjectLabel="" activeView="chat" activeChatTitle="Chat" theme="light" showDoctorStartupPrompt={false} hasStartupIssue={false} healthErrors={0} healthWarnings={0} startupIssue="" loadingDoctor={false} loading={false} error={error} chatStorageError={sessions.chatStorageError?.message || ""} onToggleTheme={noop} onOpenDoctor={noop} onRetryStartupOrHealth={noop} onDismissDoctorPrompt={noop} onStartRuntime={noop} onRetryChatStorage={()=>{void sessions.reloadChatStorageState().catch(()=>undefined)}}/><pre id="state">{JSON.stringify({error,chatStorageError:sessions.chatStorageError||null})}</pre></>;
      }
      createRoot(document.getElementById("root")).render(<Fixture/>);
    `,
  },
  bundle: true,
  write: false,
  format: "esm",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"test"' },
});

const makeChat = () => ({
  id: "chat",
  sessionId: "session",
  title: "Original title",
  projectPath: "",
  projectType: "general",
  revision: 0,
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-01T00:00:00Z",
  items: [{ id: "user-1", type: "user", text: "Original title" }],
});
let chats = [makeChat()];
const posts = [];
const postStatuses = [409, 409, 200];
const digest = () => Object.fromEntries(chats.map((chat) => [chat.id, createHash("sha256").update(JSON.stringify(chat)).digest("hex")]));
const shapeOf = (value) => value && typeof value === "object" && !Array.isArray(value)
  ? Object.fromEntries(Object.entries(value).filter(([key]) => key !== "items").map(([key, child]) => [key, shapeOf(child)]))
  : true;

const server = createServer(async (request, response) => {
  if (request.url === "/fixture.js") {
    response.setHeader("Content-Type", "text/javascript");
    response.end(bundle.outputFiles[0].text);
    return;
  }
  if (request.url === "/fixture/state") {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({ postCount: posts.length, posts }));
    return;
  }
  if (request.url?.startsWith("/api/app/chats") && request.method === "GET") {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({ ok: true, chats, count: chats.length, sources: [], recoveries: [], writeBlocked: false,
      chatDigests: digest(), chatMetadataShapes: Object.fromEntries(chats.map((chat) => [chat.id, shapeOf(chat)])) }));
    return;
  }
  if (request.url === "/api/app/chats" && request.method === "POST") {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw);
    const status = postStatuses.shift() ?? 200;
    posts.push({ status, requestedIds: body.chats.map((chat) => chat.id), patchIds: Object.keys(body.chatPatches || {}) });
    if (status === 409) {
      response.statusCode = 409;
      response.setHeader("Content-Type", "application/json");
      response.end(JSON.stringify({ detail: "stale chat storage revision" }));
      return;
    }
    chats = body.chats;
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({ ok: true, sourceRevisions: [], chatDigests: digest(),
      chatMetadataShapes: Object.fromEntries(chats.map((chat) => [chat.id, shapeOf(chat)])) }));
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
  await page.route("**/*", (route) => route.request().url().startsWith(origin) ? route.continue() : route.abort());
  await page.goto(origin);
  await page.waitForFunction(() => window.testApi?.sessions?.chatStorageReady === true && window.testApi.sessions.chats.length === 1);
  await page.evaluate(() => {
    window.testApi.setError("UNRELATED_ERROR");
    window.testApi.sessions.updateChat("chat", (chat) => ({ ...chat, title: "Local edit" }));
  });
  await page.waitForFunction(() => document.querySelector("#state")?.textContent.includes("CHAT_SAVE_CONFLICT"));
  const afterRejection = await page.evaluate(async () => ({
    ...JSON.parse(document.querySelector("#state").textContent),
    server: await fetch("/fixture/state").then((response) => response.json()),
  }));
  assert.equal(afterRejection.server.postCount, 2, `the save receives a 409 and one reconcile retry: ${JSON.stringify(afterRejection)}`);
  assert.equal(afterRejection.error, "UNRELATED_ERROR", "chat autosave failure must not replace an unrelated App error");
  assert.equal(afterRejection.chatStorageError?.kind, "save", "the failed chat save is owned by the chat storage hook");
  assert.equal(afterRejection.chatStorageError?.message, "CHAT_SAVE_CONFLICT");

  await page.getByRole("button", { name: "Retry" }).click();
  await page.waitForFunction(() => document.querySelector("#state")?.textContent.includes('"chatStorageError":null'));
  const afterRecovery = await page.evaluate(() => ({
    error: JSON.parse(document.querySelector("#state").textContent).error,
    chatStorageError: JSON.parse(document.querySelector("#state").textContent).chatStorageError,
    alertText: [...document.querySelectorAll('[role="alert"]')].map((alert) => alert.textContent),
  }));
  assert.equal(afterRecovery.error, "UNRELATED_ERROR", "successful storage reload must not clear unrelated App errors");
  assert.equal(afterRecovery.chatStorageError, null, "successful reload and save clear the resolved chat save error");
  assert.equal(afterRecovery.alertText.some((text) => text.includes("CHAT_SAVE_CONFLICT")), false);
  assert.equal(afterRecovery.alertText.some((text) => text.includes("UNRELATED_ERROR")), true);
  assert.deepEqual(posts.map((post) => post.status), [409, 409, 200]);
  assert.ok(posts.every((post) => post.requestedIds.includes("chat")));
  console.log(JSON.stringify({ status: "passed", saveResponses: posts.map((post) => post.status),
    independentErrorPreserved: afterRecovery.error, recoveredChatStorageError: afterRecovery.chatStorageError }));
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
