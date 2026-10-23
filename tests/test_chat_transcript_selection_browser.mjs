import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { build } from "esbuild";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React from "react";
    import {createRoot} from "react-dom/client";
    import i18n from "i18next";
    import {initReactI18next} from "react-i18next";
    import {useChatSessions} from "./src/hooks/use-chat-sessions";
    import * as transcriptCache from "./src/lib/chat-transcript-cache";
    import {filterPersistableChats} from "./src/lib/chat-thread";
    window.transcriptCache=transcriptCache;
    window.persistableChat=chat=>filterPersistableChats([chat])[0];
    window.historyReads=0;
    window.instrumentHistories=()=>window.testApi.chats.forEach(chat=>{
      chat.items=new Proxy(chat.items,{get(target,key,receiver){
        if(typeof key==="string" && /^(0|[1-9][0-9]*)$/.test(key)) window.historyReads++;
        return Reflect.get(target,key,receiver);
      }});
    });
    await i18n.use(initReactI18next).init({lng:"en",resources:{en:{translation:{}}}});
    function Fixture(){
      const sessions=useChatSessions({endpoint:location.origin,activeView:"chat",runtimeConnected:true,projectPrefsReady:true,
        projectPaths:[],customProjectPaths:[],activeProjectPath:"",setActiveProjectPath:()=>{},setActiveView:()=>{},setError:()=>{},expandProjectGroup:()=>{},initialChatState:{chats:[],activeChatId:""}});
      window.testApi=sessions;
      return <pre id="state">{JSON.stringify({title:sessions.displayChat?.title || "",pending:sessions.activeChatPending,
        items:sessions.displayChat?.items || [],sidebar:sessions.chatSidebar.temporaryChats.map(c=>c.id)})}</pre>;
    }
    createRoot(document.getElementById("root")).render(<Fixture/>);
  `}, bundle: true, write: false, format: "esm", jsx: "automatic", define: {"process.env.NODE_ENV": '"test"'},
  plugins: [{name:"hold-real-cache-read",setup(build){
    build.onResolve({filter:/chat-transcript-cache(?:\.ts)?$/},args=>args.importer.endsWith("use-chat-sessions.ts")
      ? {path:"held-cache-read",namespace:"test-cache-read"}:undefined);
    build.onLoad({filter:/.*/,namespace:"test-cache-read"},()=>({loader:"ts",resolveDir:process.cwd(),contents:`
      import {readChatTranscriptCache as readReal,writeChatTranscriptCache,removeChatTranscriptCache} from ${JSON.stringify(resolve("src/lib/chat-transcript-cache.ts"))};
      export {writeChatTranscriptCache,removeChatTranscriptCache};
      export async function readChatTranscriptCache(...args){
        const result=await readReal(...args);
        if(window.heldChatId===args[1]){
          window.cacheReadHeld=true;
          await new Promise(resolve=>window.releaseHeldRead=resolve);
        }
        return result;
      }
    `}));
  }}],
});

const cold = {id:"cold",sessionId:"cold-session",title:"Existing cold conversation",projectPath:"",projectType:"general",revision:0,
  createdAt:"2026-01-01T00:00:00Z",updatedAt:"2026-01-01T00:00:00Z",items:[]};
const warmA = {...cold,id:"warm-a",sessionId:"session-a",title:"Cached A",items:[{id:"a-user",type:"user",text:"original cached A body"}]};
const warmB = {...cold,id:"warm-b",sessionId:"session-b",title:"Cached B",items:[{id:"b-user",type:"user",text:"cached B body"}]};
const freshA = {...warmA,title:"Authoritative A",items:[{id:"a-user",type:"user",text:"authoritative refreshed A body"}]};
const unrelatedHistories=Array.from({length:12},(_,chatIndex)=>({...cold,id:`history-${chatIndex}`,sessionId:`history-session-${chatIndex}`,
  title:`Unrelated history ${chatIndex}`,items:Array.from({length:120},(_,itemIndex)=>({id:`history-${chatIndex}-${itemIndex}`,type:"user",text:"unrelated history"}))}));
let release;
const held = new Promise(resolve => { release = resolve; });
let posts = 0;
let gets = 0;
const server = createServer(async (req,res) => {
  if (req.url === "/fixture.js") {res.setHeader("Content-Type","text/javascript");res.end(bundle.outputFiles[0].text);return;}
  if (req.url?.startsWith("/api/app/chats")) {
    if (req.method === "POST") {posts += 1;res.setHeader("Content-Type","application/json");res.end(JSON.stringify({ok:true,sourceRevisions:[]}));return;}
    gets += 1;
    await held;
    res.setHeader("Content-Type","application/json");
    res.end(JSON.stringify({ok:true,chats:[cold,freshA,warmB,...unrelatedHistories],sources:[],recoveries:[],writeBlocked:false}));return;
  }
  res.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
});
await new Promise(resolve => server.listen(0,"127.0.0.1",resolve));
let browser;
try {
  browser = await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL || "msedge"});
  const context=await browser.newContext();
  const page = await context.newPage();
  const origin = `http://127.0.0.1:${server.address().port}`;
  await page.goto(origin);
  await page.evaluate(chat => localStorage.setItem("vrcforge_chat_sidebar_cache",JSON.stringify([chat])),cold);
  await page.reload();
  await page.waitForFunction(() => window.testApi?.chatSidebar.temporaryChats.some(c=>c.id==="cold"));
  await page.evaluate(() => window.testApi.openChat(window.testApi.chatSidebar.temporaryChats.find(c=>c.id==="cold")));
  assert.equal(await page.evaluate(() => window.testApi.displayChat?.title),cold.title,
    "a selected existing sidebar conversation must show its title before authority returns");
  assert.equal(await page.evaluate(() => window.testApi.activeChatPending),true,
    "a metadata-only selected conversation is restoring, not a new task");
  assert.equal(await page.evaluate(() => window.testApi.activeChat),null);
  assert.deepEqual(await page.evaluate(() => window.testApi.chats),[],"display-only cache must not enter authoritative chats");
  assert.equal(posts,0,"selecting a cached conversation must not save it as an authoritative addition");

  // Real IndexedDB survives reload, while the authoritative snapshot remains held.
  await page.evaluate(async ({a,b,c})=>{
    await window.transcriptCache.writeChatTranscriptCache(location.origin,a);
    await window.transcriptCache.writeChatTranscriptCache(location.origin,b);
    localStorage.setItem("vrcforge_chat_sidebar_cache",JSON.stringify([a,b,c]));
  },{a:warmA,b:warmB,c:cold});
  await page.reload();
  await page.waitForFunction(() => window.testApi?.chatSidebar.temporaryChats.some(c=>c.id==="warm-a"));
  await page.evaluate(() => window.testApi.openChat(window.testApi.chatSidebar.temporaryChats.find(c=>c.id==="warm-a")));
  await page.waitForFunction(() => window.testApi.displayChat?.items[0]?.text==="original cached A body");
  assert.equal(await page.evaluate(() => window.testApi.activeChatPending),true);
  assert.equal(await page.evaluate(() => window.testApi.activeChat),null,"cached display must never impersonate authoritative state");
  assert.deepEqual(await page.evaluate(() => window.testApi.chats),[]);
  assert.equal(posts,0,"IndexedDB restore must not create a server save candidate");

  // Delay completion of a real A cache read, then select B before A resolves.
  await page.reload();
  await page.waitForFunction(() => window.testApi?.chatSidebar.temporaryChats.some(c=>c.id==="warm-a"));
  await page.evaluate(() => {
    window.heldChatId="warm-a";
    window.testApi.openChat(window.testApi.chatSidebar.temporaryChats.find(c=>c.id==="warm-a"));
  });
  await page.waitForFunction(() => window.cacheReadHeld);
  await page.evaluate(() => window.testApi.openChat(window.testApi.chatSidebar.temporaryChats.find(c=>c.id==="warm-b")));
  await page.waitForFunction(() => window.testApi.displayChat?.items[0]?.text==="cached B body");
  await page.evaluate(() => {window.heldChatId="";window.releaseHeldRead();});
  await page.waitForTimeout(50);
  assert.equal(await page.evaluate(() => window.testApi.displayChat?.id),"warm-b","stale A read must not replace selected B");
  assert.equal(await page.evaluate(() => window.testApi.displayChat?.items[0]?.text),"cached B body");
  assert.equal(posts,0);

  if(process.env.VRCFORGE_SELECTION_REPLAY_PATH){
    const rawPath=resolve(process.env.VRCFORGE_SELECTION_REPLAY_PATH);
    const rawBytes=await readFile(rawPath);
    const stored=JSON.parse(rawBytes.toString("utf8").replace(/^\uFEFF/,""));
    const candidates=Array.isArray(stored)?stored:stored.chats;
    assert.ok(Array.isArray(candidates),"the original project store must contain chats");
    const indexedOriginal=candidates.find(chat=>chat.title?.startsWith("限时检查") && chat.items?.length)
      || candidates.find(chat=>chat.items?.length);
    assert.ok(indexedOriginal,"the original project store must contain a nonempty transcript");
    // Reuse the production archive decoder, including hash, size and chat binding
    // validation. Reading an archive index alone is not reading its transcript.
    const decoded=execFileSync(process.env.VRCFORGE_PYTHON || "python",["-B","-c",
      "import json,sys; from chat_transcript_archive import decode_chats; print(json.dumps(decode_chats(json.load(sys.stdin),sys.argv[1]),ensure_ascii=False))",rawPath],
      {input:JSON.stringify([indexedOriginal]),encoding:"utf8",maxBuffer:64*1024*1024,env:{...process.env,PYTHONIOENCODING:"utf-8"}});
    const [original]=JSON.parse(decoded);
    const expected=await page.evaluate(chat=>window.persistableChat(chat),original);
    assert.ok(expected?.items.length,"the original snapshot must retain durable conversation content");
    await page.evaluate(async({chat,metadata})=>{
      await window.transcriptCache.writeChatTranscriptCache(location.origin,chat);
      localStorage.setItem("vrcforge_chat_sidebar_cache",JSON.stringify(metadata));
    },{chat:expected,metadata:[expected,warmA,warmB,cold]});
    const originalPage=await page.context().newPage();
    await originalPage.goto(origin);
    await originalPage.waitForFunction(id=>window.testApi && [
      ...window.testApi.chatSidebar.temporaryChats,
      ...[...window.testApi.chatSidebar.projectChatsByPath.values()].flat(),
    ].some(chat=>chat.id===id),expected.id);
    await originalPage.evaluate(id=>{
      const row=[...window.testApi.chatSidebar.temporaryChats,...[...window.testApi.chatSidebar.projectChatsByPath.values()].flat()].find(chat=>chat.id===id);
      window.testApi.openChat(row);
    },expected.id);
    await originalPage.waitForFunction(({id,count})=>window.testApi.displayChat?.id===id && window.testApi.displayChat.items.length===count,
      {id:expected.id,count:expected.items.length});
    assert.equal(await originalPage.evaluate(()=>window.testApi.displayChat.title),expected.title,"original selected title must paint before authority");
    assert.deepEqual(await originalPage.evaluate(()=>window.testApi.displayChat.items),expected.items,"original durable transcript content must restore exactly");
    assert.equal(await originalPage.evaluate(()=>window.testApi.activeChat),null);
    assert.equal(await originalPage.evaluate(()=>window.testApi.activeChatPending),true);
    assert.deepEqual(await originalPage.evaluate(()=>window.testApi.chats),[],"original cached transcript must remain outside authoritative chats");
    assert.equal(posts,0,"replaying the original transcript must not save cached data to the server");
    const sourceSha256=createHash("sha256").update(rawBytes).digest("hex");
    const sourceSha256After=createHash("sha256").update(await readFile(rawPath)).digest("hex");
    assert.equal(sourceSha256After,sourceSha256,"the original transcript store must remain unchanged by read-only replay");
    const receipt={sourceSha256,sourceSha256After,rawStoreUnchanged:true,
      originalItemCount:original.items.length,durableItemCount:expected.items.length,
      decodedSnapshotBytes:Buffer.byteLength(JSON.stringify(original)),cachedDurableSnapshotBytes:Buffer.byteLength(JSON.stringify(expected)),
      verifiedArchiveDigests:indexedOriginal.items.flatMap(item=>item.__vrcforge_chat_transcript_archive__?[item.__vrcforge_chat_transcript_archive__.digest]:[]),
      durableItemsSha256:createHash("sha256").update(JSON.stringify(expected.items)).digest("hex"),
      exactDurableItemsMatch:true,exactTitleMatch:true,activeChatNull:true,activeChatPending:true,
      authoritativeChatsEmpty:true,serverPostsBeforeAuthority:posts};
    if(process.env.VRCFORGE_SELECTION_REPLAY_EVIDENCE){
      await writeFile(resolve(process.env.VRCFORGE_SELECTION_REPLAY_EVIDENCE),JSON.stringify(receipt,null,2)+"\n");
    }
    console.log(`PASS original project transcript replay (${receipt.originalItemCount} original / ${receipt.durableItemCount} durable items, exact content; source SHA-256 ${receipt.sourceSha256})`);
    await originalPage.close();
  }

  await page.evaluate(() => window.testApi.openChat(window.testApi.chatSidebar.temporaryChats.find(c=>c.id==="warm-a")));
  await page.waitForFunction(() => window.testApi.displayChat?.items[0]?.text==="original cached A body");
  release();
  await page.waitForFunction(() => window.testApi.chatStorageReady);
  await page.waitForFunction(() => window.testApi.displayChat?.title==="Authoritative A");
  assert.equal(await page.evaluate(() => window.testApi.activeChatPending),false);
  assert.equal(await page.evaluate(() => window.testApi.displayChat?.items[0]?.text),freshA.items[0].text);
  await page.waitForFunction(async () => (await window.transcriptCache.readChatTranscriptCache(location.origin,"warm-a",""))?.items[0]?.text==="authoritative refreshed A body");
  const requestsBeforeWarmSwitch=gets;
  await page.evaluate(() => {
    window.instrumentHistories();window.historyReads=0;
    window.testApi.openChat(window.testApi.chats.find(c=>c.id==="warm-b"));
  });
  await page.waitForFunction(() => window.testApi.displayChat?.id==="warm-b");
  assert.equal(gets,requestsBeforeWarmSwitch,"loaded warm switching must not refetch the whole snapshot");
  assert.ok(await page.evaluate(() => window.historyReads)<10,"switching must not scan every stored history");
  const workspaceSource=await readFile(resolve("src/components/chat/chat-workspace.tsx"),"utf8");
  const appSource=await readFile(resolve("src/App.tsx"),"utf8");
  assert.match(workspaceSource,/conversationPending\s*\?\s*\([\s\S]*?data-chat-transcript-loading[\s\S]*?:\s*projectPromptTitle\s*\?/,
    "an unresolved existing chat must render the loading skeleton before considering the new-chat welcome");
  assert.match(appSource,/conversationPending=\{activeChatPending\}/,"the workspace must receive selected-chat recovery state");
  console.log("PASS cold metadata selection, IndexedDB warm reload, async selection race, authority refresh, cache isolation and loaded switching");
} finally {
  release();
  await browser?.close();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
