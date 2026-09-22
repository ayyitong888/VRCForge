// Local UI-only fixture: no provider credentials or external requests.
// Test server is loopback-only, fixture-owned, and closed in finally.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { build } from "esbuild";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const baselineProviderTourPlugin = process.env.VRCFORGE_PROVIDER_TOUR_BASELINE === "1"
  ? [{
      name: "provider-tour-baseline",
      setup(esbuild) {
        esbuild.onLoad({ filter: /provider-setup-tour\.tsx$/ }, () => ({
          contents: execFileSync("git", ["show", "HEAD:src/components/onboarding/provider-setup-tour.tsx"], { encoding: "utf8" }),
          loader: "tsx",
          resolveDir: path.resolve("src/components/onboarding"),
        }));
      },
    }]
  : [];
const bundle = await build({ stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
import React,{StrictMode,useState} from 'react';import{createRoot}from'react-dom/client';
import i18n from 'i18next';import{initReactI18next}from'react-i18next';import en from './src/locales/en-US.json';
import{ProviderSetupTour}from'./src/components/onboarding/provider-setup-tour';
i18n.use(initReactI18next).init({lng:'en-US',resources:{'en-US':{translation:en}}});
function Fixture(){const[open,setOpen]=useState(false);const[mounted,setMounted]=useState(true);const[count,setCount]=useState(0);const[refreshes,setRefreshes]=useState(0);
window.fixture={open:()=>setOpen(true),unmount:()=>setMounted(false),refreshes:()=>refreshes,scrollInner:()=>{const el=document.querySelector('#settings-scroll');el.scrollTop=120;el.dispatchEvent(new Event('scroll'))}};
return <><output id="returned">{count}</output><output id="refreshes">{refreshes}</output><div id="settings-scroll" style={{height:260,overflowY:'auto',maxWidth:500,margin:'100px auto'}}><main>
{['connection','credentials','model','actions'].map(key=><label key={key} data-onboarding-provider={key} style={{display:'block',padding:15,minHeight:150,marginBottom:20}}>{key}<input aria-label={key}/>{key==='model'&&<button type="button" data-onboarding-provider="models-refresh" onClick={()=>setRefreshes(value=>value+1)}>refresh models</button>}</label>)}
</main></div>{mounted&&<ProviderSetupTour open={open} onReturn={()=>{setCount(c=>c+1);setOpen(false)}}/>}</>}
createRoot(document.getElementById('root')).render(<StrictMode><Fixture/></StrictMode>);` }, bundle: true, write: false, format: "iife", jsx: "automatic", loader: { ".css": "empty" }, plugins: baselineProviderTourPlugin, define: { "process.env.NODE_ENV": '"development"' } });
const css = await readFile('node_modules/driver.js/dist/driver.css','utf8') + await readFile('src/components/onboarding/provider-setup-tour.css','utf8');
const server = createServer((request,response)=>{
  if(request.url==='/fixture.js'){response.setHeader('Content-Type','text/javascript');response.end(bundle.outputFiles[0].text);return;}
  response.setHeader('Content-Type','text/html');response.end(`<style>:root{--card:0 0% 100%;--foreground:0 0% 10%;--muted-foreground:0 0% 30%;--border:0 0% 80%}${css}</style><div id="root"></div><script src="/fixture.js"></script>`);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
let browser;
try {
  browser=await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL||'msedge'});
  const page=await browser.newPage({viewport:{width:1100,height:720},reducedMotion:'reduce'});
  page.on('pageerror', error => console.error('fixture page error:', error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.waitForFunction(()=>Boolean(window.fixture));
  await page.evaluate(()=>window.fixture.open());
  await page.waitForSelector('.driver-popover');
  await page.waitForSelector('.driver-overlay path');
  assert.equal(await page.locator('#returned').innerText(),'0','StrictMode must not end the guide');
  const beforeInnerScroll = await page.evaluate(()=>{const target=document.querySelector('.driver-active-element');return {top:target.getBoundingClientRect().top,path:document.querySelector('.driver-overlay')?.innerHTML}});
  await page.evaluate(()=>window.fixture.scrollInner());
  await page.waitForFunction((before)=>{const target=document.querySelector('.driver-active-element');const path=document.querySelector('.driver-overlay')?.innerHTML;return target&&target.getBoundingClientRect().top < before.top - 50 && path && path !== before.path}, beforeInnerScroll);
  const afterInnerScroll = await page.evaluate(()=>({top:document.querySelector('.driver-active-element').getBoundingClientRect().top,path:document.querySelector('.driver-overlay')?.innerHTML}));
  assert.ok(afterInnerScroll.top < beforeInnerScroll.top - 50,'nested scroll must move the active target');
  assert.notEqual(afterInnerScroll.path,beforeInnerScroll.path,'overlay spotlight must follow nested scroll');
  await page.getByLabel('connection',{exact:true}).fill('editable');
  assert.equal(await page.getByLabel('connection',{exact:true}).inputValue(),'editable');
  await page.keyboard.press('Escape');
  console.log('Escape state:', await page.evaluate(()=>({returned:document.querySelector('#returned').textContent,overlay:!!document.querySelector('.driver-overlay')})));
  await page.waitForFunction(()=>document.querySelector('#returned').textContent==='1');
  assert.equal(await page.locator('.driver-overlay').count(),0);
  await page.evaluate(()=>window.fixture.open());
  await page.locator('.driver-popover-next-btn').click();
  await page.waitForFunction(()=>document.querySelector('[data-onboarding-provider="credentials"]').classList.contains('driver-active-element'));
  await page.locator('.driver-popover-next-btn').click();
  await page.waitForFunction(()=>document.querySelector('[data-onboarding-provider="models-refresh"]').classList.contains('driver-active-element'));
  assert.equal(await page.locator('#refreshes').innerText(),'0','Opening the guide must not refresh models automatically');
  await page.locator('[data-onboarding-provider="models-refresh"]').click();
  assert.equal(await page.locator('#refreshes').innerText(),'1','The guide must leave the real refresh button actionable');
  await page.locator('.driver-popover-next-btn').click();
  await page.waitForFunction(()=>document.querySelector('[data-onboarding-provider="model"]').classList.contains('driver-active-element'));
  await page.locator('.driver-popover-next-btn').click();
  await page.waitForFunction(()=>document.querySelector('[data-onboarding-provider="actions"]').classList.contains('driver-active-element'));
  await page.locator('.driver-popover-next-btn').click();
  await page.waitForFunction(()=>document.querySelector('#returned').textContent==='2');
  await page.evaluate(()=>window.fixture.open());
  await page.waitForSelector('.driver-popover');
  await page.evaluate(()=>window.fixture.unmount());
  await page.waitForSelector('.driver-popover',{state:'detached'});
  assert.equal(await page.locator('#returned').innerText(),'2','Unmount must not return or complete onboarding');
  console.log('provider tour: editable target, Escape/return, missing optional endpoint, StrictMode/unmount: ok');
} finally { if(browser)await browser.close(); await new Promise(resolve=>server.close(resolve)); }
