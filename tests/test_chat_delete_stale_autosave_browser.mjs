import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { build } from "esbuild";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({
  stdin: {
    resolveDir: process.cwd(), loader: "tsx",
    contents: `
import React from "react"; import {createRoot} from "react-dom/client";
import i18n from "i18next"; import {initReactI18next} from "react-i18next";
import {useChatSessions} from "./src/hooks/use-chat-sessions";
await i18n.use(initReactI18next).init({lng:"en",resources:{en:{translation:{chat:{deletePermanentlyPartialSuccess:"Chat record deleted, but runtime history cleanup is incomplete: {{error}}"}}}}});
const noop=()=>{};
function Harness(){ const sessions=useChatSessions({endpoint:location.origin,activeView:"chat",runtimeConnected:true,projectPrefsReady:true,projectPaths:[],customProjectPaths:[],activeProjectPath:"",setActiveProjectPath:noop,setActiveView:noop,setError:(error)=>window.__error=error,expandProjectGroup:noop,initialChatState:{chats:[],activeChatId:""}}); window.sessions=sessions; return <div/>; }
window.__storageTrace=[]; const originalDebug=console.debug; console.debug=(...args)=>{if(args[0]==="[vrcforge:chat-storage]")window.__storageTrace.push(args[1]);else originalDebug(...args)};
createRoot(document.getElementById("root")).render(<Harness/>);
`,
  }, bundle: true, write: false, format: "esm", jsx: "automatic",
  define: {"process.env.NODE_ENV": '"test"'},
  plugins: [{name:"hook-fixture",setup(builder){builder.onLoad({filter:/use-chat-sessions\\.ts$/},async(args)=>({contents:await readFile(args.path,"utf8"),loader:"tsx"}))}}],
});
const makeChat=(id,sessionId,title)=>({id,sessionId,title,projectPath:"",projectType:"general",revision:0,createdAt:"2026-01-01T00:00:00Z",updatedAt:"2026-01-01T00:00:00Z",items:[{id:`user-${id}`,type:"user",text:title}]});
const initialChats=[makeChat("chat-target","session-target","Delete target"),makeChat("chat-stale","session-stale","Deleted by another profile")];
let chats=[...initialChats];
const posts=[]; const runtimeDeletes=[]; const postStatuses=[];
let runtimeDeleteStatus=200;
const digest=()=>Object.fromEntries(chats.map(chat=>[chat.id,createHash("sha256").update(JSON.stringify(chat)).digest("hex")]));
const shapeOf=value=>value&&typeof value==="object"&&!Array.isArray(value)?Object.fromEntries(Object.entries(value).filter(([key])=>key!=="items").map(([key,child])=>[key,shapeOf(child)])):true;
const server=createServer(async(request,response)=>{
 if(request.url==="/fixture.js"){response.setHeader("Content-Type","text/javascript");response.end(bundle.outputFiles[0].text);return;}
 if(request.url==="/fixture/state"){response.setHeader("Content-Type","application/json");response.end(JSON.stringify({postCount:posts.length,runtimeDeleteCount:runtimeDeletes.length}));return;}
 if(request.url?.startsWith("/api/app/chats")&&request.method==="GET"){response.setHeader("Content-Type","application/json");response.end(JSON.stringify({ok:true,chats,count:chats.length,sources:[],recoveries:[],writeBlocked:false,chatDigests:digest(),chatMetadataShapes:Object.fromEntries(chats.map(chat=>[chat.id,shapeOf(chat)]))}));return;}
 if(request.url==="/fixture/drop-stale"&&request.method==="POST"){chats=chats.filter(chat=>chat.id!=="chat-stale");response.end("ok");return;}
 if(request.url==="/fixture/reconcile"&&request.method==="POST"){chats=initialChats.map(chat=>({...chat}));response.end("ok");return;}
 if(request.url?.startsWith("/api/app/agent/chats/")&&request.method==="DELETE"){
  runtimeDeletes.push(decodeURIComponent(request.url.split("/").at(-1)));
  response.statusCode=runtimeDeleteStatus;response.setHeader("Content-Type","application/json");
  response.end(JSON.stringify(runtimeDeleteStatus===200?{ok:true,removed:true}:{detail:"runtime cleanup failed"}));return;
 }
 if(request.url==="/api/app/chats"&&request.method==="POST"){
  let raw="";for await(const chunk of request)raw+=chunk;const body=JSON.parse(raw);
  const serverIds=new Set(chats.map(chat=>chat.id));const requestedIds=body.chats.map(chat=>chat.id);const patchIds=Object.keys(body.chatPatches||{});
  const unknownPatchIds=patchIds.filter(id=>!serverIds.has(id));const status=postStatuses.shift()??(unknownPatchIds.length?422:200);
  posts.push({serverIds:[...serverIds],requestedIds,patchIds,unknownPatchIds,status});
  if(status>=400){response.statusCode=status;response.setHeader("Content-Type","application/json");response.end(JSON.stringify({detail:status===422?"chat patches do not exactly cover existing requested chats":"temporary storage error"}));return;}
  chats=chats.filter(chat=>requestedIds.includes(chat.id));
  response.setHeader("Content-Type","application/json");response.end(JSON.stringify({ok:true,sourceRevisions:[],chatDigests:digest(),chatMetadataShapes:Object.fromEntries(chats.map(chat=>[chat.id,shapeOf(chat)]))}));return;
 }
 response.setHeader("Content-Type","text/html");response.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
});
let browser;
try{
 await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
 browser=await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL||"msedge"});
 const page=await browser.newPage();const origin=`http://127.0.0.1:${server.address().port}`;
 await page.addInitScript(()=>localStorage.setItem("vrcforge_developer_options_enabled","true"));
 await page.route("**/*",route=>route.request().url().startsWith(origin)?route.continue():route.abort());
 await page.goto(origin);await page.waitForFunction(()=>window.sessions?.chatStorageReady===true&&window.sessions.chats.length===2);
 await page.evaluate(()=>fetch("/fixture/drop-stale",{method:"POST"}));
 await page.evaluate(()=>{void window.sessions.deleteChatPermanently("chat-target")});
 await page.waitForFunction(()=>window.__error?.includes("chat patches do not exactly cover")||window.__storageTrace.some(event=>event.stage==="failed"));
 await page.waitForTimeout(1900);
 const state=await page.evaluate(()=>({ids:window.sessions.chats.map(chat=>chat.id),error:window.__error,trace:window.__storageTrace}));
 assert.ok(posts.length>=1,"deleting a chat sends the durable chat-list update");
 assert.deepEqual(posts[0].serverIds,["chat-target"]);
 assert.ok(posts[0].unknownPatchIds.includes("chat-stale"),"replay captures the stale server-deleted chat id still present in the UI request");
 assert.equal(posts[0].patchIds.length,1);
 assert.equal(state.ids.includes("chat-target"),true,"failed durable deletion restores the chat row");
 assert.equal(runtimeDeletes.length,0,"runtime history must remain when durable chat deletion is rejected");
 assert.equal(posts.length,1,"a stable 422 must not resend the unchanged chat generation every autosave tick");
 const explicitRetry=await page.evaluate(async()=>{try{await window.sessions.persistChatsNow();return false}catch{return true}});
 assert.equal(explicitRetry,true,"an explicit save remains available after an unchanged stable rejection");
 assert.equal(posts.length,2,"the explicit save makes one new attempt");
 await page.evaluate(()=>fetch("/fixture/reconcile",{method:"POST"}));
 postStatuses.push(503,422);
 await page.evaluate(()=>window.sessions.updateChat("chat-stale",chat=>({...chat,title:"edited after rejection"})));
 await page.waitForFunction(()=>window.__storageTrace.filter(event=>event.kind==="save"&&event.stage==="failed").length>=4);
 await page.waitForTimeout(1100);
 assert.equal(posts.length,4,"a new edit retries; a temporary 5xx also retries once before a stable 422 stops the same generation");
 assert.deepEqual(posts.slice(2).map(post=>post.status),[503,422]);
 await page.evaluate(()=>fetch("/fixture/reconcile",{method:"POST"}));
 runtimeDeleteStatus=500;
 await page.evaluate(()=>{void window.sessions.deleteChatPermanently("chat-target")});
 await page.waitForFunction(async()=>{const state=await fetch("/fixture/state").then(response=>response.json());return state.runtimeDeleteCount===1&&state.postCount===5});
 await page.waitForTimeout(50);
 const cleanupFailure=await page.evaluate(()=>({ids:window.sessions.chats.map(chat=>chat.id),error:window.__error}));
 assert.deepEqual(posts[4].unknownPatchIds,[]);
 assert.deepEqual(runtimeDeletes,["session-target"],"runtime cleanup occurs only after transcript deletion commits");
 assert.deepEqual(cleanupFailure.ids,["chat-stale"],"a later runtime cleanup error cannot resurrect a durably deleted chat");
 assert.equal(cleanupFailure.error,"Chat record deleted, but runtime history cleanup is incomplete: runtime cleanup failed");
 console.log(JSON.stringify({status:"passed",staleRequest:posts[0],stable422AutosaveRequests:1,explicitRetry,transientRetryStatuses:posts.slice(2,4).map(post=>post.status),runtimeDeletes,localIdsAfterCleanupFailure:cleanupFailure.ids,cleanupFailure:cleanupFailure.error}));
}finally{await browser?.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
