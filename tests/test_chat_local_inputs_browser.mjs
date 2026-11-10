import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const owner = { id: "chat", sessionId: "session", title: "Existing", projectPath: "", projectType: "general" };
const original = { id: "old", type: "user", text: "Previous", createdAt: "2026-01-01T00:00:00Z" };
const user = { id: "user-direct", type: "user", clientTurnId: "direct", text: "A full local input", createdAt: "2026-01-01T00:00:01Z" };
const queued = { id: "user-queued", type: "user", clientTurnId: "queued", text: "A queued input", createdAt: "2026-01-01T00:00:02Z",
  queuedFrom: true, composerQueued: true, queueStatus: "queued", queueEnvelope: { laneId: "chat", sessionId: "session" } };
const cancelled = { ...queued, id: "user-cancelled", clientTurnId: "cancelled", text: "A cancelled input", createdAt: "2026-01-01T00:00:03Z" };
const receipt = { id: "reply-direct", type: "agent", response: { clientTurnId: "direct", plan: { reply: "Recovered reply" } } };
const wrongOwner = { ...owner, sessionId: "old-session" };
const newOwner = { ...owner, id: "new-chat", sessionId: "new-session", title: "New" };
const titledOwner = { ...owner, id: "titled-chat", sessionId: "", title: "Named before first send" };
let serverChats = [{ ...owner, items: [original, receipt, { ...cancelled, attachments: [{ id: "external-note", name: "note.txt", size: 0, type: "text/plain" }] }] },
  { ...titledOwner, items: [] }];
