import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import {build} from 'esbuild';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.VRCFORGE_PLAYWRIGHT_PATH||'playwright');
const {applyPatch}=require('fast-json-patch');
const bundle=await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import React from 'react';import {createRoot} from 'react-dom/client';
import i18n from 'i18next';import {initReactI18next} from 'react-i18next';
import {useChatSessions} from './src/hooks/use-chat-sessions';
await i18n.use(initReactI18next).init({lng:'en',resources:{en:{translation:{}}}});
const noop=()=>{};const paths=[];const initial={chats:[],activeChatId:''};
function Fixture(){const [error,setError]=React.useState('');
const sessions=useChatSessions({endpoint:location.origin,activeView:'chat',runtimeConnected:true,
projectPrefsReady:true,projectPaths:paths,customProjectPaths:paths,activeProjectPath:'',setActiveProjectPath:noop,
setActiveView:noop,setError,expandProjectGroup:noop,initialChatState:initial});
window.notify=sessions.notifyChatStorageChanged;
window.append=()=>sessions.appendToChat('old',{id:'local-extra',type:'user',text:'local-added'});
return <pre id="state">{JSON.stringify({chats:sessions.chats,blocked:sessions.chatPersistenceBlocked,error})}</pre>}
createRoot(document.getElementById('root')).render(<Fixture/>);
`},bundle:true,write:false,format:'esm',jsx:'automatic',define:{'process.env.NODE_ENV':'"test"'}});
function chat(id){return {id,sessionId:'session-'+id,title:id,revision:0,projectPath:'',projectType:'general',createdAt:'2026-01-01T00:00:00Z',updatedAt:'2026-01-01T00:00:00Z',items:[{id:'item-'+id,type:'user',text:'initial-'+id,createdAt:'2026-01-01T00:00:00Z'}]};}
let chats=[chat('old'),chat('edited')], invalid=false;
const reads=[],writes=[];
const digest=chat=>createHash('sha256').update(JSON.stringify(chat)).digest('hex');
const shape=value=>value&&typeof value==='object'&&!Array.isArray(value)?Object.fromEntries(Object.entries(value).map(([k,v])=>[k,shape(v)])):true;
function metadata(){return {chatDigests:Object.fromEntries(chats.map(c=>[c.id,digest(c)])),chatMetadataShapes:Object.fromEntries(chats.map(c=>{const {items,...rest}=c;return [c.id,shape(rest)];}))};}
const server=createServer(async(req,res)=>{
  if(req.url.startsWith('/api/app/chats')){
    res.setHeader('Content-Type','application/json');
    if(req.method==='POST'){
      let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);writes.push(body);
      chats=body.chats.map(c=>{
        const patch=body.chatPatches?.[c.id];if(!patch)return c;
        const before=chats.find(v=>v.id===c.id);assert.equal(patch.baseDigest,digest(before));
        const after=applyPatch(structuredClone(before),patch.metadataPatch).newDocument;
        after.items=patch.itemOrder.map(id=>patch.newItems[id]??applyPatch(structuredClone(before.items.find(v=>v.id===id)),patch.itemPatches[id]||[]).newDocument);
        return after;
      });
      res.end(JSON.stringify({ok:true,sourceRevisions:[],...metadata()}));return;
    }
    const url=new URL(req.url,'http://fixture');const knownRaw=url.searchParams.get('knownChatDigests');
    const known=knownRaw===null?undefined:JSON.parse(knownRaw);const versions=metadata();
    const changed=known?chats.filter(c=>known[c.id]!==versions.chatDigests[c.id]):chats;
    reads.push({known,ids:changed.map(c=>c.id)});
    res.end(JSON.stringify({ok:true,chats:changed,count:chats.length,sources:[],recoveries:[],writeBlocked:false,...versions,
      ...(known?{incremental:true,chatManifest:{schema:'vrcforge.chat_manifest.v1',complete:!invalid,ids:chats.map(c=>c.id),scopeDigest:createHash('sha256').update('').digest('hex')}}:{})}));return;
  }
  if(req.url==='/fixture.js'){res.setHeader('Content-Type','text/javascript');res.end(bundle.outputFiles[0].text);return;}
  res.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
});
await new Promise(done=>server.listen(0,'127.0.0.1',done));let browser;
try{
  browser=await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL||'msedge'});
  const page=await browser.newPage();const origin='http://127.0.0.1:'+server.address().port;
  await page.route('**/*',route=>route.request().url().startsWith(origin)?route.continue():route.abort());
  await page.goto(origin);
  await page.waitForFunction(()=>JSON.parse(document.querySelector('#state')?.textContent||'{}').chats?.length===2);
  assert.equal(reads[0].known,undefined);
  await page.evaluate(()=>window.append());
  chats=chats.map(c=>c.id==='edited'?{...c,items:[...c.items,{id:'remote-extra',type:'user',text:'remote-added'}]}:c);
  await page.evaluate(()=>window.notify());
  await page.waitForFunction(()=>{
    const s=JSON.parse(document.querySelector('#state').textContent);
    return s.chats.find(c=>c.id==='edited')?.items.some(i=>i.id==='remote-extra')&&s.chats.find(c=>c.id==='old')?.items.some(i=>i.id==='local-extra');
  });
  assert.deepEqual(reads[1].ids,['edited'],'unchanged full history must never cross reload transport');
  assert.ok(reads[1].known.old);
  await page.waitForFunction(()=>JSON.parse(document.querySelector('#state').textContent).blocked===false);
  const state=await page.locator('#state').textContent();
  invalid=true;await page.evaluate(()=>window.notify());
  await page.waitForFunction(()=>JSON.parse(document.querySelector('#state').textContent).error.includes('Incomplete incremental'));
  const before=JSON.parse(state),after=JSON.parse(await page.locator('#state').textContent());
  assert.deepEqual(after.chats,before.chats,'invalid manifest must retain both cached and dirty local history');
  assert.equal(after.chats.length,2);
  console.log('PASS real hook incremental refresh: unchanged cached chat, dirty append merge, invalid manifest retains state');
}finally{await browser?.close();server.closeAllConnections();await new Promise(done=>server.close(done));}
