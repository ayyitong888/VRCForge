import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { build } from "esbuild";
import jsonPatch from "fast-json-patch";
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
window.sessions=sessions;
window.setTitle=title=>sessions.updateChat('chat',chat=>({...chat,title}));
window.localEdit=()=>sessions.updateChat('chat',chat=>({...chat,title:'local-dirty'}));
window.localAppend=()=>sessions.appendToChat('chat',{id:'local-extra',type:'user',text:'local-added'});
window.openChat=()=>sessions.openChat(sessions.sidebarChats.find(chat=>chat.id==='chat'));
window.startStreaming=()=>sessions.appendToChat('chat',{id:'active-stream',type:'streaming',clientTurnId:'active-turn',text:'in-progress response'});
return <pre id="state">{JSON.stringify({ready:sessions.chatStorageReady,chats:sessions.chats,display:sessions.displayChat,blocked:sessions.chatPersistenceBlocked})}</pre>}
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
let gets=0,posts=0,postOriginIds=[],postBodies=[];
const chat={id:"chat",sessionId:"session",title:"initial",revision:0,projectPath:"C:/fixture/project",projectType:"unity",
createdAt:"2026-01-01T00:00:00Z",updatedAt:"2026-01-01T00:00:00Z",items:[{id:"u1",type:"user",text:"initial",createdAt:"2026-01-01T00:00:00Z"}]};
let chats=[chat];
let sourceProjectPath='';
const saveReceipts=new Map();
const digest=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const sources=()=>[
  {storeId:'chat.app',scope:'app',exists:true,digest:digest(sourceProjectPath?[]:chats),status:'ok'},
  {storeId:'chat.project',scope:'project',projectPath:chat.projectPath,exists:true,digest:digest(chats),status:'ok'},
];
const source=()=>sources()[sourceProjectPath?1:0];
const shape=value=>value&&typeof value==='object'&&!Array.isArray(value)
  ?Object.fromEntries(Object.entries(value).map(([key,child])=>[key,shape(child)])):true;