let blocked = true;
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React from "react";
    import {createRoot} from "react-dom/client";
    import i18n from "i18next";
    import {initReactI18next} from "react-i18next";
    import {persistAttachmentReference} from "./src/lib/attachment-payloads";
    import {useChatSessions} from "./src/hooks/use-chat-sessions";
    import {recordLocalChatInput, readLocalChatInputs} from "./src/lib/chat-local-inputs";
    await i18n.use(initReactI18next).init({lng:"en",resources:{en:{translation:{}}}});
    const endpoint=location.origin;
    const noop=()=>{};
    const paths=[];
    const initial={chats:[],activeChatId:""};
    window.journal=()=>readLocalChatInputs(endpoint);
    window.stage=async()=>{
      const stage=(owner,item,...earlier)=>recordLocalChatInput(endpoint,{...owner,items:[...earlier,item]},item.clientTurnId);
      const owner=${JSON.stringify(owner)};
      const original=${JSON.stringify(original)};
      const attachmentPayloads={};
      const attachment=persistAttachmentReference({id:"note",name:"notes.txt",size:13,type:"text/plain",text:"Original note"},attachmentPayloads);
      const user={...${JSON.stringify(user)},attachments:[attachment]};
      const queued=${JSON.stringify(queued)};
      const cancelled=${JSON.stringify(cancelled)};
      await recordLocalChatInput(endpoint,{...owner,items:[original,user],attachmentPayloads},user.clientTurnId);
      await stage(owner,queued,original);
      await stage(owner,{...cancelled,queueStatus:"cancelled"},original);
      await stage(${JSON.stringify(wrongOwner)},{...${JSON.stringify(user)},id:"wrong",clientTurnId:"wrong",text:"Do not merge across sessions"},original);
      await stage(${JSON.stringify(newOwner)},{...${JSON.stringify(user)},id:"new-input",clientTurnId:"new-input"});
      await stage({...${JSON.stringify(titledOwner)},sessionId:"titled-session"},{...${JSON.stringify(user)},id:"titled-input",clientTurnId:"titled-input"});
    };
    window.stageDeletion=()=>recordLocalChatInput(endpoint,{
      ...${JSON.stringify(newOwner)},id:"delete-chat",sessionId:"delete-session",
      items:[{...${JSON.stringify(user)},id:"delete-input",clientTurnId:"delete-input"}],
    },"delete-input");
    function Fixture(){
      window.sessions=useChatSessions({endpoint,activeView:"chat",runtimeConnected:true,projectPrefsReady:true,
        projectPaths:paths,customProjectPaths:paths,activeProjectPath:"",setActiveProjectPath:noop,setActiveView:noop,
        setError:noop,expandProjectGroup:noop,initialChatState:initial});
      return <pre>{JSON.stringify({ready:window.sessions.chatStorageReady,chats:window.sessions.chats})}</pre>;
    }
    createRoot(document.getElementById("root")).render(<Fixture/>);
  ` },
  bundle: true, write: false, format: "esm", jsx: "automatic", define: { "process.env.NODE_ENV": '"test"' },
});
const server = createServer(async (request, response) => {
  if (request.url === "/api/app/agent/chats/delete-session" && request.method === "DELETE") {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({ ok: true, removed: true }));
    return;
  }
  if (request.url.startsWith("/api/app/chats")) {
    response.setHeader("Content-Type", "application/json");
    if (request.method === "POST") {
      if (blocked) { response.statusCode = 409; response.end("{}"); return; }
      let body = "";
      for await (const chunk of request) body += chunk;
      serverChats = JSON.parse(body).chats;
    }
    response.end(JSON.stringify({ ok: true, chats: serverChats, count: serverChats.length,
      sources: [], recoveries: blocked ? [{ status: "invalid" }] : [], writeBlocked: blocked }));
    return;
  }
  if (request.url === "/fixture.js") {
    response.setHeader("Content-Type", "text/javascript");
    response.end(bundle.outputFiles[0].text);
    return;
  }
  response.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const origin = "http://127.0.0.1:" + server.address().port;
  const context = await browser.newContext();
  let page = await context.newPage();
  await page.goto(origin);
  await page.waitForFunction(() => window.sessions?.chatStorageReady);
  await page.evaluate(() => window.stage());
  await page.close();
  page = await context.newPage();
  await page.goto(origin);
  await page.waitForFunction(() => window.sessions?.chatStorageReady);
  const restored = await page.evaluate(() => window.sessions.chats);
  assert.deepEqual(restored[0].items.map(({ id }) => id), ["old", "user-direct", "reply-direct", "user-cancelled", "user-queued"]);
  assert.equal(restored[0].items.find(({ id }) => id === "user-queued").queueStatus, "delivery_unverified");
  assert.equal(restored[0].items.find(({ id }) => id === "user-queued").composerQueued, undefined);
  assert.equal(restored[0].items.find(({ id }) => id === "user-cancelled").queueStatus, "cancelled");
  assert.equal(restored[0].items.find(({ id }) => id === "user-cancelled").attachments[0].id, "external-note");
  const localAttachment = restored[0].items.find(({ id }) => id === "user-direct").attachments[0];
  assert.equal(restored[0].attachmentPayloads[localAttachment.payloadHash].text, "Original note");
  assert.deepEqual(restored.find(({ id }) => id === "new-chat").items.map(({ id }) => id), ["new-input"]);
  const titled = restored.find(({ id }) => id === "titled-chat");
  assert.equal(titled.sessionId, "titled-session");
  assert.equal(titled.title, titledOwner.title);
  assert.deepEqual(titled.items.map(({ id }) => id), ["titled-input"]);
  assert.equal(serverChats[0].items.length, 3, "blocked server copy was left intact");

  blocked = false;
  await page.evaluate(() => window.sessions.reloadChatStorageState());
  await page.waitForFunction(async () => (await window.journal()).length === 2);
  assert.ok((await page.evaluate(() => window.journal())).some(({ owner }) => owner.sessionId === "old-session"));
  assert.equal(serverChats[0].items.filter(({ id }) => id === "user-direct").length, 1);
  assert.equal(serverChats.find(({ id }) => id === "new-chat").items[0].id, "new-input");

  blocked = true;
  await page.evaluate(() => window.stageDeletion());
  await page.reload();
  await page.waitForFunction(() => window.sessions?.chats.some(({ id }) => id === "delete-chat"));
  blocked = false;
  await page.evaluate(() => window.sessions.reloadChatStorageState());
  await page.evaluate(() => window.sessions.deleteChatPermanently("delete-chat"));
  await page.reload();
  await page.waitForFunction(() => window.sessions?.chatStorageReady);
  assert.equal(await page.evaluate(() => window.sessions.chats.some(({ id }) => id === "delete-chat")), false);
  console.log("PASS: exact user inputs survive a blocked restart, queue state is safe, and only canonical acknowledgements prune local records");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
