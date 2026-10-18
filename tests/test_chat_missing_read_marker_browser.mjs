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
createdAt:'2026-01-01T00:00:00Z',updatedAt:'2026-01-01T00:00:00Z',items:[{id:'u1',type:'user',text:'hello'},{id:'a1',type:'agent',createdAt:'2026-01-01T00:00:01Z',response:{plan:{reply:'restored result'}}}]}];
const digests = () => Object.fromEntries(chats.map(c => [c.id, createHash('sha256').update(JSON.stringify(c)).digest('hex')]));
const shapeOf = value => value && typeof value==='object' && !Array.isArray(value)
  ? Object.fromEntries(Object.entries(value).filter(([key])=>key!=='items').map(([key,child])=>[key,shapeOf(child)])) : true;
const shapes = () => Object.fromEntries(chats.map(c=>[c.id,shapeOf(c)]));
const projection = () => {
  const copy=structuredClone(chats);
  for(const c of copy)for(const i of c.items)if(i.response?.task)i.response.task.objective='[redacted]';
  return copy;
};
let gets=0,posts=0,omitDigests=false,receivedPatches=[],patchErrors=[];
const server=createServer(async(req,res)=>{
  if(req.url.startsWith('/api/app/chats')){
    res.setHeader('Content-Type','application/json');
    if(req.method==='POST'){let body='';for await(const b of req)body+=b;const parsed=JSON.parse(body);if(parsed.chatPatches)receivedPatches.push(parsed.chatPatches);
      try {
        if(parsed.chatPatches) for(const c of chats) {
          const patch=parsed.chatPatches[c.id];
          if(patch) jsonpatch.applyPatch(structuredClone(c),patch.metadataPatch,true);
        }
      } catch(error) { patchErrors.push(error.message);res.statusCode=422;res.end(JSON.stringify({detail:'strict patch failed'}));return; }
      chats=parsed.chats.map(candidate=>{const raw=chats.find(c=>c.id===candidate.id);const patch=parsed.chatPatches?.[candidate.id];return raw&&patch?jsonpatch.applyPatch(structuredClone(raw),patch.metadataPatch,true).newDocument:candidate;});posts++;
      const ack={ok:true,sourceRevisions:[]};if(!omitDigests)ack.chatDigests=digests();ack.chatMetadataShapes=shapes();res.end(JSON.stringify(ack));return;}
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
  await page.evaluate(async()=>{
    window.sessions.openChat(window.sessions.chats[0]);
    await new Promise(resolve=>setTimeout(resolve,30));
    try { await window.sessions.persistChatsNow(); } catch {}
  });
  assert.deepEqual(patchErrors, [], 'opening restored chat must add an absent lastViewedAt, not replace it');
  assert.equal(receivedPatches.at(-1)?.chat.metadataPatch.find(op=>op.path==='/lastViewedAt')?.op,'add');
  assert.equal(chats[0].lastViewedAt,'2026-01-01T00:00:01Z');
  assert.equal(chats[0].items[1].response.plan.reply,'restored result');
  console.log('PASS restored chat missing read marker: strict patch preserves original history');
}finally{await browser?.close();server.closeAllConnections();await new Promise(done=>server.close(done));}
