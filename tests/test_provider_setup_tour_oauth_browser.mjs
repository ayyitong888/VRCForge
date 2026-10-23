// Real settings components; no credentials, account endpoints, or external requests.
// The loopback fixture and browser are owned here and released in finally.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { readFile, readdir } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { build } from 'esbuild';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || 'playwright');
const baseline = process.env.VRCFORGE_PROVIDER_TOUR_BASELINE === '1';
const plugins = baseline ? [{name:'baseline-tour',setup(builder){builder.onLoad({filter:/provider-setup-tour\.tsx$/},()=>({contents:execFileSync('git',['show','HEAD:src/components/onboarding/provider-setup-tour.tsx'],{encoding:'utf8'}),loader:'tsx',resolveDir:path.resolve('src/components/onboarding')}))}}] : [];
const result = await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import React,{StrictMode,useState} from 'react';import{createRoot}from'react-dom/client';
import i18n from'i18next';import{initReactI18next}from'react-i18next';import en from'./src/locales/en-US.json';
import{ProviderSetupTour}from'./src/components/onboarding/provider-setup-tour';
import{ChatGptOAuthSettings}from'./src/features/chatgpt-oauth/settings';
import{ProviderSetup}from'./src/components/settings/provider-settings';
i18n.use(initReactI18next).init({lng:'en-US',resources:{'en-US':{translation:en}}});
function Fixture(){const[provider,setProvider]=useState('chatgpt');const[picker,setPicker]=useState(false);const[key,setKey]=useState('');const[model,setModel]=useState('gpt-6.1-sol');const[open,setOpen]=useState(false);const[mounted,setMounted]=useState(true);const[returns,setReturns]=useState(0);
window.fixture={open:()=>setOpen(true),unmount:()=>setMounted(false),mutate:()=>document.querySelector('[data-chatgpt-oauth-panel]').append(document.createElement('span'))};
return <><output id="returns">{returns}</output><div data-vrcforge-settings-scroll style={{height:380,overflowY:'auto',maxWidth:680,margin:'70px auto',padding:24}}><div data-onboarding-provider={provider==='chatgpt'?'connection':undefined}>
<ChatGptOAuthSettings endpoint="http://127.0.0.1:1" runtimeConnected={false} apiProvider={provider} apiModel={model} savingApiConfig={false} testingProvider="" providerTestMessage="" providerTestPassed={false} developerOptionsEnabled={false} onTestProvider={()=>{}} onProviderConfigChanged={()=>{}}/>
{provider==='chatgpt'&&!picker?<button data-testid="choose-api" type="button" onClick={()=>setPicker(true)}>Choose another API provider</button>:<ProviderSetup provider={provider} providerPickerOnly={provider==='chatgpt'} apiKey={key} baseUrl="" model={model} apiType="auto" contextWindow="" thinkingLevel="" reasoningVariants={[]} saving={false} models={[]} loadingModels={false} modelsError="" testingProvider="" providerTestMessage="" runtimeConnected={true} keySaved={false} onLoadModels={()=>{}} onTestProvider={()=>{}} onProviderChange={(v)=>{setProvider(v);setPicker(v==='chatgpt')}} onApiKeyChange={setKey} onBaseUrlChange={()=>{}} onModelChange={setModel} onProtocolAutoNegotiationChange={()=>{}} onApiTypeChange={()=>{}} onContextWindowChange={()=>{}} onThinkingLevelChange={()=>{}} onSubmit={()=>{}}/>}
</div></div>{mounted&&<ProviderSetupTour open={open} onReturn={()=>{setReturns(v=>v+1);setOpen(false)}}/>}</>}
createRoot(document.getElementById('root')).render(<StrictMode><Fixture/></StrictMode>);`},bundle:true,write:false,format:'iife',jsx:'automatic',loader:{'.css':'empty'},plugins,define:{'process.env.NODE_ENV':'"development"'}});
const cssName=(await readdir('dist/assets')).find(name=>name.endsWith('.css'));
const css=await readFile(path.join('dist/assets',cssName),'utf8');
const server=createServer((request,response)=>{if(request.url==='/fixture.js'){response.setHeader('Content-Type','text/javascript');response.end(result.outputFiles[0].text);}else{response.setHeader('Content-Type','text/html');response.end(`<style>${css}</style><div id="root"></div><script src="/fixture.js"></script>`);}});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
let browser;
try{
 browser=await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL||'msedge'});
 const page=await browser.newPage({viewport:{width:1100,height:780},reducedMotion:'reduce'});
 page.setDefaultTimeout(12000);
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.route('http://127.0.0.1:1/**',route=>route.abort());
 await page.goto(`http://127.0.0.1:${server.address().port}/`);
 await page.waitForSelector('[data-chatgpt-oauth-panel]');
 await page.evaluate(()=>window.fixture.open());
 await page.waitForSelector('.driver-active-element');
 assert.equal(await page.locator('.driver-active-element [data-chatgpt-oauth-panel]').count(),1,'OAuth card must be inside the active connection spotlight');
 await page.getByTestId('choose-api').click();
 await page.locator('[data-onboarding-provider="connection"]').last().locator('select').selectOption('openai');
 await page.waitForFunction(()=>document.querySelector('[data-onboarding-provider="connection"]')?.classList.contains('driver-active-element'));
 assert.equal(await page.locator('[data-onboarding-provider="connection"]').count(),1,'API form must have one current connection anchor');
 await page.locator('.driver-popover-next-btn').click();
 await page.waitForFunction(()=>document.querySelector('[data-onboarding-provider="credentials"]')?.classList.contains('driver-active-element'));
 const input=page.locator('[data-onboarding-provider="credentials"] input');
 await input.fill('fixture-only-value');
 await page.evaluate(()=>window.fixture.mutate());
 await page.waitForTimeout(60);
 assert.equal(await input.inputValue(),'fixture-only-value');
 assert.equal(await input.evaluate(element=>document.activeElement===element),true,'OAuth updates must preserve credential focus');
 const oldPath=await page.locator('.driver-overlay').innerHTML();
 await page.setViewportSize({width:780,height:640});
 await page.waitForFunction(before=>document.querySelector('.driver-overlay')?.innerHTML!==before,oldPath);
 await input.fill('after-resize');assert.equal(await input.inputValue(),'after-resize');
 await page.keyboard.press('Escape');await page.waitForSelector('.driver-overlay',{state:'detached'});
 assert.equal(await page.locator('#returns').innerText(),'1');
 await page.evaluate(()=>window.fixture.open());await page.waitForSelector('.driver-overlay');
 await page.evaluate(()=>window.fixture.unmount());await page.waitForSelector('.driver-overlay',{state:'detached'});
 assert.equal(await page.locator('#returns').innerText(),'1');
 assert.deepEqual(errors,[]);
 console.log('PASS real OAuth/ProviderSetup: OAuth spotlight, provider switch, credential click/value/focus, resize, Escape and cleanup');
}finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
