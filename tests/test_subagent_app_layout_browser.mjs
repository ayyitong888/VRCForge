import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,writeFile,mkdtemp,mkdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {build} from 'vite';
import {resolve,extname,sep} from 'node:path';
import {chromium} from 'playwright';
const root=resolve(import.meta.dirname,'..');
const scratch=process.env.VRCFORGE_FRONTEND_BUILD_DIR?null:await mkdtemp(resolve(tmpdir(),'vrcforge-child-app-'));
const frontend=resolve(process.env.VRCFORGE_FRONTEND_BUILD_DIR||resolve(scratch,'frontend'));
const evidence=process.env.VRCFORGE_SUBAGENT_EVIDENCE_DIR?resolve(process.env.VRCFORGE_SUBAGENT_EVIDENCE_DIR):null;
if(evidence)await mkdir(evidence,{recursive:true});
if(scratch)await build({root,logLevel:'error',build:{outDir:frontend,emptyOutDir:true}});
const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.woff2':'font/woff2','.woff':'font/woff','.svg':'image/svg+xml'};
const server=createServer(async(req,res)=>{
  const path=resolve(frontend,'.'+new URL(req.url,'http://localhost').pathname);
  if(path!==frontend&&!path.startsWith(frontend+sep)){res.writeHead(403);res.end();return}
  try{const file=path===frontend?resolve(frontend,'index.html'):path;res.writeHead(200,{'Content-Type':types[extname(file)]||'application/octet-stream'});res.end(await readFile(file))}catch{res.writeHead(404);res.end()}
});
let browser,page;const errors=[];
try{
 await new Promise(done=>server.listen(0,'127.0.0.1',done));
 const origin=`http://127.0.0.1:${server.address().port}`;
 browser=await chromium.launch({channel:'msedge',headless:true});
 page=await browser.newPage({viewport:{width:1600,height:1000},colorScheme:'light'});page.setDefaultTimeout(12000);
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{localStorage.setItem('vrcforge_theme','light');localStorage.setItem('i18nextLng','zh-CN')});
 await page.route('**/*',route=>{
  const u=new URL(route.request().url());
  if(u.origin===origin)return route.continue();
  if(u.pathname.startsWith('/api/')){
    const task={id:'subagent-context-smoke',parentChatId:'markdown-smoke-chat',parentSessionId:'markdown-smoke-session',childSessionId:'app-child',role:'llm_readonly',displayName:'UI conversation review',task:'Inspect the existing conversation.',status:'completed',summary:'Public report',canResume:true};
    let body={ok:true,health:{ok:true,errors:[],warnings:[],components:{}},settings:{},projects:[],chats:[],tasks:[],questions:[],runs:[],approvals:[],actions:[]};
    if(u.pathname==='/api/app/sub-agents')body={ok:true,schema:'vrcforge.sub_agent_tasks.v1',tasks:[task],count:1,roles:[{id:'llm_readonly',title:'Reviewer'}]};
    else if(u.pathname==='/api/app/sub-agents/subagent-context-smoke')body={ok:true,task};
    else if(u.pathname==='/api/app/agent/runs/timeline')body={ok:true,run:{sessionId:'app-child',clientTurnId:task.id},events:[{id:'reply',sequence:0,timestamp:'2026-10-28T10:00:00Z',kind:'assistant',payload:{summary:'Saved public child report.',status:'completed',phase:'final_answer'}}],nextSequence:1,hasMore:false};
    return route.fulfill({status:200,contentType:'application/json',headers:{'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'*'},body:JSON.stringify(body)});
  }
  return route.abort();
 });
 await page.goto(origin+'/?markdownSmoke=1&case=project-workbench');
 const parent=page.locator('[data-chat-composer-dock] textarea').first();
 await parent.waitFor();const initialDraft=await parent.inputValue();
 const sidebar=page.locator('[data-vrcforge-subagent-sidebar]');await sidebar.waitFor();
 await page.locator('[data-vrcforge-subagent-back]').click();
 await page.locator('[data-vrcforge-subagent-sidebar-list]').waitFor();
 const initialPane=await sidebar.boundingBox();
 const splitter=page.locator('[data-layout-splitter="right"]');const splitBox=await splitter.boundingBox();
 await page.mouse.move(splitBox.x+splitBox.width/2,splitBox.y+100);await page.mouse.down();
 await page.mouse.move(splitBox.x+splitBox.width/2+100,splitBox.y+100,{steps:5});await page.mouse.up();
 const box=await sidebar.boundingBox(),main=await parent.boundingBox();
 assert(box.width<initialPane.width-50,'right pane resize must change child pane width');
 assert(main.x+main.width<=box.x+2,'right pane must not overlap the main composer');
 assert(box.width>520,'child pane must allow a readable width beyond old workbench cap');
 assert.equal(await parent.inputValue(),initialDraft);
 if(evidence)await page.screenshot({path:resolve(evidence,'full-app-subagent-list-light.png'),fullPage:true});
 await sidebar.locator('[data-vrcforge-open-subagent-thread]').first().click();
 await sidebar.locator('[data-vrcforge-subagent-workspace-surface]').waitFor();
 assert.equal(await parent.inputValue(),initialDraft);
 if(evidence)await page.screenshot({path:resolve(evidence,'full-app-subagent-conversation-light.png'),fullPage:true});
 assert.deepEqual(errors,[]);
 if(evidence)await writeFile(resolve(evidence,'full-app-layout.json'),JSON.stringify({ok:true,parentComposer:main,rightPane:box,pageErrors:errors},null,2));
 console.log('PASS: production App keeps parent composer visible and draft intact beside wide, navigable child pane.');
}catch(error){console.error('Page errors:',JSON.stringify(errors));if(page&&evidence)await page.screenshot({path:resolve(evidence,'full-app-layout-failure.png'),fullPage:true}).catch(()=>{});throw error}
finally{await browser?.close();server.closeAllConnections();await new Promise(done=>server.close(done));if(scratch)await rm(scratch,{recursive:true,force:true})}
