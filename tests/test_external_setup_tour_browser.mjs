// Local UI-only fixture: no provider credentials, connector config, or external requests.
// Test server is loopback-only, fixture-owned, and closed in finally.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const app = await readFile("src/App.tsx", "utf8");
assert.match(app, /showOnboarding && onboardingMinimized && externalSetupGuide && externalAgentVerified/);
assert.match(app, /setActiveView\("chat"\);[\s\S]*?setExternalSetupGuide\(false\);[\s\S]*?setOnboardingMinimized\(false\);/);
assert.match(app, /externalAgentVerified\s*=\s*hasRecentConnectorSelfTest\(/);

const bundle = await build({ stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
import React,{StrictMode,useState} from 'react';import{createRoot}from'react-dom/client';
import i18n from 'i18next';import{initReactI18next}from'react-i18next';import en from './src/locales/en-US.json';
import{ExternalSetupTour}from'./src/components/onboarding/external-setup-tour';
i18n.use(initReactI18next).init({lng:'en-US',resources:{'en-US':{translation:en}}});
const clients=[['codexApp','Codex App'],['codexCli','Codex CLI'],['claudeCode','Claude Code CLI'],['claudeCowork','Claude Cowork App'],['deepseekHarness','DeepSeek Harness'],['generic','Generic client']];
function Fixture(){const[open,setOpen]=useState(false);const[mounted,setMounted]=useState(true);const[returned,setReturned]=useState(0);const[mutations,setMutations]=useState(0);
window.fixture={open:()=>setOpen(true),unmount:()=>setMounted(false),scrollInner:()=>{const el=document.querySelector('#client-scroll');el.scrollTop-=60;el.dispatchEvent(new Event('scroll'))}};
return <><output id="returned">{returned}</output><output id="mutations">{mutations}</output><div data-onboarding-external="gateway" style={{margin:'40px auto',maxWidth:600}}><button onClick={()=>setMutations(x=>x+1)}>Gateway toggle</button></div><div id="client-scroll" style={{height:260,overflowY:'auto',maxWidth:600,margin:'0 auto'}}>{clients.map(([id,label])=><div key={id} data-onboarding-client={id} data-onboarding-client-label={label} style={{height:150,marginBottom:20,padding:20,border:'1px solid gray'}}><span>{label}</span><button onClick={()=>setMutations(x=>x+1)}>Install</button><input type="checkbox" onChange={()=>setMutations(x=>x+1)}/></div>)}</div>{mounted&&<ExternalSetupTour open={open} onReturn={()=>{setReturned(x=>x+1);setOpen(false)}}/>}</>}
createRoot(document.getElementById('root')).render(<StrictMode><Fixture/></StrictMode>);` }, bundle: true, write: false, format: "iife", jsx: "automatic", loader: { ".css": "empty" }, define: { "process.env.NODE_ENV": '"development"' } });
const css = await readFile("node_modules/driver.js/dist/driver.css", "utf8") + await readFile("src/components/onboarding/provider-setup-tour.css", "utf8");
const server = createServer((request,response)=>{
  if(request.url==="/fixture.js"){response.setHeader("Content-Type","text/javascript");response.end(bundle.outputFiles[0].text);return;}
  response.setHeader("Content-Type","text/html");response.end(`<style>:root{--card:0 0% 100%;--foreground:0 0% 10%;--muted-foreground:0 0% 30%;--border:0 0% 80%}${css}</style><div id="root"></div><script src="/fixture.js"></script>`);
});
await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
let browser;
try {
  browser=await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL||"msedge"});
  const page=await browser.newPage({viewport:{width:1100,height:720},reducedMotion:"no-preference"});
  page.setDefaultTimeout(5000);
  page.on("pageerror", error => console.error("fixture page error:", error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.waitForFunction(()=>Boolean(window.fixture));
  await page.evaluate(()=>window.fixture.open());
  await page.waitForSelector(".driver-popover");
  assert.equal(await page.locator('[data-onboarding-external="gateway"].driver-active-element').count(),1);
  assert.equal(await page.locator("#mutations").innerText(),"0");
  await page.locator(".driver-popover-next-btn").click();
  await page.waitForSelector(".vrcforge-external-tour-client-picker select");
  const clientSelect=page.locator(".vrcforge-external-tour-client-picker select");
  assert.equal(await clientSelect.locator("option").count(),6);
  await clientSelect.selectOption("deepseekHarness");
  await page.waitForFunction(()=>document.querySelector('[data-onboarding-client="deepseekHarness"]')?.classList.contains("driver-active-element"));
  assert.equal(await page.locator("#mutations").innerText(),"0","client selection must not install, click, or toggle anything");
  assert.equal(await page.locator(".driver-active-element").count(),1,"switching clients must clear the previous highlight");
  const before=await page.evaluate(()=>({top:document.querySelector(".driver-active-element").getBoundingClientRect().top,path:document.querySelector(".driver-overlay path")?.getAttribute("d")}));
  await page.evaluate(()=>window.fixture.scrollInner());
  await page.waitForFunction((old)=>{const target=document.querySelector(".driver-active-element");const path=document.querySelector(".driver-overlay path")?.getAttribute("d");return target&&Math.abs(target.getBoundingClientRect().top-old.top)>50&&path&&path!==old.path},before);
  const after=await page.evaluate(()=>({top:document.querySelector(".driver-active-element").getBoundingClientRect().top,path:document.querySelector(".driver-overlay path")?.getAttribute("d")}));
  assert.ok(Math.abs(after.top-before.top)>50);assert.notEqual(after.path,before.path);
  await page.locator(".driver-popover-next-btn").click();
  await page.waitForFunction(()=>document.querySelector('[data-onboarding-client="deepseekHarness"]')?.classList.contains("driver-active-element"));
  assert.equal(await page.locator("#mutations").innerText(),"0");
  await page.keyboard.press("Escape");
  await page.waitForFunction(()=>document.querySelector("#returned").textContent==="1");
  assert.equal(await page.locator(".driver-overlay").count(),0);
  await page.evaluate(()=>window.fixture.open());await page.waitForSelector(".driver-popover");
  await page.evaluate(()=>window.fixture.unmount());await page.waitForSelector(".driver-popover",{state:"detached"});
  assert.equal(await page.locator("#returned").innerText(),"1","unmount must not return or mutate onboarding");
  console.log("external setup tour: gateway/client selection, no-op actions, nested scroll, Escape, unmount: ok");
} finally { if(browser)await browser.close(); await new Promise(resolve=>server.close(resolve)); }
