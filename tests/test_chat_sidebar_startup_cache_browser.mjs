import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React from "react";
    import {createRoot} from "react-dom/client";
    import i18n from "i18next";
    import {initReactI18next} from "react-i18next";
    import {useChatSessions} from "./src/hooks/use-chat-sessions";
    await i18n.use(initReactI18next).init({lng:"en",resources:{en:{translation:{}}}});
    function Fixture(){
      const sessions=useChatSessions({endpoint:location.origin,activeView:"chat",runtimeConnected:true,projectPrefsReady:true,
        projectPaths:[],customProjectPaths:[],activeProjectPath:"",setActiveProjectPath:()=>{},setActiveView:()=>{},setError:()=>{},expandProjectGroup:()=>{},initialChatState:{chats:[],activeChatId:""}});
      window.testApi=sessions;
      return <pre id="state">{JSON.stringify({ids:sessions.chatSidebar.temporaryChats.map((chat)=>chat.id),active:sessions.activeChatId})}</pre>;
    }
    createRoot(document.getElementById("root")).render(<Fixture/>);
  `}, bundle:true, write:false, format:"esm", jsx:"automatic", define:{"process.env.NODE_ENV":'"test"'},
});

let release;
const held = new Promise((resolve) => { release = resolve; });
let requests = 0;
const server = createServer(async (req, res) => {
  if (req.url === "/fixture.js") { res.setHeader("Content-Type", "text/javascript"); res.end(bundle.outputFiles[0].text); return; }
  if (req.url?.startsWith("/api/app/chats")) {
    requests += 1;
    await held;
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ok:true,chats:[{id:"remote",sessionId:"s",title:"Remote",projectPath:"",projectType:"general",revision:0,createdAt:"2026-01-01T00:00:00Z",updatedAt:"2026-01-01T00:00:00Z",items:[]}],sources:[],recoveries:[],writeBlocked:false}));
    return;
  }
  res.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({headless:true, channel:process.env.VRCFORGE_BROWSER_CHANNEL || "msedge"});
  const page = await browser.newPage();
  const origin = `http://127.0.0.1:${server.address().port}`;
  await page.goto(origin);
  await page.evaluate(() => localStorage.setItem("vrcforge_chat_sidebar_cache", JSON.stringify([{id:"cached",sessionId:"",title:"Cached conversation",projectPath:"",projectType:"general",pinned:false,archived:false,createdAt:"2026-01-01T00:00:00Z",updatedAt:"2026-01-01T00:00:00Z"}])));
  await page.reload();
  await page.waitForFunction(() => JSON.parse(document.querySelector("#state").textContent).ids.includes("cached"));
  assert.equal(await page.evaluate(() => window.testApi.activeChatId), "", "startup remains a new chat");
  await page.evaluate(() => window.testApi.openChat(window.testApi.chatSidebar.temporaryChats.find((chat) => chat.id === "cached")));
  assert.equal(await page.evaluate(() => window.testApi.activeChatId), "cached", "cached row is selectable before authority returns");
  release();
  await page.waitForFunction(() => JSON.parse(document.querySelector("#state").textContent).ids.includes("remote"));
  assert.equal(await page.evaluate(() => localStorage.getItem("vrcforge_chat_sidebar_cache")).then((value) => value.includes("remote")), true);
  console.log(`PASS startup sidebar cache and deferred authority (${requests} fetch)`);
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
