import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { build } from 'esbuild';
import jsonpatch from 'fast-json-patch';

// Test-owned loopback server/browser, no authentication or external traffic.
// Resources are released in finally. No installed profile or model requests.
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || 'playwright');
const bundle = await build({ stdin: { resolveDir: process.cwd(), loader: 'tsx', contents: `
import React from 'react';import {createRoot} from 'react-dom/client';
import i18n from 'i18next';import {initReactI18next} from 'react-i18next';
import {useChatSessions} from './src/hooks/use-chat-sessions';
await i18n.use(initReactI18next).init({lng:'en',resources:{en:{translation:{}}}});
const noop=()=>{};const paths=[];const initial={chats:[],activeChatId:''};
function Fixture(){const s=useChatSessions({endpoint:location.origin,activeView:'chat',runtimeConnected:true,
projectPrefsReady:true,projectPaths:paths,customProjectPaths:paths,activeProjectPath:'',setActiveProjectPath:noop,
setActiveView:noop,setError:noop,expandProjectGroup:noop,initialChatState:initial});
window.sessions=s;return <pre>{JSON.stringify({chats:s.chats,blocked:s.chatPersistenceBlocked})}</pre>}
createRoot(document.getElementById('root')).render(<Fixture/>);
` }, bundle: true, write: false, format: 'esm', jsx: 'automatic', define: { 'process.env.NODE_ENV': '"test"' } });
let chats = [{id:'chat',sessionId:'session',title:'initial',projectPath:'',projectType:'general',revision:0,
createdAt:'2026-01-01T00:00:00Z',updatedAt:'2026-01-01T00:00:00Z',opaqueExtension:{keep:'raw'},items:[{id:'u1',type:'user',text:'initial'},
{id:'a0',type:'agent',response:{plan:{reply:'restored'},contextUsage:{exact:true,inputTokens:5,totalTokens:10}}}]}];
const digests = () => Object.fromEntries(chats.map(c => [c.id, createHash('sha256').update(JSON.stringify(c)).digest('hex')]));
const shapeOf = value => value && typeof value==='object' && !Array.isArray(value)
  ? Object.fromEntries(Object.entries(value).filter(([key])=>key!=='items').map(([key,child])=>[key,shapeOf(child)])) : true;
