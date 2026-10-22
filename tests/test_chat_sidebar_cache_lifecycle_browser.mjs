import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({ stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
import React from "react"; import {createRoot} from "react-dom/client"; import i18n from "i18next"; import {initReactI18next} from "react-i18next"; import {useChatSessions} from "./src/hooks/use-chat-sessions";
await i18n.use(initReactI18next).init({lng:"en",resources:{en:{translation:{}}}});
function Fixture(){const s=useChatSessions({endpoint:location.origin,activeView:"chat",runtimeConnected:true,projectPrefsReady:true,projectPaths:[],customProjectPaths:[],activeProjectPath:"",setActiveProjectPath:()=>{},setActiveView:()=>{},setError:()=>{},expandProjectGroup:()=>{},initialChatState:{chats:[],activeChatId:""}});window.testApi=s;return <pre id="state">{JSON.stringify({ids:s.chats.map(c=>c.id),sidebar:s.chatSidebar.temporaryChats.map(c=>c.id)})}</pre>}; createRoot(document.getElementById("root")).render(<Fixture/>);
`}, bundle:true, write:false, format:"esm", jsx:"automatic", define:{"process.env.NODE_ENV":'"test"'} });

const cached={id:"cached",sessionId:"",title:"Cached",projectPath:"",projectType:"general",revision:0,createdAt:"2026-01-01T00:00:00Z",updatedAt:"2026-01-01T00:00:00Z",items:[]};
const deleted={...cached,id:"deleted",sessionId:"runtime",title:"Deleted"};
let mode="fail", posts=0, deletes=0;
const server=createServer(async(req,res)=>{
  if(req.url==="/fixture.js"){res.setHeader("Content-Type","text/javascript");res.end(bundle.outputFiles[0].text);return;}
  if(req.url?.startsWith("/api/app/chats")){
    if(req.method==="POST"){posts++;let body="";for await(const chunk of req)body+=chunk;const payload=JSON.parse(body);if(payload.chats.some(c=>c.id==="deleted"))throw new Error("deleted chat was saved");res.setHeader("Content-Type","application/json");res.end(JSON.stringify({ok:true,sourceRevisions:[]}));return;}
    if(mode==="fail"){res.statusCode=503;res.end(JSON.stringify({detail:"offline"}));return;}
    if(mode==="merge")await new Promise(r=>setTimeout(r,500));
    res.setHeader("Content-Type","application/json");res.end(JSON.stringify({ok:true,chats:mode==="delete"?[deleted]:[],sources:[],recoveries:[],writeBlocked:false}));return;
  }
  if(req.url?.startsWith("/api/app/agent/chats/")&&req.method==="DELETE"){deletes++;res.setHeader("Content-Type","application/json");res.end(JSON.stringify({ok:true,removed:true}));return;}
  mode=new URL(req.url||"/","http://127.0.0.1").searchParams.get("mode")||"fail";res.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
});
await new Promise(r=>server.listen(0,"127.0.0.1",r)); let browser;
try{browser=await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL||"msedge"});const page=await browser.newPage();const origin=`http://127.0.0.1:${server.address().port}`;
  await page.goto(`${origin}/?mode=fail`,{waitUntil:"domcontentloaded"});await page.evaluate(v=>localStorage.setItem("vrcforge_chat_sidebar_cache",JSON.stringify([v])),cached);await page.reload();await page.waitForFunction(()=>JSON.parse(document.querySelector("#state").textContent).sidebar.includes("cached"));assert.match(await page.evaluate(()=>localStorage.getItem("vrcforge_chat_sidebar_cache")),/cached/);assert.equal(posts,0);assert.equal(deletes,0);
  mode="merge";await page.goto(`${origin}/?mode=merge`,{waitUntil:"domcontentloaded"});await page.evaluate(v=>localStorage.setItem("vrcforge_chat_sidebar_cache",JSON.stringify([v])),cached);await page.reload();await page.waitForFunction(()=>JSON.parse(document.querySelector("#state").textContent).sidebar.includes("cached"));await page.evaluate(()=>window.testApi.ensureActiveChat());assert.match(await page.evaluate(()=>localStorage.getItem("vrcforge_chat_sidebar_cache")),/cached/);
  mode="delete";await page.goto(`${origin}/?mode=delete`,{waitUntil:"domcontentloaded"});await page.evaluate(v=>localStorage.setItem("vrcforge_chat_sidebar_cache",JSON.stringify([v])),deleted);await page.reload();await page.waitForFunction(()=>JSON.parse(document.querySelector("#state").textContent).sidebar.includes("deleted"));await page.evaluate(()=>window.testApi.deleteChatPermanently("deleted"));await page.waitForFunction(()=>window.testApi.chats.length===0);assert.equal(deletes,1);assert.equal(posts>0,true);assert.doesNotMatch(await page.evaluate(()=>localStorage.getItem("vrcforge_chat_sidebar_cache")||""),/deleted/);console.log("PASS sidebar cache failure, merge, and deferred delete lifecycle");
}finally{await browser?.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
