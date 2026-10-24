import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { build } from 'esbuild';

const replay = process.env.VRCFORGE_ATTENTION_REPLAY ? JSON.parse(await readFile(process.env.VRCFORGE_ATTENTION_REPLAY,'utf8')) : null;
const run = replay?.run || {sessionId:'session',clientTurnId:'turn',turnId:'backend',status:'blocked',startedAt:'2026-10-24T08:10:03Z'};
const reply = replay?.events.find(e => e.kind==='assistant' && e.payload?.phase==='final_answer')?.payload.summary || 'The visual audit did not pass its required checks: visualVerified.';
const terminal = process.env.VRCFORGE_ATTENTION_RECEIPT ? JSON.parse(await readFile(process.env.VRCFORGE_ATTENTION_RECEIPT,'utf8')) : {ok:true,status:'completed',response:{...run,plan:{nextStep:'needs_user_action',reply,completionGate:{status:'needs_user_action',reason:'verification_required'}}}};
const baseline = process.env.VRCFORGE_ATTENTION_BASELINE === '1';
const bundle = await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import React from 'react';
import {createRoot} from 'react-dom/client';
import {useRuntimeWorkspace} from './src/hooks/use-runtime-workspace';
window.snapshotRuns=[${JSON.stringify(run)}];window.receiptReads=0;window.toasts=[];
window.__TAURI_INTERNALS__={invoke:async(command,args)=>{window.toasts.push({command,args});}};
window.receipt=${JSON.stringify(terminal)};
const noop=()=>{};
function Harness(){const result=useRuntimeWorkspace({endpoint:'http://fixture',runtimeConnected:true,sessionId:${JSON.stringify(run.sessionId)},activeRuntimeProjectPath:'',activeProjectPath:'',sending:false,pendingApprovals:0,setBootstrap:noop,setAgentApprovals:noop,setError:noop});window.refresh=()=>result.refreshRuntimeRuns(false);window.attention=result.runtimeAttention;return <div id="count">{result.runtimeAttention?.length||0}</div>}
window.root=createRoot(document.getElementById('root'));window.root.render(<Harness/>);
`},bundle:true,write:false,platform:'browser',format:'esm',jsx:'automatic',plugins:[{name:'fixture',setup(b){
  if(baseline)b.onLoad({filter:/use-runtime-workspace\.ts$/},()=>({contents:execFileSync('git',['show','HEAD:src/hooks/use-runtime-workspace.ts'],{encoding:'utf8'}),loader:'ts'}));
  b.onResolve({filter:/^react-i18next$/},()=>({path:'translation',namespace:'fixture'}));
  b.onResolve({filter:/^\.\.\/lib\/(api|api\/agent-runtime|markdown-smoke)$/},args=>args.importer.endsWith('use-runtime-workspace.ts')?{path:args.path,namespace:'fixture'}:undefined);
  b.onLoad({filter:/.*/,namespace:'fixture'},args=>({contents:args.path==='translation'?`const t=k=>k;export const useTranslation=()=>({t});`:args.path.endsWith('markdown-smoke')?`export const isProjectWorkbenchSmokeMode=()=>false;export const markdownSmokeGoals=()=>[];export const markdownSmokeMemories=()=>[];export const markdownSmokeProgress=()=>[];export const markdownSmokeRuns=()=>[];`:args.path.endsWith('agent-runtime')?`export const fetchAgentTurnResponse=async()=>{window.receiptReads++;return window.receipt;};`:`export const fetchDesktopRuntimeSnapshot=async()=>({ok:true,runs:{runs:window.snapshotRuns},questions:{questions:[]}});export const cancelAgentDesktopAction=async()=>({});export const fetchActiveAgentDesktopActions=async()=>({actions:[]});export const fetchWorkspaceDiff=async()=>({});export const refreshUnityReadiness=async()=>({});`,loader:'js'}));
}}]});
const server=createServer((req,res)=>{res.setHeader('Content-Type','text/html');res.end(`<div id="root"></div><script type="module">${bundle.outputFiles[0].text}</script>`);});
let browser;
try{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const {chromium}=createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH||'playwright');
 browser=await chromium.launch({headless:true,channel:'msedge'});const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(`http://127.0.0.1:${server.address().port}`);
 await page.waitForFunction(()=>typeof window.refresh==='function');
 await page.waitForFunction(()=>window.toasts.length===1,{},{timeout:5000});
 assert.equal(await page.locator('#count').textContent(),'1');
 assert.equal(await page.evaluate(()=>window.toasts[0].command),'show_background_goal_notification');
 for(let i=0;i<3;i++)await page.evaluate(()=>window.refresh());
 assert.equal(await page.evaluate(()=>window.receiptReads),1,'terminal receipt cached across snapshot polling');
 assert.equal(await page.evaluate(()=>window.toasts.length),1);
 await page.evaluate(run=>{window.snapshotRuns=[run,{...run,status:'running',clientTurnId:'new',turnId:'new-backend',startedAt:'2026-10-24T10:00:00Z'}];return window.refresh();},run);
 await page.waitForFunction(()=>document.getElementById('count').textContent==='0');
 assert.equal(await page.evaluate(()=>window.toasts.length),1,'resumed run suppresses old blocked receipt');
 await page.evaluate(()=>window.root.unmount());assert.deepEqual(errors,[]);
 console.log('PASS: real workspace hook raw terminal replay sends one native request, exposes review count, caches receipt, suppresses resumed turn');
}finally{await browser?.close();await new Promise(resolve=>server.close(resolve));}