const metadataShape=chat=>{const {items,...metadata}=chat;return shape(metadata);};
function applyChatPatch(previous,patch){
  const metadata=jsonPatch.applyPatch(previous,patch.metadataPatch,true,false).newDocument;
  const items=new Map(previous.items.map(item=>[item.id,item]));
  return {...metadata,items:patch.itemOrder.map(id=>patch.newItems[id]??jsonPatch.applyPatch(items.get(id),patch.itemPatches[id]??[],true,false).newDocument)};
}
let nextGetHold=null;
function holdNextGet(){
let release;let markStarted;const gate=new Promise(resolve=>{release=resolve;});
const started=new Promise(resolve=>{markStarted=resolve;});nextGetHold={gate,markStarted};return {started,release};}
const server=createServer(async(req,res)=>{
const url=new URL(req.url,'http://localhost');
if(url.pathname==='/api/app/chats/single'&&req.method==='POST'){
res.setHeader('Content-Type','application/json');posts++;let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);postOriginIds.push(body.chatOriginId);postBodies.push(body);
assert.ok(body.chatSaveId);const saveKey=body.chatOriginId+':'+body.chatSaveId;
if(saveReceipts.has(saveKey)){res.end(JSON.stringify(saveReceipts.get(saveKey)));return;}
assert.equal(body.chatId,chat.id);assert.deepEqual(body.sourceRevisions,sources());
if(body.projectPath!==sourceProjectPath){res.statusCode=404;res.end(JSON.stringify({detail:'Chat no longer exists in the former source'}));return;}
const previous=chats.find(item=>item.id===body.chatId);
if(body.chatPatch)assert.equal(body.chatPatch.baseDigest,digest(previous));
const updated=body.chat??applyChatPatch(previous,body.chatPatch);chats=chats.map(item=>item.id===updated.id?updated:item);
sourceProjectPath=updated.projectPath;
const receipt={ok:true,sourceRevisions:sources(),chatDigests:{[updated.id]:digest(updated)},chatMetadataShapes:{[updated.id]:metadataShape(updated)}};
saveReceipts.set(saveKey,receipt);res.end(JSON.stringify(receipt));return;}
if(url.pathname==='/api/app/chats'&&req.method==='GET'){res.setHeader('Content-Type','application/json');gets++;
if(url.searchParams.get('catalog')==='1'){res.end(JSON.stringify({ok:true,entries:chats.map(({items,...metadata})=>({...metadata,sourceProjectPath,sourceStoreId:source().storeId})),sources:sources(),recoveries:[],writeBlocked:false,pendingChatIds:[]}));return;}
assert.equal(url.searchParams.get('chatId'),chat.id);
const snapshot=structuredClone(chats);const revision=source();const held=nextGetHold;nextGetHold=null;if(held){held.markStarted();await held.gate;}
res.end(JSON.stringify({ok:true,chats:snapshot,sourceRevision:revision,hasOlder:false,nextBefore:null,chatDigests:Object.fromEntries(snapshot.map(item=>[item.id,digest(item)])),chatMetadataShapes:Object.fromEntries(snapshot.map(item=>[item.id,metadataShape(item)]))}));return;}
if(req.url==='/fixture.js'){res.setHeader('Content-Type','text/javascript');res.end(bundle.outputFiles[0].text);return;}
res.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
let browser;
function appendRelay(id) {
  const previous = chats[0];
  chats = [{...previous, revision: previous.revision + 1, updatedAt: new Date().toISOString(),
    items: [...previous.items, {
      id, type: 'user', text: id, deliveryMode: 'queue_only', forwardedByAgent: true,
      forwardedFrom: {sourceChatId: 'agent-source', sourceClientTurnId: 'source-turn', sourceActionId: id},
    }],
  }];
}
try{
browser=await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL||'msedge'});
const page=await browser.newPage();const origin='http://127.0.0.1:'+server.address().port;
await page.route('**/*',route=>route.request().url().startsWith(origin)?route.continue():route.abort());
await page.goto(origin);await page.waitForFunction(()=>window.sessions?.chatStorageReady);
await page.evaluate(()=>window.openChat());
await page.waitForFunction(()=>window.sessions.displayChat?.items.some(item=>item.id==='u1'));
assert.equal(await page.evaluate(()=>window.sessions.activeChat),null,'browsing selected chat uses paged history');
appendRelay('visible-while-browsing');
await page.evaluate(()=>window.notify('cross-session-agent'));
await page.waitForFunction(()=>window.sessions.displayChat?.items.some(item=>item.id==='visible-while-browsing'),
  undefined,{timeout:3000});
