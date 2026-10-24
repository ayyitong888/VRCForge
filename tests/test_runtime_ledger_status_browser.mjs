import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { build } from "esbuild";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const sourceRoot = process.env.VRCFORGE_LEDGER_SOURCE_ROOT || process.cwd();
const bundle=await build({stdin:{resolveDir:sourceRoot,loader:"tsx",contents:`
import React,{useState} from 'react';import {createRoot} from 'react-dom/client';
import {RuntimeActivityPanel} from './src/components/runtime/runtime-activity-panel';
import {initializeI18n} from './src/i18n';
function Fixture(){const [status,setStatus]=useState('waiting_for_model');window.setStatus=setStatus;
return <RuntimeActivityPanel key={status} runs={[{id:'fixture-run',sessionId:'fixture-session',clientTurnId:'fixture-turn',status,messageSummary:'Actual run ledger fixture'}]} onSaveOperationAsSkill={()=>{}}/>}
void initializeI18n().then(()=>createRoot(document.getElementById('root')).render(<Fixture/>));
`},bundle:true,write:false,format:"iife",platform:"browser",plugins:process.env.VRCFORGE_LEDGER_BASELINE==='1'?[{name:'actual-head-ledger',setup(build){build.onLoad({filter:/runtime-(activity-panel|sidebar-ui)\.tsx$/},args=>({contents:execFileSync('git',['show','HEAD:'+args.path.replaceAll('\\','/').split('/src/')[1].replace(/^/,'src/')],{encoding:'utf8'}),loader:'tsx'}));}}]:[]});
const server=createServer((_req,res)=>{res.setHeader('Content-Type','text/html');res.end(`<style>svg{width:14px;height:14px}</style><div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`)});
let browser;
try {await new Promise(r=>server.listen(0,'127.0.0.1',r));browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto('http://127.0.0.1:'+server.address().port);
for(const status of ['waiting_for_model','waiting_for_tool','starting','queued','claimed','cancelling','cancel_requested','in_progress','running','applying','dispatching']){
await page.evaluate(s=>window.setStatus(s),status);const row=page.locator('[data-vrcforge-runtime-run-status="'+status+'"]');await row.waitFor({timeout:1500});
assert.equal(await row.locator('.animate-pulse').count(),1,status+' must use active visual state');
if(status!=='running' && status!=='applying') assert.ok(!(await row.innerText()).includes(status),status+' must have a user-facing translated label');}
for(const status of ['completed','failed']){await page.evaluate(s=>window.setStatus(s),status);await page.waitForFunction(()=>!document.querySelector('[data-vrcforge-current-run-ledger]'));}
assert.deepEqual(errors,[]);console.log('PASS: real run ledger opens and translates all shared active states, terminal rows remain folded.');
}finally{await browser?.close();await new Promise(r=>server.close(r));}
