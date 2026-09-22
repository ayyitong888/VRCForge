// Local UI-only fixture: no provider credentials or external requests.
// Test server is loopback-only, fixture-owned, and closed in finally.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import { build } from "esbuild";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({ stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
import React,{StrictMode,useState} from 'react';import{createRoot}from'react-dom/client';
import i18n from 'i18next';import{initReactI18next}from'react-i18next';import en from './src/locales/en-US.json';
import{ProviderSetupTour}from'./src/components/onboarding/provider-setup-tour';
i18n.use(initReactI18next).init({lng:'en-US',resources:{'en-US':{translation:en}}});
function Fixture(){const[open,setOpen]=useState(false);const[mounted,setMounted]=useState(true);const[count,setCount]=useState(0);
window.fixture={open:()=>setOpen(true),unmount:()=>setMounted(false)};
return <><output id="returned">{count}</output><main style={{maxWidth:500,margin:'100px auto'}}>
{['connection','credentials','model','actions'].map(key=><label key={key} data-onboarding-provider={key} style={{display:'block',padding:15,marginBottom:20}}>{key}<input aria-label={key}/></label>)}
</main>{mounted&&<ProviderSetupTour open={open} onReturn={()=>{setCount(c=>c+1);setOpen(false)}}/>}</>}
createRoot(document.getElementById('root')).render(<StrictMode><Fixture/></StrictMode>);` }, bundle: true, write: false, format: "iife", jsx: "automatic", loader: { ".css": "empty" }, define: { "process.env.NODE_ENV": '"development"' } });
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
  assert.equal(await page.locator('#returned').innerText(),'0','StrictMode must not end the guide');
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
  await page.waitForFunction(()=>document.querySelector('[data-onboarding-provider="model"]').classList.contains('driver-active-element'));
  await page.locator('.vrcforge-provider-tour-return').click();
  await page.waitForFunction(()=>document.querySelector('#returned').textContent==='2');
  await page.evaluate(()=>window.fixture.open());
  await page.waitForSelector('.driver-popover');
  await page.evaluate(()=>window.fixture.unmount());
  await page.waitForSelector('.driver-popover',{state:'detached'});
  assert.equal(await page.locator('#returned').innerText(),'2','Unmount must not return or complete onboarding');
  console.log('provider tour: editable target, Escape/return, missing optional endpoint, StrictMode/unmount: ok');
} finally { if(browser)await browser.close(); await new Promise(resolve=>server.close(resolve)); }
