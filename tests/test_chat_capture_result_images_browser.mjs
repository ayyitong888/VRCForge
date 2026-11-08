import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import postcss from 'postcss';
import tailwindcss from 'tailwindcss';
const uri=`vrcforge://scene-capture-preview/8d886299-9be1-4baf-8e1f-e205f3a8e5fc?context=runtime%3Asession-fixture&revision=1&store=${'1'.repeat(32)}`;
const preview={schema:'vrcforge.scene_capture_preview.v1',mimeType:'image/png',captureId:'8d886299-9be1-4baf-8e1f-e205f3a8e5fc',resourceUri:uri};
const backId='f75e7e6b-e22e-40c3-af08-1b19ce0d9578';
const back={...preview,captureId:backId,resourceUri:uri.replace(preview.captureId,backId)};
const againId='f10d4588-76af-4608-8893-f631bb8c7561';
const frontAgain={...preview,captureId:againId,resourceUri:uri.replace(preview.captureId,againId)};
const response=process.env.VRCFORGE_CAPTURE_RECEIPT?JSON.parse(await readFile(process.env.VRCFORGE_CAPTURE_RECEIPT,'utf8')).response:{sessionId:'session-fixture',clientTurnId:'turn-fixture',plan:{summary:'Captured result',reply:'Review the result.'},steps:[
 {kind:'tool',actionId:'first-shot',result:{captures:[{angle:'back',preview:back},{angle:'front',preview}]}},
 {kind:'tool',actionId:'second-shot',result:{captures:[{angle:'front',preview:frontAgain},{angle:'duplicate',preview}]}},
]};
const captures=response.steps.flatMap(step=>step.result?.captures || []).filter(c=>c.preview?.schema==='vrcforge.scene_capture_preview.v1');
const unique=new Set(captures.map(c=>c.preview.resourceUri));
assert.ok(unique.size);
const bundle=await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import React from 'react';import {createRoot} from 'react-dom/client';
import {ConversationCard} from './src/components/chat/conversation-card';import {initializeI18n} from './src/i18n';
const root=createRoot(document.getElementById('root'));
window.mount=async(item,context)=>{await initializeI18n();root.render(<ConversationCard item={item} chatResourceImageContext={context}/>);};
`},bundle:true,write:false,format:'iife',platform:'browser',jsx:'automatic'});
const css=(await postcss([tailwindcss("./tailwind.config.ts")]).process((await readFile("src/theme.css","utf8"))+"\n@tailwind base; @tailwind components; @tailwind utilities;",{from:undefined})).css;
const server=createServer((req,res)=>{res.setHeader('Content-Type','text/html');res.end('<style>'+css+'</style><div id="root"></div><script>'+bundle.outputFiles[0].text.replaceAll('</script','<\\/script')+'</script>');});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const require=createRequire(import.meta.url);const{chromium}=require(process.env.VRCFORGE_PLAYWRIGHT_PATH||'playwright');let browser;
try{
 browser=await chromium.launch({headless:true,channel:'msedge'});const page=await browser.newPage();const calls=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
 const endpoint=`http://127.0.0.1:${server.address().port}`;await page.goto(endpoint);const context={endpoint,chatId:'fixture',projectPath:'fixture-project'};
 const pngBase64=await page.evaluate(()=>{
   const canvas=document.createElement('canvas');canvas.width=720;canvas.height=720;
   return canvas.toDataURL().split(',')[1];
 });
 await page.route('**/api/app/agent/resource-image',async route=>{const request=route.request().postDataJSON();calls.push(request);assert.ok(unique.has(request.uri));await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({uri:request.uri,mimeType:'image/png',sha256:'a'.repeat(64),pngBase64})});});
 await page.evaluate(({response,context})=>window.mount({id:'final',type:'agent',response},context),{response,context});
 if(response.timeline?.some(event=>event.kind==='tool_result')) {
   await page.locator('[data-agent-turn-process-group] > div > button').first().click();
 }
 const groups=page.locator('[data-agent-capture-images]');
 await groups.first().waitFor();
 assert.equal(await groups.count(),1,'images from different capture receipts share one gallery');
 assert.equal(await page.locator('[data-agent-capture-image] img').count(),0,'tool image activities start collapsed');
 assert.equal(calls.length,0,'named references do not fetch unopened images');
 const firstLink=groups.locator('[data-agent-capture-link]').first();
 if(!process.env.VRCFORGE_CAPTURE_RECEIPT) {
   assert.deepEqual(await groups.locator('[data-agent-capture-link]').allInnerTexts(),[
     'Front view · Capture 1','Back view','Front view · Capture 2',
   ],'views are ordered within their original receipts, and repeated angles identify the capture');
 }
 await firstLink.click();
 await page.locator('dialog[open] img').waitFor();
 assert.equal(calls.length,1,'a named reference opens only the selected capture');
 await page.keyboard.press('Escape');
 await page.locator('dialog[open]').waitFor({state:'detached'});
 if(!process.env.VRCFORGE_CAPTURE_RECEIPT) {
   assert.equal(calls[0].uri,uri);
   await groups.getByRole('button',{name:'Front view · Capture 2',exact:true}).click();
   await page.locator('dialog[open] img').waitFor();
   assert.equal(calls.at(-1).uri,frontAgain.resourceUri,'the later front image opens its own immutable resource');
   await page.keyboard.press('Escape');
   await page.locator('dialog[open]').waitFor({state:'detached'});
 }
 await groups.locator('button[aria-expanded=false]').click();
 await page.waitForFunction(count=>document.querySelectorAll('[data-agent-capture-image] img').length===count,unique.size);
 if(!process.env.VRCFORGE_CAPTURE_RECEIPT) {
   const batches=groups.locator('[data-agent-capture-receipt]');
   assert.deepEqual(await batches.locator('h3').allInnerTexts(),['Capture 1','Capture 2']);
   assert.deepEqual(await batches.locator('img').evaluateAll(images=>images.map(image=>image.alt)),[
     'Front view · Capture 1','Back view','Front view · Capture 2',
   ]);
 }
 const firstCapture=page.locator('[data-agent-capture-image]').first();
 const thumbnail=firstCapture.locator('img');
 assert.equal(await thumbnail.evaluate(img=>getComputedStyle(img).objectFit),'contain','a square capture must fit fully inside the wide thumbnail');
 const source=await thumbnail.getAttribute('src');
 const initialFetchCount=calls.length;
 const dialog=page.locator('dialog[open]');
 const openPreview=async()=>{
   await firstCapture.click();
   await dialog.locator('img').waitFor();
 };
 await openPreview();
 const image=dialog.locator('img');
 assert.equal(await image.getAttribute('src'),source,'full-size preview and thumbnail display the same pixels');
 const close=dialog.getByRole('button',{name:'Close image preview'});
 const closeBox=await close.boundingBox();
 const viewport=page.viewportSize();
 assert.ok(closeBox.y<60&&closeBox.x+closeBox.width>viewport.width-60,'close control is in the top right');
 const fit=(await image.boundingBox()).width;
 await dialog.getByRole('button',{name:'Zoom in'}).click();
 assert.ok((await image.boundingBox()).width>fit,'zoom in increases image size');
 await dialog.getByRole('button',{name:'Zoom out'}).click();
 assert.equal((await image.boundingBox()).width,fit,'zoom out returns to fit');
 await image.hover();
 await page.mouse.wheel(0,-120);
 await dialog.getByText('125%',{exact:true}).waitFor();
 await page.mouse.wheel(0,120);
 await dialog.getByText('100%',{exact:true}).waitFor();
 await image.click();
 assert.equal(await dialog.count(),1,'clicking the image keeps it open');
 await close.click();
 await dialog.waitFor({state:'detached'});
 await openPreview();
 await page.mouse.click(8,viewport.height/2);
 await dialog.waitFor({state:'detached'});
 await openPreview();
 await page.keyboard.press('Escape');
 await dialog.waitFor({state:'detached'});
 assert.equal(calls.length,initialFetchCount,'preview does not fetch the capture again');
 assert.equal(await page.locator('[data-agent-capture-image]').count(),unique.size,'each immutable capture renders once, independently of model prose');
 // A durable tool-result marker without captures must not suppress the persisted step previews.
 const markerOnly={...response,timeline:[{id:'marker',sequence:0,kind:'tool_result',timestamp:new Date().toISOString(),payload:{tool:'capture',actionId:'capture',status:'ok'}}]};
 await page.evaluate(({response,context})=>window.mount({id:'marker-only',type:'agent',response},context),{response:markerOnly,context});
 await page.locator('[data-agent-capture-images]').waitFor();
 while(await page.locator('[data-agent-capture-images] button[aria-expanded=false]').count()) await page.locator('[data-agent-capture-images] button[aria-expanded=false]').first().click();
 await page.waitForFunction(count=>document.querySelectorAll('[data-agent-capture-image] img').length===count,unique.size);
 assert.equal(await page.locator('[data-agent-capture-image]').count(),unique.size,'step previews remain visible when a tool-result marker has no captures');
 assert.equal(await page.locator('[data-agent-turn-process-group] [data-agent-capture-images]').count(),0,'gallery is available outside the folded work log');
 assert.ok(calls.every(c=>c.sessionId===response.sessionId&&c.clientTurnId===response.clientTurnId));
 const live={id:'live',type:'streaming',clientTurnId:response.clientTurnId,text:'Checking the captured result.',timeline:[{id:'capture',sequence:0,kind:'tool_result',timestamp:new Date().toISOString(),payload:{tool:'capture',actionId:'capture',captures}}]};
 await page.evaluate(({live,context})=>window.mount(live,context),{live,context});
 await page.locator('[data-conversation-streaming-turn] [data-agent-capture-images]').first().waitFor();
 while(await page.locator('[data-conversation-streaming-turn] [data-agent-capture-images] button[aria-expanded=false]').count()) await page.locator('[data-conversation-streaming-turn] [data-agent-capture-images] button[aria-expanded=false]').first().click();
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
 assert.equal(order[0],preparing,'commentary remains above the grouped captures');
 assert.match(order[1],new RegExp(` · ${unique.size}`),'collapsed image activity shows its image count');
 const consecutive={...ordered,id:'consecutive',timeline:[
   event('a','tool_call',0,{tool:'capture',actionId:'a',status:'started'}),
   event('ar','tool_result',1,{tool:'capture',actionId:'a',status:'ok',captures:captures.slice(0,1)}),
   event('b','tool_call',2,{tool:'capture',actionId:'b',status:'started'}),
   event('br','tool_result',3,{tool:'capture',actionId:'b',status:'ok',captures:captures.slice(1)}),
 ]};
 await page.evaluate(({item,context})=>window.mount(item,context),{item:consecutive,context});
 await page.locator('[data-agent-capture-images]').waitFor();
 assert.equal(await page.locator('[data-agent-tool-call-group]').count(),1,'capture receipts do not split a consecutive tool group');
 assert.equal(await page.locator('[data-agent-capture-images]').count(),1,'consecutive captures have one row');
 const retired={id:'retired-preview',type:'agent',response:{...response,timeline:[],steps:[
   {kind:'tool',actionId:'retired-shot',result:{captures:[{angle:'front',preview:{...preview,resourceUri:uri.replace(/\?.*$/, '?legacy=1')}}]}},
 ]}};
 await page.evaluate(({item,context})=>window.mount(item,context),{item:retired,context});
 const retiredGallery=page.locator('[data-agent-capture-images]');
 await retiredGallery.locator('button[aria-expanded=false]').click();
 const unavailable=retiredGallery.locator('[data-agent-capture-image] [role=status]');
 await unavailable.getByText('Image unavailable',{exact:true}).waitFor();
 assert.ok((await unavailable.boundingBox()).height>=90,'unavailable preview keeps the thumbnail slot');
 assert.equal(await retiredGallery.locator('[data-agent-capture-image]').getByText('Front view',{exact:true}).count(),1,'the view caption appears once');
 const callCount=calls.length;
 await retiredGallery.locator('[data-agent-capture-link]').click();
 assert.equal(await retiredGallery.getByText('Image unavailable',{exact:true}).count(),2,'opening an expired reference gives feedback');
 assert.equal(calls.length,callCount,'an obsolete reference does not cross the image resource boundary');
 assert.deepEqual(errors,[]);console.log(`PASS final/live capture activities, dialog, ${unique.size} previews, exact turn context, commentary dedup and result-time ordering`);
}finally{await browser?.close();await new Promise(r=>server.close(r));}
