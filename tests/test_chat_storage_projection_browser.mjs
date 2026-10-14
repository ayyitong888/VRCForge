import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { build } from 'esbuild';

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
createdAt:'2026-01-01T00:00:00Z',updatedAt:'2026-01-01T00:00:00Z',items:[{id:'u1',type:'user',text:'initial'}]}];
const digests = () => Object.fromEntries(chats.map(c => [c.id, createHash('sha256').update(JSON.stringify(c)).digest('hex')]));
const projection = () => {
  const copy=structuredClone(chats);
  for(const c of copy)for(const i of c.items)if(i.response?.task)i.response.task.objective='[redacted]';
  return copy;
};
let gets=0,posts=0,omitDigests=false,receivedPatches=[];
const server=createServer(async(req,res)=>{
  if(req.url.startsWith('/api/app/chats')){
    res.setHeader('Content-Type','application/json');
    if(req.method==='POST'){let body='';for await(const b of req)body+=b;const parsed=JSON.parse(body);if(parsed.chatPatches)receivedPatches.push(parsed.chatPatches);chats=parsed.chats;posts++;
      const ack={ok:true,sourceRevisions:[]};if(!omitDigests)ack.chatDigests=digests();res.end(JSON.stringify(ack));return;}
    gets++;const snapshot={ok:true,chats:projection(),count:chats.length,sources:[],recoveries:[],writeBlocked:false};
    if(!omitDigests)snapshot.chatDigests=digests();res.end(JSON.stringify(snapshot));return;
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
  await page.evaluate(async()=>{
    window.sessions.updateChat('chat',c=>({...c,items:[...c.items,{id:'a1',type:'agent',response:{plan:{reply:'remembered'},task:{objective:'the public field api_key'}}}]}));
    await window.sessions.persistChatsNow();
    window.sessions.updateChat('chat',c=>({...c,items:[...c.items,{id:'u2',type:'user',text:'read-only tool request'}]}));
    window.sessions.notifyChatStorageChanged();
  });
  const before=gets;await page.waitForTimeout(400);assert.ok(gets>before);
  assert.equal(await page.evaluate(()=>window.sessions.chatPersistenceBlocked),false,
    'a changed display projection of the acknowledged version must not look like a remote edit');
  assert.equal(await page.evaluate(()=>window.sessions.chats[0].items[1].response.task.objective),'the public field api_key');
  await page.evaluate(async()=>{
    window.sessions.updateChat('chat',c=>({...c,items:[...c.items,{id:'a2',type:'agent',response:{plan:{reply:'tool result'}}}]}));
    await window.sessions.persistChatsNow();
  });
  assert.ok(receivedPatches.at(-1)?.chat,
    'an acknowledged existing chat must be saved through a raw-preserving patch');
  assert.deepEqual(receivedPatches.at(-1).chat.itemOrder, ['u1','a1','u2','a2']);
  assert.equal(chats[0].items.length,4,'reply after the storage notification remains saveable');
  const savedPosts=posts;await page.evaluate(()=>window.sessions.notifyChatStorageChanged());await page.waitForTimeout(900);
  assert.equal(posts,savedPosts,'unchanged projected echo must not start an autosave loop');
  // Same visible projection, but a truly different stored value must conflict.
  chats=structuredClone(chats);chats[0].items[1].response.task.objective='different external private value';
  await page.evaluate(()=>{
    window.sessions.updateChat('chat',c=>({...c,title:'local edit'}));
    window.sessions.notifyChatStorageChanged();
  });
  await page.waitForFunction(()=>window.sessions.chatPersistenceBlocked);
  assert.equal(await page.evaluate(()=>window.sessions.chats[0].title),'local edit');
  assert.equal(chats[0].items[1].response.task.objective,'different external private value');
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
  console.log('PASS versioned storage projection: own echo, completed reply save, no loop, hidden remote conflict');
}finally{await browser?.close();server.closeAllConnections();await new Promise(done=>server.close(done));}
