// Real settings controller -> connector API with loopback-only fixture data.
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {build} from "esbuild";
import {chromium} from "playwright";
const bundle=await build({stdin:{resolveDir:process.cwd(),loader:"tsx",contents:`
 import React,{useEffect} from 'react';import {createRoot} from 'react-dom/client';
 import i18n from 'i18next';import {initReactI18next} from 'react-i18next';
 import en from './src/locales/en-US.json';
 import {CheckpointStoragePanel} from './src/components/settings/checkpoint-storage-panel';
 import {useSettingsWorkspaceController} from './src/hooks/use-settings-workspace-controller';
 i18n.use(initReactI18next).init({lng:'en',resources:{en:{translation:en}},interpolation:{escapeValue:false}});
 window.errors=[];
 function Fixture(){
  const c=useSettingsWorkspaceController({endpoint:location.origin,runtimeConnected:true,activeProjectPath:'',
   setActiveView:()=>{},startRuntime:async()=>location.origin,refresh:async()=>{},
   setError:e=>window.errors.push(e),setDoctorMessage:()=>{}});
  useEffect(()=>{void c.loadConnectors()},[]);
  return <CheckpointStoragePanel status={c.connectorStatus} loading={c.loadingConnectors} isDesktop={false}
   limitInput={c.checkpointArchiveLimitInput} countInput={c.checkpointArchiveCountInput}
   onLimitInputChange={c.setCheckpointArchiveLimitInput} onCountInputChange={c.setCheckpointArchiveCountInput}
   onSaveLimit={()=>void c.saveCheckpointArchiveLimit()} onOpenFolder={()=>{}} onPickDirectory={async()=>''}
   onDeleteSelected={()=>{}} onRelocate={()=>{}}/>;
 }
 createRoot(document.getElementById('root')).render(<Fixture/>);
`},bundle:true,write:false,format:'iife',jsx:'automatic',define:{'process.env.NODE_ENV':'"test"'}});
let count=4;const writes=[];
const status=()=>({ok:true,clients:[],gateway:{checkpointArchiveMaxCount:count,checkpointArchiveMaxSizeMb:10240,
 checkpointArchiveUsage:{directory:'/fixture',archiveCount:2,sizeBytes:256,maxCount:count,archives:[]}}});
const server=createServer(async(req,res)=>{
 res.setHeader('Content-Type','application/json');
 if(req.url.startsWith('/api/app/external-agent/connectors'))return res.end(JSON.stringify(status()));
 if(req.url==='/api/app/external-agent/gateway'){
  let data='';for await(const chunk of req)data+=chunk;const args=JSON.parse(data);writes.push(args);count=args.checkpointArchiveMaxCount;
  return res.end(JSON.stringify(status()));
 }
 if(req.url==='/fixture.js'){res.setHeader('Content-Type','text/javascript');return res.end(bundle.outputFiles[0].text)}
 res.setHeader('Content-Type','text/html');res.end('<div id="root"></div><script src="/fixture.js"></script>');
});
await new Promise(done=>server.listen(0,'127.0.0.1',done));let browser;
try{
 browser=await chromium.launch({headless:true,channel:'msedge'});const page=await browser.newPage();
 const pageErrors=[];page.on('pageerror',e=>pageErrors.push(e.message));
 const origin=`http://127.0.0.1:${server.address().port}`;await page.goto(origin);
 const input=page.getByRole('spinbutton',{name:'Retained backup count (0 = unlimited)'});
 await page.waitForFunction(()=>document.querySelectorAll('input[type="number"]')[1]?.value==='4');
 assert.equal(await input.inputValue(),'4');
 await input.fill('2');await page.getByRole('button',{name:'Save',exact:true}).click();
 await page.waitForFunction(()=>!document.querySelector('input[type="number"]').disabled);
 assert.equal(writes.length,1);assert.equal(writes[0].checkpointArchiveMaxCount,2);assert.equal(writes[0].checkpointArchiveMaxSizeMb,10240);
 await input.fill('0');await page.getByRole('button',{name:'Save',exact:true}).click();
 await page.waitForFunction(()=>!document.querySelector('input[type="number"]').disabled);
 assert.equal(writes.length,2);assert.equal(writes[1].checkpointArchiveMaxCount,0);
 await input.fill('1.5');await page.getByRole('button',{name:'Save',exact:true}).click();
 assert.equal(writes.length,2);assert.match((await page.evaluate(()=>window.errors)).at(-1),/whole backup count/);
 await page.reload();await input.waitFor();
 await page.waitForFunction(()=>document.querySelectorAll('input[type="number"]')[1]?.value==='0');
 assert.equal(await input.inputValue(),'0');assert.deepEqual(pageErrors,[]);
 assert.match(await page.locator('body').innerText(),/in-flight operation/);
 console.log('PASS: default four, editable count save with size limit, explicit unlimited, integer validation, and reloaded server value via real controller/API.');
}finally{await browser?.close();server.closeAllConnections();await new Promise(done=>server.close(done))}
