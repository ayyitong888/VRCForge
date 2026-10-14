import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { build } from 'esbuild';

// Task-owned loopback fixture, no credentials or external traffic. Browser and
// server are closed in finally; this does not contact the installed app.
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || 'playwright');
const bundle = await build({ stdin: { resolveDir: process.cwd(), loader: 'tsx', contents: `
import React from 'react'; import {createRoot} from 'react-dom/client';
import i18n from 'i18next';import {initReactI18next} from 'react-i18next';
import {useChatSessions} from './src/hooks/use-chat-sessions';
await i18n.use(initReactI18next).init({lng:'en',resources:{en:{translation:{}}}});
const noop=()=>{};const paths=[];const initial={chats:[],activeChatId:''};
function Fixture(){const s=useChatSessions({endpoint:location.origin,activeView:'chat',runtimeConnected:true,
projectPrefsReady:true,projectPaths:paths,customProjectPaths:paths,activeProjectPath:'',setActiveProjectPath:noop,
setActiveView:noop,setError:noop,expandProjectGroup:noop,initialChatState:initial});
window.sessions=s; return <pre id="state">{JSON.stringify({chats:s.chats,blocked:s.chatPersistenceBlocked})}</pre>}
createRoot(document.getElementById('root')).render(<Fixture/>);
` }, bundle: true, write: false, format: 'esm', jsx: 'automatic', define: { 'process.env.NODE_ENV': '"test"' } });
let chats = [{id:'chat',sessionId:'session',title:'test',projectPath:'',projectType:'general',revision:0,
createdAt:'2026-01-01T00:00:00Z',updatedAt:'2026-01-01T00:00:00Z',items:[{id:'u1',type:'user',text:'test'}]}];
let gets=0;
const server = createServer(async(req,res)=>{
  if(req.url.startsWith('/api/app/chats')){
    res.setHeader('Content-Type','application/json');
    if(req.method==='POST'){let body='';for await(const chunk of req)body+=chunk;chats=JSON.parse(body).chats;
      res.end(JSON.stringify({ok:true,sourceRevisions:[]}));return;}
    gets++;res.end(JSON.stringify({ok:true,chats,count:chats.length,sources:[],recoveries:[],writeBlocked:false}));return;
  }
  if(req.url==='/fixture.js'){res.setHeader('Content-Type','text/javascript');res.end(bundle.outputFiles[0].text);return;}
  res.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
let browser;
try{
  browser=await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL||'msedge'});
  const page=await browser.newPage();const origin='http://127.0.0.1:'+server.address().port;
  await page.route('**/*',r=>r.request().url().startsWith(origin)?r.continue():r.abort());
  await page.goto(origin);await page.waitForFunction(()=>window.sessions?.chats.length===1);
  await page.evaluate(async()=>{
    window.sessions.updateChat('chat',c=>({...c,compaction:{generation:'live-compact',status:'compacting',trigger:'manual',phase:'standalone',startedAt:new Date().toISOString()}}));
    await window.sessions.persistChatsNow();
  });
  assert.equal(chats[0].compaction.status,'compacting');
  const before=gets;await page.evaluate(()=>window.sessions.notifyChatStorageChanged());
  await page.waitForTimeout(500);
  assert.ok(gets>before,'storage notification fetched its own persisted state');
  assert.equal(await page.evaluate(()=>window.sessions.chats[0].compaction.status),'compacting',
    'a live compaction must survive its own storage event without becoming interrupted');
  assert.equal(await page.evaluate(()=>window.sessions.chatPersistenceBlocked),false);
  await page.reload();await page.waitForFunction(()=>window.sessions?.chats.length===1);
  assert.equal(await page.evaluate(()=>window.sessions.chats[0].compaction.failureClass),'interrupted',
    'a new window still marks a genuinely abandoned compaction interrupted');
  console.log('PASS active compaction survives storage echo; restart marks abandoned work interrupted');
}finally{await browser?.close();server.closeAllConnections();await new Promise(done=>server.close(done));}
