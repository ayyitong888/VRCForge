import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { build } from "esbuild";
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.VRCFORGE_PLAYWRIGHT_PATH||"playwright");
const app=readFileSync("src/App.tsx","utf8");
const dashboard=readFileSync("dashboard_server.py","utf8");
assert.match(app,/if \(eventType === "chatStorageChanged"\) \{\s*notifyChatStorageChanged\(event\.payload\?\.originId\);\s*\}/);
assert.match(dashboard,/async def write_chat_transcripts[\s\S]*?await EVENT_BUS\.broadcast\("chatStorageChanged", \{"source": "app", "originId": request\.chat_origin_id\}\)/);
const bundle=await build({stdin:{resolveDir:process.cwd(),loader:"tsx",contents:`
import React from 'react';import {createRoot} from 'react-dom/client';
import i18n from 'i18next';import {initReactI18next} from 'react-i18next';
import {useChatSessions} from './src/hooks/use-chat-sessions';
import {chatStorageOriginId} from './src/lib/chat-storage-origin';
await i18n.use(initReactI18next).init({lng:'en',resources:{en:{translation:{}}}});
const noop=()=>{};const paths=[];const initial={chats:[],activeChatId:''};
function Fixture(){const sessions=useChatSessions({endpoint:location.origin,activeView:'chat',runtimeConnected:true,
projectPrefsReady:true,projectPaths:paths,customProjectPaths:paths,activeProjectPath:'',setActiveProjectPath:noop,
setActiveView:noop,setError:noop,expandProjectGroup:noop,initialChatState:initial});
window.notify=sessions.notifyChatStorageChanged;
window.originId=chatStorageOriginId();
window.persist=sessions.persistChatsNow;
window.localEdit=()=>sessions.updateChat('chat',chat=>({...chat,title:'local-dirty'}));
return <pre id="state">{JSON.stringify({chats:sessions.chats,blocked:sessions.chatPersistenceBlocked})}</pre>}
const root=createRoot(document.getElementById('root'));root.render(<Fixture/>);window.unmount=()=>root.unmount();
`},bundle:true,write:false,format:"esm",jsx:"automatic",define:{"process.env.NODE_ENV":'"test"'},plugins:(process.env.VRCFORGE_TEST_LEGACY_CHAT_ECHO==="1"||process.env.VRCFORGE_TEST_LEGACY_CHAT_RELOAD_QUEUE==="1")?[{
name:"legacy-chat-storage-control",setup(builder){builder.onLoad({filter:/use-chat-sessions\.ts$/},async(args)=>{
let contents=readFileSync(args.path,"utf8");
if(process.env.VRCFORGE_TEST_LEGACY_CHAT_ECHO==="1")contents=contents.replace("    if (originId && originId === chatStorageOriginId()) return;\n","");
if(process.env.VRCFORGE_TEST_LEGACY_CHAT_RELOAD_QUEUE==="1")contents=contents.replace(/  const notifyChatStorageChanged = useCallback\(\(originId\?: string\) => \{[\s\S]*?\n  \}, \[\]\);/,`  const notifyChatStorageChanged = useCallback((originId?: string) => {
    if (originId && originId === chatStorageOriginId()) return;
    if (!externalReloadActiveRef.current) return;
    if (externalReloadTimerRef.current !== null) window.clearTimeout(externalReloadTimerRef.current);
    externalReloadTimerRef.current = window.setTimeout(() => {
      externalReloadTimerRef.current = null;
      if (externalReloadActiveRef.current) void externalReloadRef.current().catch(() => undefined);
    }, 150);
  }, []);`);
return {contents,loader:"tsx"};
});}
}]:[]});
let gets=0,posts=0,postOriginIds=[];
const chat={id:"chat",sessionId:"session",title:"initial",revision:0,projectPath:"",projectType:"general",
createdAt:"2026-01-01T00:00:00Z",updatedAt:"2026-01-01T00:00:00Z",items:[{id:"u1",type:"user",text:"initial",createdAt:"2026-01-01T00:00:00Z"}]};
let chats=[chat];
let nextGetHold=null;
function holdNextGet(){
let release;let markStarted;const gate=new Promise(resolve=>{release=resolve;});
const started=new Promise(resolve=>{markStarted=resolve;});nextGetHold={gate,markStarted};return {started,release};}
const server=createServer(async(req,res)=>{
if(req.url.startsWith('/api/app/chats')){res.setHeader('Content-Type','application/json');
if(req.method==='POST'){posts++;let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);postOriginIds.push(body.chatOriginId);chats=body.chats;res.end(JSON.stringify({ok:true,sourceRevisions:[]}));return;}
gets++;const snapshot=chats;const held=nextGetHold;nextGetHold=null;if(held){held.markStarted();await held.gate;}
res.end(JSON.stringify({ok:true,chats:snapshot,count:snapshot.length,sources:[],recoveries:[],writeBlocked:false}));return;}
if(req.url==='/fixture.js'){res.setHeader('Content-Type','text/javascript');res.end(bundle.outputFiles[0].text);return;}
res.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
let browser;
try{
browser=await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL||'msedge'});
const page=await browser.newPage();const origin='http://127.0.0.1:'+server.address().port;
await page.route('**/*',route=>route.request().url().startsWith(origin)?route.continue():route.abort());
await page.goto(origin);await page.waitForFunction(()=>document.querySelector('#state')?.textContent.includes('initial'));
const before=gets;
const originId=await page.evaluate(()=>window.originId);
await page.evaluate((id)=>{window.notify(id);window.notify(id);window.notify(id);},originId);
await page.waitForTimeout(220);assert.equal(gets,before,'the originating webview ignores its own precise storage echo');
const beforeOwnWrite=posts;
await page.evaluate(()=>window.persist());
assert.equal(posts,beforeOwnWrite+1,'explicit persistence should complete without waiting behind a self-triggered reload');
assert.equal(postOriginIds.at(-1),originId,'the write carries the exact per-webview origin identity');
await page.evaluate((id)=>{window.notify(id);window.notify(id);window.notify(id);},originId);
await page.waitForTimeout(220);assert.equal(gets,before,'self write echoes do not queue a full reload');
chats=[{...chats[0],items:[...chats[0].items,{id:'u2',type:'user',text:'telegram-visible',createdAt:'2026-01-01T00:00:01Z'}]}];
const heldReload=holdNextGet();
await page.evaluate(()=>{window.notify();window.notify();window.notify();});
await heldReload.started;
chats=[{...chats[0],items:[...chats[0].items,{id:'u3',type:'user',text:'telegram-visible-latest',createdAt:'2026-01-01T00:00:02Z'}]}];
for(let index=0;index<3;index++){await page.evaluate(()=>window.notify());await page.waitForTimeout(220);}
assert.equal(gets,before+1,'external events during an active reload must not start concurrent reads');
heldReload.release();
await page.waitForFunction(()=>document.querySelector('#state')?.textContent.includes('telegram-visible-latest'));
await page.waitForTimeout(250);assert.equal(gets,before+2,'events during an active reload collapse into one trailing refresh');
await page.evaluate(()=>window.localEdit());
chats=[{...chats[0],title:'remote-dirty'}];
await page.evaluate(()=>window.notify());
await page.waitForFunction(()=>JSON.parse(document.querySelector('#state').textContent).blocked===true);
assert.equal(await page.evaluate(()=>JSON.parse(document.querySelector('#state').textContent).chats[0].title),'local-dirty');
const beforeUnmount=gets;
await page.evaluate(()=>{window.notify();window.unmount();window.notify();});
await page.waitForTimeout(250);assert.equal(gets,beforeUnmount,'unmount must cancel pending reload');
console.log('PASS chatStorageChanged: own-echo suppression, external reload coalescing, latest-state refresh, dirty conflict protection, unmount cancellation');
}finally{await browser?.close();server.closeAllConnections();await new Promise(done=>server.close(done));}
