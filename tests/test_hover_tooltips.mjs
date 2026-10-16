// Production hover surfaces, replaying reported usage values without provider calls.
import assert from "node:assert/strict";
import { readFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const root = resolve(import.meta.dirname, "..");
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({ stdin: {resolveDir:root,loader:"tsx",contents:`
import React from 'react';
import {createRoot} from 'react-dom/client';
import {ContextUsageMeter,AttachmentStrip} from './src/components/chat/composer';
import i18n from './src/i18n';
import {initReactI18next} from 'react-i18next';
import messages from './src/locales/zh-CN.json';
i18n.use(initReactI18next).init({lng:'zh-CN',resources:{'zh-CN':{translation:messages}},interpolation:{escapeValue:false}});
const selected={id:'selection',name:'Selected text',size:12,type:'text/plain',text:'给你三个不同风格的名字，都适合黑白配色的模型：',payloadKind:'text'};
const regular={...selected,id:'regular',name:'notes.txt'};
createRoot(document.getElementById('root')).render(<main style={{padding:'180px 80px'}}>
<ContextUsageMeter usage={{limitKnown:true,exact:true,ratio:10337/500000,used:10337,limit:500000,label:'',cacheHitRatio:0.8544,cacheUsageStatus:'complete'}}/>
{[false,true].map(compact=><section key={String(compact)} data-variant={String(compact)} style={{marginTop:80}}><AttachmentStrip compact={compact} attachments={[selected,regular]} onRemove={()=>{}}/></section>)}
</main>);`},bundle:true,write:false,format:'iife',platform:'browser',jsx:'automatic'});
const css=(await postcss([tailwindcss(resolve(root,'tailwind.config.ts'))]).process(
readFileSync(resolve(root,'src/styles.css'),'utf8').replace('@import "./theme.css";',readFileSync(resolve(root,'src/theme.css'),'utf8')),
{from:resolve(root,'src/styles.css')})).css;
const browser=await chromium.launch({headless:true,channel:'msedge'});
try {
 const page=await browser.newPage({viewport:{width:900,height:800}});
 await page.setContent(`<style>${css}</style><div id="root"></div>`);
 await page.addScriptTag({content:bundle.outputFiles[0].text});
 const meter=page.locator('[data-context-meter]');
 await meter.waitFor();
 await meter.hover();
 const visible=page.locator('[data-context-cache-hit-rate]:visible');
 assert.equal(await visible.count(),1,'one custom usage popup on hover');
 assert.match(await visible.innerText(),/85.44%/);
 const screenshot=process.env.VRCFORGE_TOOLTIP_SCREENSHOT;
 if(screenshot){mkdirSync(resolve(screenshot,'..'),{recursive:true});await page.screenshot({path:screenshot});}
 const titles=await meter.evaluate(el=>{const values=[];for(let node=el;node;node=node.parentElement) if(node.getAttribute('title')) values.push(node.getAttribute('title'));return values});
 assert.deepEqual(titles,[],'hover target and ancestors must not also supply native title');
 assert.match(await meter.getAttribute('aria-label'),/10,337/);
 await page.mouse.move(880,780);
 assert.equal(await visible.count(),0);
 await meter.focus();
 assert.equal(await visible.count(),1,'keyboard focus retains one custom popup');
 for(const variant of ['false','true']) {
  const section=page.locator(`[data-variant="${variant}"]`);
  const remove=section.getByRole('button',{name:'移除',exact:true});
  await remove.first().hover();
  assert.equal(await remove.first().getAttribute('title'),null,'selected-text remove does not stack native tooltip over quote preview');
  assert.equal(await section.locator('span[aria-hidden="true"]:visible').count(),1);
  assert.equal(await remove.last().getAttribute('title'),'移除','ordinary attachment keeps its only tooltip');
 }
 console.log('PASS: screenshot usage values, mouse/focus single popup, full and compact selected-text remove, ordinary attachment title');
} finally {await browser.close();}