const shapes = () => Object.fromEntries(chats.map(c=>[c.id,shapeOf(c)]));
const projection = () => {
  const copy=structuredClone(chats);
  for(const c of copy)for(const i of c.items)if(i.response?.task)i.response.task.objective='[redacted]';
  return copy;
};
let gets=0,posts=0,omitDigests=false,omitAckShapes=false,receivedPatches=[];
const server=createServer(async(req,res)=>{
  if(req.url.startsWith('/api/app/chats')){
    res.setHeader('Content-Type','application/json');
    if(req.method==='POST'){let body='';for await(const b of req)body+=b;const parsed=JSON.parse(body);if(parsed.chatPatches)receivedPatches.push(parsed.chatPatches);
      chats=parsed.chats.map(candidate=>{const raw=chats.find(c=>c.id===candidate.id);const patch=parsed.chatPatches?.[candidate.id];if(!raw||!patch)return candidate;
        const merged=jsonpatch.applyPatch(structuredClone(raw),patch.metadataPatch,true).newDocument;const byId=new Map(raw.items.map(item=>[item.id,item]));
        for(const [id,ops] of Object.entries(patch.itemPatches||{}))byId.set(id,jsonpatch.applyPatch(structuredClone(byId.get(id)),ops,true).newDocument);
        for(const [id,item] of Object.entries(patch.newItems||{}))byId.set(id,item);merged.items=patch.itemOrder.map(id=>byId.get(id));return merged;});posts++;
      const ack={ok:true,sourceRevisions:[]};if(!omitDigests)ack.chatDigests=digests();if(!omitAckShapes)ack.chatMetadataShapes=shapes();res.end(JSON.stringify(ack));return;}
    gets++;const snapshot={ok:true,chats:projection(),count:chats.length,sources:[],recoveries:[],writeBlocked:false};
    if(!omitDigests)snapshot.chatDigests=digests();snapshot.chatMetadataShapes=shapes();res.end(JSON.stringify(snapshot));return;
  }
  if(req.url==='/fixture.js'){res.setHeader('Content-Type','text/javascript');res.end(bundle.outputFiles[0].text);return;}
  res.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
});
await new Promise(done=>server.listen(0,'127.0.0.1',done));let browser;
try{
  browser=await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL||'msedge'});
  const page=await browser.newPage();const origin='http://127.0.0.1:'+server.address().port;
  await page.route('**/*',r=>r.request().url().startsWith(origin)?r.continue():r.abort());
  await page.goto(origin);await page.waitForFunction(()=>window.sessions?.chats.length===1);
  assert.deepEqual(chats[0].opaqueExtension,{keep:'raw'},'unknown raw metadata survives scoped deltas');
  await page.evaluate(async()=>{
    window.sessions.updateChat('chat',c=>({...c,items:[...c.items,{id:'a1',type:'agent',response:{plan:{reply:'remembered'},task:{objective:'the public field api_key'}}}]}));
    await window.sessions.persistChatsNow();
    window.sessions.updateChat('chat',c=>({...c,items:[...c.items,{id:'u2',type:'user',text:'read-only tool request'}]}));
    window.sessions.notifyChatStorageChanged();
  });
  assert.ok(receivedPatches.at(-1)?.chat?.metadataPatch.some(op=>op.op==='add'&&op.path==='/contextUsageCache'),
    'cold restore synthesizes a cache and first save must add its absent raw parent');
  const before=gets;await page.waitForTimeout(400);assert.ok(gets>before);
  assert.equal(await page.evaluate(()=>window.sessions.chatPersistenceBlocked),false,
    'a changed display projection of the acknowledged version must not look like a remote edit');
  assert.equal(await page.evaluate(()=>window.sessions.chats[0].items[2].response.task.objective),'the public field api_key');
  await page.evaluate(async()=>{
    window.sessions.updateChat('chat',c=>({...c,items:[...c.items.map(i=>i.id==='a0'?{...i,response:{...i.response,contextUsage:{exact:true,inputTokens:99,totalTokens:99}}}:i),{id:'a2',type:'agent',response:{plan:{reply:'tool result'}}}]}));
    await window.sessions.persistChatsNow();
  });
  assert.ok(receivedPatches.at(-1)?.chat,
    'an acknowledged existing chat must be saved through a raw-preserving patch');
  assert.deepEqual(receivedPatches.at(-1).chat.itemOrder, ['u1','a0','a1','u2','a2']);
  assert.ok(receivedPatches.at(-1).chat.metadataPatch.some(op=>op.path.startsWith('/contextUsageCache')),
    'second cache mutation after ACK is diffed against acknowledged persisted shape');
  assert.equal(chats[0].items.length,5,'reply after the storage notification remains saveable');
  const savedPosts=posts;await page.evaluate(()=>window.sessions.notifyChatStorageChanged());await page.waitForTimeout(900);
  assert.equal(posts,savedPosts,'unchanged projected echo must not start an autosave loop');
  // Same visible projection, but a truly different stored value must conflict.
  chats=structuredClone(chats);chats[0].items[2].response.task.objective='different external private value';
  await page.evaluate(()=>{
    window.sessions.updateChat('chat',c=>({...c,title:'local edit'}));
    window.sessions.notifyChatStorageChanged();
  });
  await page.waitForFunction(()=>window.sessions.chatPersistenceBlocked);
  assert.equal(await page.evaluate(()=>window.sessions.chats[0].title),'local edit');
  assert.equal(chats[0].items[2].response.task.objective,'different external private value');
  const page2=await browser.newPage();
  await page2.goto(origin);await page2.waitForFunction(()=>window.sessions?.chats.length===1);
  omitDigests=true;
  await page2.evaluate(async()=>{
    window.sessions.updateChat('chat',c=>({...c,title:'missing-ack-local'}));
    try{await window.sessions.persistChatsNow();}catch{}
  });
  omitDigests=false;
  await page2.evaluate(()=>window.sessions.notifyChatStorageChanged());
  await page2.waitForFunction(()=>window.sessions.chatPersistenceBlocked);
  assert.equal(await page2.evaluate(()=>window.sessions.chats[0].title),'missing-ack-local',
    'a missing digest ACK leaves the local candidate visible for conflict handling');
  await page2.close();
  const page3=await browser.newPage();await page3.goto(origin);await page3.waitForFunction(()=>window.sessions?.chats.length===1);
  omitAckShapes=true;
  const missingShapeAck=await page3.evaluate(async()=>{window.sessions.updateChat('chat',c=>({...c,title:'missing-shape-ack'}));try{await window.sessions.persistChatsNow();return ''}catch(e){return String(e?.message||e)}});
  omitAckShapes=false;
  assert.match(missingShapeAck,/omitted chat metadata shape/,'a digest ACK without persisted shape must not be accepted');
  await page3.close();
  console.log('PASS versioned storage projection: own echo, completed reply save, no loop, hidden remote conflict');
}finally{await browser?.close();server.closeAllConnections();await new Promise(done=>server.close(done));}
