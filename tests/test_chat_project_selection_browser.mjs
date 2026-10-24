import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.VRCFORGE_PLAYWRIGHT_PATH||'playwright');
const bundle=await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import React from 'react';import {createRoot} from 'react-dom/client';
import i18n from 'i18next';import {initReactI18next} from 'react-i18next';
import {useChatSessions} from './src/hooks/use-chat-sessions';
await i18n.use(initReactI18next).init({lng:'en',resources:{en:{translation:{}}}});
const noop=()=>{};const paths=[];
const make=(id,projectPath,updatedAt,extra={})=>({id,sessionId:'s-'+id,title:id,projectPath,projectType:'general',revision:0,createdAt:'2026-01-01T00:00:00Z',updatedAt,items:[{id:'i-'+id,type:'user',text:'history'}],...extra});
const initial={activeChatId:'',chats:[
make('old','C:/project','2026-01-01T00:00:00Z'),
make('new','C:/project','2026-10-24T00:00:00Z'),
make('archived','C:/project','2026-12-01T00:00:00Z',{archived:true}),
make('pinned-old','C:/pinned','2026-01-01T00:00:00Z',{pinned:true}),
make('unpinned-new','C:/pinned','2026-10-24T00:00:00Z'),
make('other','C:/other','2026-12-01T00:00:00Z')]};
function Fixture(){const s=useChatSessions({endpoint:location.origin,activeView:'chat',runtimeConnected:false,projectPrefsReady:false,projectPaths:paths,customProjectPaths:paths,activeProjectPath:'',setActiveProjectPath:noop,setActiveView:noop,setError:noop,expandProjectGroup:noop,initialChatState:initial});window.choose=path=>s.selectProject(path,'general');return <pre id="state">{JSON.stringify({active:s.activeChatId,sidebar:[...s.chatSidebar.projectChatsByPath.values()].map(group=>group.map(c=>c.id))})}</pre>}
createRoot(document.getElementById('root')).render(<Fixture/>);
`},bundle:true,write:false,format:'esm',jsx:'automatic',define:{'process.env.NODE_ENV':'"test"'}});
const server=createServer((req,res)=>{if(req.url==='/fixture.js'){res.setHeader('Content-Type','text/javascript');res.end(bundle.outputFiles[0].text);}else res.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
try{
  browser=await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL||'msedge'});
  const page=await browser.newPage();const origin='http://127.0.0.1:'+server.address().port;
  await page.route('**/*',route=>route.request().url().startsWith(origin)?route.continue():route.abort());
  await page.goto(origin);await page.waitForFunction(()=>window.choose);
  async function choose(path){await page.evaluate(path=>window.choose(path),path);await page.waitForTimeout(50);return JSON.parse(await page.locator('#state').textContent());}
  let state=await choose('c:\\PROJECT');
  assert.equal(state.active,'new','project navigation must select its most recent visible order, not its first storage record');
  assert.deepEqual(state.sidebar[0],['new','old']);
  state=await choose('C:/pinned');
  assert.equal(state.active,'pinned-old','navigation must retain the existing pinned-first sidebar ordering');
  state=await choose('C:/missing');assert.equal(state.active,'','projects without conversations still open a new task');
  console.log('PASS project navigation: recency, normalized project scope, archived exclusion, pin order, empty project');
}finally{await browser?.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
