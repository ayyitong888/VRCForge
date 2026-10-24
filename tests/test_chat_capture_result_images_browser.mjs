import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
const uri=`vrcforge://scene-capture-preview/8d886299-9be1-4baf-8e1f-e205f3a8e5fc?context=runtime%3Asession-fixture&revision=1&store=${'1'.repeat(32)}`;
const preview={schema:'vrcforge.scene_capture_preview.v1',mimeType:'image/png',captureId:'8d886299-9be1-4baf-8e1f-e205f3a8e5fc',resourceUri:uri};
const response=process.env.VRCFORGE_CAPTURE_RECEIPT?JSON.parse(await readFile(process.env.VRCFORGE_CAPTURE_RECEIPT,'utf8')).response:{sessionId:'session-fixture',clientTurnId:'turn-fixture',plan:{summary:'Captured result',reply:'Review the result.'},steps:[{kind:'tool',result:{captures:[{angle:'front',preview},{angle:'duplicate',preview}]}}]};
const captures=response.steps.flatMap(step=>step.result?.captures || []).filter(c=>c.preview?.schema==='vrcforge.scene_capture_preview.v1');
const unique=new Set(captures.map(c=>c.preview.resourceUri));
assert.ok(unique.size);
const bundle=await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import React from 'react';import {createRoot} from 'react-dom/client';
import {ConversationCard} from './src/components/chat/conversation-card';import {initializeI18n} from './src/i18n';
const root=createRoot(document.getElementById('root'));
window.mount=async(item,context)=>{await initializeI18n();root.render(<ConversationCard item={item} chatResourceImageContext={context}/>);};
`},bundle:true,write:false,format:'iife',platform:'browser',jsx:'automatic'});
const server=createServer((req,res)=>{res.setHeader('Content-Type','text/html');res.end('<div id="root"></div><script>'+bundle.outputFiles[0].text.replaceAll('</script','<\\/script')+'</script>');});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const require=createRequire(import.meta.url);const{chromium}=require(process.env.VRCFORGE_PLAYWRIGHT_PATH||'playwright');let browser;
try{
 browser=await chromium.launch({headless:true,channel:'msedge'});const page=await browser.newPage();const calls=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/app/agent/resource-image',async route=>{const request=route.request().postDataJSON();calls.push(request);assert.ok(unique.has(request.uri));await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({uri:request.uri,mimeType:'image/png',sha256:'a'.repeat(64),pngBase64:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5foAAAAASUVORK5CYII='})});});
 const endpoint=`http://127.0.0.1:${server.address().port}`;await page.goto(endpoint);const context={endpoint,chatId:'fixture',projectPath:'fixture-project'};
 await page.evaluate(({response,context})=>window.mount({id:'final',type:'agent',response},context),{response,context});
 if(response.timeline?.some(event=>event.kind==='tool_result')) {
   await page.locator('[data-agent-turn-process-group] > div > button').first().click();
 }
 await page.locator('[data-agent-capture-images]').first().waitFor();
 assert.equal(await page.locator('[data-agent-capture-image] img').count(),0,'tool image activities start collapsed');
 while(await page.locator('[data-agent-capture-images] > button[aria-expanded=false]').count()) await page.locator('[data-agent-capture-images] > button[aria-expanded=false]').first().click();
 await page.waitForFunction(count=>document.querySelectorAll('[data-agent-capture-image] img').length===count,unique.size);
 await page.locator('[data-agent-capture-image]').first().click();
 await page.locator('dialog[open] img').waitFor();
 await page.keyboard.press('Escape');
 await page.waitForFunction(()=>document.querySelectorAll('dialog[open]').length===0);
 assert.equal(await page.locator('[data-agent-capture-image]').count(),unique.size,'each immutable capture renders once, independently of model prose');
 if(response.timeline?.some(event=>event.kind==='tool_result')) {
   assert.ok(await page.locator('[data-agent-turn-process-group] [data-agent-capture-images]').count()>0,'capture groups remain at tool activity positions');
   assert.ok(await page.locator('[data-agent-capture-images]').evaluateAll(groups=>groups.every(group=>group.previousElementSibling?.hasAttribute('data-agent-tool-call-group'))),'each capture group follows its source tool group');
 }
 assert.ok(calls.every(c=>c.sessionId===response.sessionId&&c.clientTurnId===response.clientTurnId));
 const live={id:'live',type:'streaming',clientTurnId:response.clientTurnId,text:'Checking the captured result.',timeline:[{id:'capture',sequence:0,kind:'tool_result',timestamp:new Date().toISOString(),payload:{tool:'capture',actionId:'capture',captures}}]};
 await page.evaluate(({live,context})=>window.mount(live,context),{live,context});
 await page.locator('[data-conversation-streaming-turn] [data-agent-capture-images]').first().waitFor();
 while(await page.locator('[data-conversation-streaming-turn] [data-agent-capture-images] > button[aria-expanded=false]').count()) await page.locator('[data-conversation-streaming-turn] [data-agent-capture-images] > button[aria-expanded=false]').first().click();
 await page.waitForFunction(count=>document.querySelectorAll('[data-conversation-streaming-turn] [data-agent-capture-image] img').length===count,unique.size);
 assert.equal(await page.locator('[data-agent-capture-image]').count(),unique.size,'live result has a correctly bound image context');
 // Reproduce durable commentary arriving before the live buffer clears.
 const commentary='Checking the current outfit.';
 const event=(id,kind,sequence,payload)=>({id,kind,sequence,timestamp:`2026-10-25T00:00:0${sequence}Z`,payload});
 const duplicate={id:'duplicate',type:'streaming',clientTurnId:response.clientTurnId,text:commentary,timeline:[event('commentary','assistant',0,{phase:'commentary',summary:commentary})]};
 await page.evaluate(({item,context})=>window.mount(item,context),{item:duplicate,context});
 await page.waitForFunction(text=>document.querySelector('[data-conversation-streaming-turn]')?.textContent.includes(text),commentary);
 assert.deepEqual(await page.locator('[data-chat-quote-content]').allTextContents(),[commentary],'durable commentary and identical live buffer display once');
 const preparing='The camera is still preparing.';
 const ordered={id:'ordered',type:'streaming',clientTurnId:response.clientTurnId,text:'',timeline:[
   event('call','tool_call',0,{tool:'capture',actionId:'capture',status:'started'}),
   event('update','assistant',1,{phase:'commentary',summary:preparing}),
   event('result','tool_result',2,{tool:'capture',actionId:'capture',status:'ok',captures}),
 ]};
 await page.evaluate(({item,context})=>window.mount(item,context),{item:ordered,context});
 await page.locator('[data-agent-capture-images]').waitFor();
 const order=await page.locator('[data-agent-capture-images], [data-chat-quote-content]').allTextContents();
 assert.equal(order[0],preparing,'capture evidence belongs at result time after intervening commentary, not call time');
 assert.match(order[1],new RegExp(` · ${unique.size}`),'collapsed image activity shows its image count');
 assert.deepEqual(errors,[]);console.log(`PASS final/live capture activities, dialog, ${unique.size} previews, exact turn context, commentary dedup and result-time ordering`);
}finally{await browser?.close();await new Promise(r=>server.close(r));}
