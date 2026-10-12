import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { build } from "esbuild";
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.VRCFORGE_PLAYWRIGHT_PATH||"playwright");
const app=readFileSync("src/App.tsx","utf8");
assert.match(app,/if \(eventType === "chatStorageChanged"\) \{\s*notifyChatStorageChanged\(\);\s*\}/);
const bundle=await build({stdin:{resolveDir:process.cwd(),loader:"tsx",contents:`
import React from 'react';import {createRoot} from 'react-dom/client';
import i18n from 'i18next';import {initReactI18next} from 'react-i18next';
import {useChatSessions} from './src/hooks/use-chat-sessions';
await i18n.use(initReactI18next).init({lng:'en',resources:{en:{translation:{}}}});
const noop=()=>{};const paths=[];const initial={chats:[],activeChatId:''};
function Fixture(){const sessions=useChatSessions({endpoint:location.origin,activeView:'chat',runtimeConnected:true,
projectPrefsReady:true,projectPaths:paths,customProjectPaths:paths,activeProjectPath:'',setActiveProjectPath:noop,
setActiveView:noop,setError:noop,expandProjectGroup:noop,initialChatState:initial});
window.notify=sessions.notifyChatStorageChanged;
window.localEdit=()=>sessions.updateChat('chat',chat=>({...chat,title:'local-dirty'}));
return <pre id="state">{JSON.stringify({chats:sessions.chats,blocked:sessions.chatPersistenceBlocked})}</pre>}
const root=createRoot(document.getElementById('root'));root.render(<Fixture/>);window.unmount=()=>root.unmount();
`},bundle:true,write:false,format:"esm",jsx:"automatic",define:{"process.env.NODE_ENV":'"test"'}});
let gets=0,posts=0;
const chat={id:"chat",sessionId:"session",title:"initial",revision:0,projectPath:"",projectType:"general",
createdAt:"2026-01-01T00:00:00Z",updatedAt:"2026-01-01T00:00:00Z",items:[{id:"u1",type:"user",text:"initial",createdAt:"2026-01-01T00:00:00Z"}]};
let chats=[chat];
const server=createServer((req,res)=>{
if(req.url.startsWith('/api/app/chats')){res.setHeader('Content-Type','application/json');
if(req.method==='POST'){posts++;res.end(JSON.stringify({ok:true,sourceRevisions:[]}));return;}
gets++;res.end(JSON.stringify({ok:true,chats,count:chats.length,sources:[],recoveries:[],writeBlocked:false}));return;}
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
chats=[{...chat,items:[...chat.items,{id:'u2',type:'user',text:'telegram-visible',createdAt:'2026-01-01T00:00:01Z'}]}];
await page.evaluate(()=>{window.notify();window.notify();window.notify();});
await page.waitForFunction(()=>document.querySelector('#state')?.textContent.includes('telegram-visible'));
await page.waitForTimeout(200);assert.equal(gets,before+1,'burst should make one serialized reload');
await page.evaluate(()=>window.localEdit());
chats=[{...chats[0],title:'remote-dirty'}];
await page.evaluate(()=>window.notify());
await page.waitForFunction(()=>JSON.parse(document.querySelector('#state').textContent).blocked===true);
assert.equal(await page.evaluate(()=>JSON.parse(document.querySelector('#state').textContent).chats[0].title),'local-dirty');
const beforeUnmount=gets;
await page.evaluate(()=>{window.notify();window.unmount();window.notify();});
await page.waitForTimeout(250);assert.equal(gets,beforeUnmount,'unmount must cancel pending reload');
console.log('PASS chatStorageChanged: real hook reload, burst debounce, dirty conflict protection, unmount cancellation');
}finally{await browser?.close();server.closeAllConnections();await new Promise(done=>server.close(done));}