assert.equal(await page.evaluate(()=>window.sessions.activeChat),null,'live relay keeps browsing history paged');
await page.evaluate(()=>window.sessions.setActiveChatId(''));
await page.evaluate(()=>window.sessions.loadChat('chat'));
await page.waitForFunction(()=>document.querySelector('#state')?.textContent.includes('initial'));
const before=gets;
const originId=await page.evaluate(()=>window.originId);
await page.evaluate((id)=>{window.notify(id);window.notify(id);window.notify(id);},originId);
await page.waitForTimeout(220);assert.equal(gets,before,'the originating webview ignores its own precise storage echo');
const beforeOwnWrite=posts;
await page.evaluate(()=>window.setTitle('own-save'));
await page.evaluate(()=>window.persist());
assert.equal(posts,beforeOwnWrite+1,'explicit persistence should complete without waiting behind a self-triggered reload');
assert.equal(postOriginIds.at(-1),originId,'the write carries the exact per-webview origin identity');
await page.evaluate((id)=>{window.notify(id);window.notify(id);window.notify(id);},originId);
await page.waitForTimeout(220);assert.equal(gets,before,'self write echoes do not queue a full reload');
assert.equal(postBodies.at(-1).projectPath,'','the first save uses the catalog source of the equivalent app/project copies');
await page.evaluate(()=>window.setTitle('after-source-normalization'));
await page.evaluate(()=>window.persist());
assert.equal(postBodies.at(-1).projectPath,chat.projectPath,'the second save uses the normalized project source even though metadata.projectPath never changed');
const beforeLostAck=posts;
await page.evaluate(()=>window.setTitle('lost-ack-save'));
const discardSaveResponse=async(route)=>{
  if(route.request().method()!=='POST')return route.continue();
  await route.fetch();
  await route.abort();
};
await page.route('**/api/app/chats/single',discardSaveResponse,{times:2});
await page.evaluate(()=>window.persist().then(()=>{throw new Error('expected transport error');},()=>{}));
await page.evaluate(()=>window.persist());
assert.equal(posts,beforeLostAck+3,'one retry per save attempt, then the next attempt recovers the pending receipt');
const lostAckBodies=postBodies.slice(beforeLostAck);
assert.ok(lostAckBodies[0].chatSaveId,'chat save carries a retry identifier');
assert.notEqual(lostAckBodies[0].chatSaveId,postBodies[beforeLostAck-1].chatSaveId,'distinct operations use distinct identifiers');
assert.deepEqual(lostAckBodies,[lostAckBodies[0],lostAckBodies[0],lostAckBodies[0]],'transport and later retries replay the exact operation');
chats=[{...chats[0],items:[...chats[0].items,{id:'u2',type:'user',text:'telegram-visible',createdAt:'2026-01-01T00:00:01Z'}]}];
const heldReload=holdNextGet();
await page.evaluate(()=>{window.notify();window.notify();window.notify();});
await heldReload.started;
chats=[{...chats[0],items:[...chats[0].items,{id:'u3',type:'user',text:'telegram-visible-latest',createdAt:'2026-01-01T00:00:02Z'}]}];
for(let index=0;index<3;index++){await page.evaluate(()=>window.notify());await page.waitForTimeout(220);}
assert.equal(gets,before+2,'an active catalog plus selected-record reload must not start concurrent reads');
heldReload.release();
await page.waitForFunction(()=>document.querySelector('#state')?.textContent.includes('telegram-visible-latest'));
await page.waitForTimeout(250);assert.equal(gets,before+4,'events during an active reload collapse into one trailing catalog plus selected-record refresh');
await page.evaluate(()=>window.localAppend());
chats=[{...chats[0],items:[...chats[0].items,{id:'remote-extra',type:'user',text:'remote-added'}]}];
await page.evaluate(()=>window.notify());
await page.waitForFunction(()=>{
const state=JSON.parse(document.querySelector('#state').textContent);
return state.chats[0].items.some(item=>item.id==='local-extra')&&state.chats[0].items.some(item=>item.id==='remote-extra');
});
assert.equal(await page.evaluate(()=>JSON.parse(document.querySelector('#state').textContent).blocked),false,
  'independent appended messages reconcile without pausing storage or discarding local content');
appendRelay('retained-while-offscreen');
await page.evaluate(()=>window.notify('cross-session-agent'));
await page.evaluate(()=>window.openChat());
await page.waitForFunction(()=>window.sessions.displayChat?.items.some(item=>item.id==='retained-while-offscreen'));
await page.evaluate(()=>window.startStreaming());
await page.evaluate(()=>window.persist());
appendRelay('visible-while-streaming');
await page.evaluate(()=>window.notify('cross-session-agent'));
await page.waitForFunction(()=>window.sessions.displayChat?.items.some(item=>item.id==='visible-while-streaming'));
assert.ok(await page.evaluate(()=>window.sessions.displayChat.items.some(item=>item.id==='active-stream')),
  'incoming relay preserves the live response');
await page.evaluate(()=>window.setTitle('during-live-response'));
assert.ok(await page.evaluate(()=>window.sessions.displayChat.items.some(item=>item.id==='active-stream')),
  'subsequent local mutations preserve the live response');
await page.evaluate(()=>window.persist());
assert.ok(chats[0].items.some(item=>item.id==='visible-while-streaming' && item.forwardedFrom.sourceChatId==='agent-source'),
  'local persistence preserves the incoming relay attribution');
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
