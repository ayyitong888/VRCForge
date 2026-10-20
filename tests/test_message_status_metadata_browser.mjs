import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { readFile, writeFile } from "node:fs/promises";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const original = process.env.VRCFORGE_MESSAGE_STATUS_REPLAY
  ? JSON.parse(await readFile(process.env.VRCFORGE_MESSAGE_STATUS_REPLAY, "utf8")) : [];
const css = (await postcss([tailwindcss("./tailwind.config.ts")]).process(
  (await readFile("src/theme.css", "utf8")) + "\n@tailwind base; @tailwind components; @tailwind utilities;", {from:undefined},
)).css;
const bundle = await build({stdin:{resolveDir:process.cwd(),loader:"tsx",contents:`
import React from 'react';
import {createRoot} from 'react-dom/client';
import {ConversationCard} from './src/components/chat/conversation-card';
import {initializeI18n,setLocale} from './src/i18n';
const original = ${JSON.stringify(original)};
const source = original.length ? original : [{id:'original-unverified',type:'user',text:'最后简单告诉我哪些已经做好，哪些地方还没完成。',queueStatus:'delivery_unverified',queuedFrom:true,createdAt:'2026-10-19T12:00:00Z'}];
const statuses = ['steering','queued','waiting_for_resources','paused','cancelled'];
window.originalBefore = JSON.stringify(source);
window.originalAfter = () => JSON.stringify(source);
window.locale = setLocale;
function Card({item,editing=false}) { return <ConversationCard item={item} editing={editing} editingText={item.text}
 canEdit canRetry onCopyItem={()=>{}} onEditItem={()=>{}} onRetryItem={()=>{}} onEditItemSave={()=>{}} onEditItemCancel={()=>{}} />; }
void initializeI18n().then(()=>setLocale('zh-CN')).then(()=>createRoot(document.getElementById('root')).render(
 <main className="mx-auto max-w-3xl space-y-6 py-6 text-foreground">
  <div id="preview" className="space-y-6">
   <section data-case="confirmed"><Card item={{id:'confirmed',type:'user',text:'整理后让我在 Hierarchy 里能一眼分清原装、alternate 和暂时关闭的衣服。',queuedFrom:true,createdAt:'2026-10-19T12:00:00Z'}} /></section>
   <section data-case="steering"><Card item={{id:'steering',type:'user',text:'最后简单告诉我哪些已经做好，哪些地方还没完成。',queueStatus:'steering',queuedFrom:true,createdAt:'2026-10-19T12:00:00Z'}} /></section>
   {source.map(item=><section key={item.id} data-case="unverified"><Card item={item} /></section>)}
  </div>
  {statuses.filter(status=>status!=='steering').map(status=><section key={status} data-case={status}><Card item={{id:status,type:'user',text:'补充说明：保留当前任务的范围。',queueStatus:status,queuedFrom:true,createdAt:'2026-10-19T12:00:00Z'}} /></section>)}
  <section data-case="editing-confirmed"><Card editing item={{id:'editing-confirmed',type:'user',text:'编辑已确认送达的输入。',queuedFrom:true}} /></section>
  <section data-case="editing-unverified"><Card editing item={{id:'editing-unverified',type:'user',text:'编辑未确认送达的输入。',queueStatus:'delivery_unverified',queuedFrom:true}} /></section>
 </main>));
`},bundle:true,write:false,format:"iife",platform:"browser",jsx:"automatic"});
const server=createServer((_request,response)=>{
 response.setHeader('Content-Type','text/html');
 response.end(`<style>${css}</style><div id="root" style="width:1040px;margin:auto"></div><script>${bundle.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`);
});
let browser;
try {
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1100,height:1100}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(`http://127.0.0.1:${server.address().port}`);
 await page.locator('[data-case="steering"] [data-user-message-status]').waitFor();
 assert.equal(await page.locator('[data-case="confirmed"] [data-user-message-status]').count(),0);
 assert.equal(await page.locator('[data-case="editing-confirmed"] [data-user-message-status]').count(),0);
 assert.equal((await page.locator('[data-case="editing-confirmed"]').innerText()).includes('已追加到对话'),false);
 const geometry=[];
 for(const status of ['steering','queued','waiting_for_resources','paused','cancelled','unverified']) {
  const cases=await page.locator(`[data-case="${status}"]`).all();
  for(const entry of cases) {
   const metadata=entry.locator('[data-user-message-status]');
   const bubble=await entry.locator('[data-user-message-bubble]').boundingBox();
   const rect=await metadata.boundingBox();
   assert.ok(rect.y>=bubble.y+bubble.height,'status belongs below the message body');
   assert.equal(await metadata.locator('xpath=..').evaluate(element=>element.hasAttribute('data-chat-message-actions')),true);
   const style=await metadata.evaluate(element=>{const s=getComputedStyle(element);return {size:s.fontSize,background:s.backgroundColor,border:s.borderWidth,radius:s.borderRadius};});
   assert.deepEqual(style,{size:'11px',background:'rgba(0, 0, 0, 0)',border:'0px',radius:'0px'});
   const copy=await entry.locator('[data-chat-message-actions] button').first().boundingBox();
   assert.ok(Math.abs(rect.y+rect.height/2-copy.y-copy.height/2)<1,'status shares the time/actions line');
   assert.equal(await metadata.locator('svg').count(),status==='steering'?1:0);
   geometry.push({status,belowBubble:true,sameActionsLine:true,smallPlainText:true});
  }
 }
 const expected={'zh-CN':['未确认送达','发送状态未确认 · 输入已保留'],'zh-TW':['未確認送達','傳送狀態未確認 · 輸入已保留'],'ja-JP':['送信未確認','送信状態を確認できません · 入力は保持されています'],'en-US':['Delivery unconfirmed','Delivery not confirmed · input retained']};
 for(const [locale,[short,full]] of Object.entries(expected)) {
  await page.evaluate(locale=>window.locale(locale),locale);
  const metadata=page.locator('[data-case="unverified"] [data-user-message-status]').first();
  assert.equal(await metadata.innerText(),short);
  assert.equal(await metadata.getAttribute('title'),full);
  assert.equal(await metadata.getAttribute('aria-label'),full);
  assert.ok((await metadata.ariaSnapshot()).includes(full),'full delivery explanation is exposed in the accessibility tree');
 }
 assert.equal(await page.evaluate(()=>window.originalAfter()===window.originalBefore),true,'presentation never changes old message state');
 await page.evaluate(()=>window.locale('zh-CN'));
 if(process.env.VRCFORGE_MESSAGE_STATUS_EVIDENCE_DIR) {
  const dir=process.env.VRCFORGE_MESSAGE_STATUS_EVIDENCE_DIR;
  await page.locator('#preview').screenshot({path:dir+'/metadata-light.png'});
  await page.locator('main').screenshot({path:dir+'/all-states-light.png'});
  await page.locator('html').evaluate(element=>element.classList.add('dark'));
  await page.locator('#preview').screenshot({path:dir+'/metadata-dark.png'});
  await writeFile(dir+'/visual-validation.json',JSON.stringify({geometry,locales:4,accessibleTreeFullExplanation:true,confirmedNoMetadata:true,editingNoQueuedSent:true,oldStateUntouched:true,originalMessages:original.length,pageErrors:errors},null,2));
 }
 assert.deepEqual(errors,[]);
 console.log('message status metadata: below-bubble action line, plain small text, spinner, accessible full labels, four locales, unchanged history passed');
} finally {await browser?.close();await new Promise(resolve=>server.close(resolve));}
