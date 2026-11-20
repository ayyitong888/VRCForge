// Real sidebar + settings workspace. Loopback fixture data only; no live backend.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { readFile, mkdir } from 'node:fs/promises';
import { build } from 'esbuild';
import postcss from 'postcss';
import tailwindcss from 'tailwindcss';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || 'playwright');
const workspaceSource = await readFile('src/components/settings/settings-workspace.tsx', 'utf8');
const callbackNames = [...workspaceSource.matchAll(/^  (on\w+):/gm)].map(match => match[1]);
const bundle = await build({ stdin: { resolveDir: process.cwd(), loader: 'tsx', contents: `
import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from './src/locales/en-US.json';
import zh from './src/locales/zh-CN.json';
import tw from './src/locales/zh-TW.json';
import ja from './src/locales/ja-JP.json';
import { AppSidebar } from './src/components/sidebar/app-sidebar';
import { SettingsWorkspace } from './src/components/settings/settings-workspace';
import { DEFAULT_THEME_CUSTOMIZATION } from './src/lib/theme-customization';
import { SETTINGS_SEARCH_ENTRIES, searchSettings } from './src/lib/settings-search';
i18n.use(initReactI18next).init({lng:'en-US', resources:{'en-US':{translation:en},'zh-CN':{translation:zh},'zh-TW':{translation:tw},'ja-JP':{translation:ja}},interpolation:{escapeValue:false}});
window.mutations = 0;
const callbacks = Object.fromEntries(${JSON.stringify(callbackNames)}.map(key => [key, () => { window.mutations++; }]));
const props = {
 ...callbacks, endpoint:location.origin, developerOptionsEverEnabled:false, computerUseEnabled:false, computerUseEverEnabled:false,
 backgroundGoalNotificationsEnabled:false, savingAdvancedSettings:false, permission:null, loading:false, runtimeConnected:false,
 currentLanguage:'en-US', runningEnterAction:'queue', themeCustomization:DEFAULT_THEME_CUSTOMIZATION,
 apiKey:'fixture-secret-value',apiBaseUrl:'',apiModel:'',apiType:'openai',apiContextWindow:'',
 apiThinkingLevel:'',reasoningVariants:null,apiKeySaved:false,savingApiConfig:false,modelOptions:[],loadingModels:false,modelsError:'',testingProvider:'',providerTestMessage:'',providerTestPassed:false,
 visionProvider:'openai',visionApiKey:'',visionBaseUrl:'',visionModel:'',visionEnabled:false,savingVisionConfig:false,
 diagnosticsStatus:null,diagnosticsMessage:'',loadingDiagnostics:false,exportingSupportBundle:false,connectorStatus:null,loadingConnectors:false,connectorMessage:'',selectedProjectPath:'',isDesktop:false,
 checkpointArchiveLimitInput:'0',checkpointArchiveCountInput:'4',agentNotes:'fixture-private-notes',agentNotesLoaded:true,agentNotesPath:'',notesMessage:'',savingNotes:false,compactDebugEntries:[],
};
function Fixture() {
 const [section,setSection] = useState('general'); const [selection,setSelection] = useState(null); const [developer,setDeveloper] = useState(false);
 const [collapsed, setCollapsed] = useState(false);
 const [apiProvider,setApiProvider] = useState('chatgpt');
 const [running,setRunning] = useState(false);
 const [model,setModel] = useState("a-model");
 const [visionEnabled,setVisionEnabled] = useState(false);
 const select = entry => {setSection(entry.section);setSelection(previous=>({entry,requestId:(previous?.requestId||0)+1}));};
 window.fixture = {select, setModel, setRunning, setDeveloper, setCollapsed, apiProvider, setApiProvider, visionEnabled, setVisionEnabled, language:code=>i18n.changeLanguage(code), entries:SETTINGS_SEARCH_ENTRIES,
  search:query=>searchSettings(query,key=>i18n.t(key),developer), label:key=>i18n.t(key)};
 return <div style={{display:'grid',gridTemplateColumns:(collapsed?'56px':'260px')+' minmax(0,1fr)',height:'100vh'}}>
 <AppSidebar collapsed={collapsed} activeView='settings' activeSettingsSection={section} developerOptionsEnabled={developer}
  projectItems={[]} chatSidebar={{temporaryChats:[],testChats:[],projectChatsByPath:new Map()}}
  onOpenSettingsSection={next=>{setSelection(null);setSection(next);}} onSelectSettingsSearchResult={select} onBackFromSettings={()=>{}} />
 <SettingsWorkspace {...props} apiConnectionSaved={apiProvider === 'openai'} runtimeConnected={running} providerConfigurationLocked={running}
  modelOptions={[{id:'a-model'},{id:'b-model'}]} apiModel={model} apiThinkingLevel="high"
  reasoningVariants={{variants:[{key:'high',displayKey:'provider.reasoning_high'},{key:'low',displayKey:'provider.reasoning_low'}]}}
  onApiModelChange={model=>{window.pickedModel=model;setModel(model);}} onApiThinkingLevelChange={effort=>{window.pickedEffort=effort;}}
  onSaveApiProvider={e=>{e.preventDefault();window.savedSelection=true;}} apiProvider={apiProvider} visionEnabled={visionEnabled} activeSection={section} developerOptionsEnabled={developer} searchSelection={selection}/>
 </div>;
}
createRoot(document.getElementById('root')).render(<Fixture/>);
`}, bundle:true,write:false,format:'esm',jsx:'automatic',define:{'process.env.NODE_ENV':'"test"'}});
const css = await postcss([tailwindcss]).process('@tailwind base;\n@tailwind components;\n@tailwind utilities;', {from:'src/styles.css'});
const appStyles = (await readFile('src/theme.css','utf8')) + (await readFile('src/styles/app-select.css','utf8'));
const writes=[];
const server=createServer((req,res)=>{
 if(req.method !== 'GET') writes.push(req.url);
 if(req.url==='/fixture.js'){res.setHeader('Content-Type','text/javascript');res.end(bundle.outputFiles[0].text);}
 else if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end('<!doctype html><style>'+appStyles+'\n'+css.css+'</style><div id="root"></div><script type="module" src="/fixture.js"></script>');}
 else if(req.url==='/api/chatgpt/status'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({accounts:[{id:'fixture',label:'Fixture',connected:true}],activeAccountId:'fixture',activeModel:'a-model',activeThinkingLevel:'high'}));}
 else if(req.url==='/api/chatgpt/models'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({models:['a-model','b-model'].map(id=>({id,name:id,supportedReasoningLevels:[{effort:'low'},{effort:'high'}]}))}));}
 else if(req.url==='/api/chatgpt/activate'){res.setHeader('Content-Type','application/json');res.end('{"ok":true}');}
 else if(req.url?.includes('telegram')){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({configured:false,enabled:false,state:'disabled',ownerId:0,projects:[],proxyMode:'environment'}));}
 else if(req.url?.includes('data-root')){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({path:'/fixture',writable:true,inProgramFiles:false,migrated:false}));}
 else {res.statusCode=404;res.end('{}');}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
let browser;
try {
 browser=await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL||'msedge'});
 const page=await browser.newPage({viewport:{width:1280,height:900},deviceScaleFactor:1.5}); const errors=[];
 page.on('pageerror',e=>{errors.push(e.message);console.error(e.message);});
 await page.goto('http://127.0.0.1:'+server.address().port);
 const input=page.getByRole('searchbox'); await input.waitFor();
 const normalNav=page.locator('aside').getByRole('button',{name:'General',exact:true});
 assert.ok(await normalNav.isVisible());
 const preferences=page.getByRole('navigation',{name:'Preferences'});
 const agent=page.getByRole('navigation',{name:'Agent',exact:true});
 const application=page.getByRole('navigation',{name:'Application'});
 await preferences.getByRole('button',{name:'Chat',exact:true}).click();
 assert.equal(await page.getByRole('heading',{name:'Chat',exact:true}).count(),1);
 assert.equal(await page.locator('[data-settings-panel="enter"]').count(),1);
 await preferences.getByRole('button',{name:'Notifications',exact:true}).click();
 assert.equal(await page.locator('[data-settings-panel="sounds"]').count(),1);
 await application.getByRole('button',{name:'About & Support',exact:true}).click();
 assert.equal(await page.locator('[data-settings-panel="diagnostics"]').count(),1);
 assert.equal(await page.locator('[data-settings-panel="developer-toggle"]').count(),1);
 await agent.getByRole('button',{name:'Connectors',exact:true}).click();
 const targetAgent=page.getByRole('combobox',{name:'Target Agent'});
 const install=page.getByRole('button',{name:'Install MCP config',exact:true});
 const targetBox=await targetAgent.boundingBox();
 const installBox=await install.boundingBox();
 assert.equal(targetBox.y,installBox.y,'connector select and install control share a top edge');
 assert.equal(targetBox.height,installBox.height,'connector select and install control share a height');
 assert.equal(await targetAgent.evaluate(element=>getComputedStyle(element).alignItems),'center');
 await targetAgent.press('Space');
 await page.getByRole('option',{name:'Codex App',exact:true}).waitFor();
 await page.keyboard.press('ArrowDown');
 await page.keyboard.press('Enter');
 assert.equal(await targetAgent.inputValue(),'codexApp');
 await targetAgent.selectOption('deepseekHarness');
 if(process.env.VRCFORGE_SCREENSHOT_DIR){
  await mkdir(process.env.VRCFORGE_SCREENSHOT_DIR,{recursive:true});
  await page.screenshot({path:process.env.VRCFORGE_SCREENSHOT_DIR+'/settings-connectors-wide.png'});
 }
 await page.setViewportSize({width:580,height:820});
 assert.ok((await install.boundingBox()).y>(await targetAgent.boundingBox()).y,'install wraps under the select in narrow content');
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 if(process.env.VRCFORGE_SCREENSHOT_DIR) await page.screenshot({path:process.env.VRCFORGE_SCREENSHOT_DIR+'/settings-connectors-narrow.png'});
 await page.evaluate(()=>window.fixture.setCollapsed(true));
 await preferences.getByRole('button',{name:'General',exact:true}).click();
 assert.equal(await preferences.getByRole('button',{name:'General',exact:true}).getAttribute('aria-current'),'page');
 await page.evaluate(()=>window.fixture.setCollapsed(false));
 await page.setViewportSize({width:1280,height:900});
 // A help-text term, without a matching section name, must locate the actual field.
 await input.fill('oldest eligible');
 const countResult=page.locator('[data-settings-search-result="checkpoints:settings.checkpointArchiveCountLimit"]');
 await countResult.waitFor(); await countResult.click();
 await page.waitForFunction(()=>document.activeElement?.hasAttribute('data-settings-search-match'));
 assert.equal(await page.evaluate(()=>document.activeElement?.closest('[data-settings-panel]')?.getAttribute('data-settings-panel')),'checkpoints');
 assert.ok(await page.evaluate(()=>{const e=document.activeElement;const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;}));
 // Whitespace, empty state, clear and keyboard round trip.
 await input.fill('unmatched-qzx-813'); assert.match(await page.getByRole('status').innerText(),/No matching/);
 await input.press('Escape'); assert.ok(await normalNav.isVisible());
 await input.fill('   '); assert.ok(await normalNav.isVisible());
 await input.fill('language'); await input.press('ArrowDown');
 assert.ok(await page.evaluate(()=>document.activeElement?.hasAttribute('data-settings-search-result')));
 await page.keyboard.press('ArrowUp'); assert.equal(await input.evaluate(e=>e===document.activeElement),true);
 await input.press('Enter'); await page.waitForFunction(()=>document.activeElement?.closest('[data-settings-panel]')?.getAttribute('data-settings-panel')==='language');
 await page.getByRole('button',{name:'Clear search',exact:true}).click(); assert.ok(await normalNav.isVisible());
 // Local values never participate in the index; hidden developer entries cannot leak.
 assert.deepEqual(await page.evaluate(()=>window.fixture.search('fixture-secret-value')),[]);
 assert.deepEqual(await page.evaluate(()=>window.fixture.search('fixture-private-notes')),[]);
 for(const query of ['settings.computerUse','settings.logIdentityMapping','settings.compactDebugTitle']) {
  await input.fill(await page.evaluate(key=>window.fixture.label(key),query));
  assert.equal(await page.locator('[data-settings-search-result^="computer-use:"], [data-settings-search-result^="identity:"], [data-settings-search-result^="compaction:"]').count(),0);
 }
 await page.evaluate(()=>window.fixture.setDeveloper(true));
 await input.fill(await page.evaluate(()=>window.fixture.label('settings.computerUse')));
 await page.locator('[data-settings-search-result="computer-use:settings.computerUse"]').click();
 await page.waitForFunction(()=>document.activeElement?.closest('[data-settings-panel]')?.getAttribute('data-settings-panel')==='computer-use');
 await page.evaluate(()=>window.fixture.setDeveloper(false));
 await page.locator('[data-settings-search-result^="computer-use:"]').waitFor({state:'hidden'});
 assert.equal(await page.locator('[data-settings-search-result^="computer-use:"]').count(),0);
 // Search switches presentation tabs, without selecting an account or changing provider configuration.
 async function selectResult(id) {
  await input.focus();
  await page.evaluate(id=>window.fixture.select(window.fixture.entries.find(entry=>entry.id===id)),id);
  await page.waitForFunction(id=>document.activeElement?.getAttribute('data-settings-search-match')===id,id);
 }
 await selectResult('api:provider.apiKey');
 assert.equal(await page.locator('#api-model-settings').isVisible(),true);
 assert.equal(await page.locator('#subscription-model-settings').isVisible(),false);
 assert.equal(await page.evaluate(()=>window.fixture.apiProvider),'chatgpt');
 await selectResult('account:chatgptOAuth.model');
 assert.equal(await page.locator('#subscription-model-settings').isVisible(),true);
 assert.equal(await page.locator('#api-model-settings').isVisible(),false);
 await page.locator('[aria-controls="api-model-settings"]').click();
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 assert.equal(await page.locator('#api-model-settings').isVisible(),true,'manual tabs remain usable after searching');
 await page.evaluate(()=>window.fixture.setApiProvider('openai'));
 await page.waitForFunction(()=>window.fixture.apiProvider==='openai');
 await selectResult('api:provider.apiKey');
 assert.equal(await page.evaluate(()=>document.activeElement?.textContent),await page.evaluate(()=>window.fixture.label('provider.apiKey')));
 await selectResult('vision:provider.apiKey');
 assert.equal(await page.locator('[data-settings-panel="vision"]').evaluate(element=>element.open),true,'vision search opens its disclosure');
 assert.equal(await page.evaluate(()=>window.fixture.visionEnabled),false,'search does not enable the vision profile');
 assert.equal(await page.evaluate(()=>document.activeElement?.getAttribute('data-settings-panel')),'vision','an unavailable vision field lands on its visible disclosure');
 await page.evaluate(()=>window.fixture.setVisionEnabled(true));
 await page.waitForFunction(()=>window.fixture.visionEnabled);
 await selectResult('vision:provider.apiKey');
 assert.equal(await page.evaluate(()=>document.activeElement?.textContent),await page.evaluate(()=>window.fixture.label('provider.apiKey')));
 await selectResult('startup-animation:settings.startupAnimation');
 assert.equal(await page.locator('#settings-startup-animation-label').evaluate(element=>element===document.activeElement),true);
 // Every authored result has a visible landing panel in real settings; conditional controls land on their owning panel.
 const entries=await page.evaluate(()=>window.fixture.entries.filter(e=>!e.developerOnly));
 for(const entry of entries){
  await page.evaluate(entry=>window.fixture.select(entry),entry);
  await page.waitForFunction(entry=>{const e=document.activeElement;return e?.getAttribute('data-settings-search-match')===entry.id&&(e.getAttribute('data-settings-panel')===entry.panel||e.closest('[data-settings-panel]')?.getAttribute('data-settings-panel')===entry.panel)&&e.getClientRects().length>0;},entry,{timeout:3000}).catch(error=>{throw new Error(entry.id+': '+error.message);});
 }
 // Field-level Telegram navigation opens the closed disclosure without connecting.
 await input.fill('Bot token'); await page.locator('[data-settings-search-result="telegram:settings.telegramBotToken"]').click();
 await page.waitForFunction(()=>document.activeElement?.textContent==='Bot token');
 assert.equal(await page.locator('[data-settings-panel="telegram"]').evaluate(e=>e.open),true);
 // All four locale resources work in the same real sidebar.
 for(const locale of ['zh-CN','zh-TW','ja-JP','en-US']){
  await page.evaluate(code=>window.fixture.language(code),locale);
  await input.fill('');
  for(const key of ['settings.navPersonal','settings.navAgent','settings.navApplication']){
   const groupLabel=await page.evaluate(key=>window.fixture.label(key),key);
   assert.notEqual(groupLabel,key);
   assert.ok(await page.getByRole('navigation',{name:groupLabel,exact:true}).isVisible());
  }
  const label=await page.evaluate(()=>window.fixture.label('settings.language'));
  await input.fill(label); assert.equal(await page.locator('[data-settings-search-result="language:settings.language"]').count(),1);
 }
 await page.evaluate(()=>window.fixture.language('zh-CN')); await input.fill('');
 if(process.env.VRCFORGE_SCREENSHOT_DIR){
  await mkdir(process.env.VRCFORGE_SCREENSHOT_DIR,{recursive:true});
  await page.screenshot({path:process.env.VRCFORGE_SCREENSHOT_DIR+'/settings-search-idle.png'});
  await input.fill('记忆');
  await page.locator('[data-settings-search-result="memory:settings.crossSessionMemory"]').click();
  await page.waitForFunction(()=>document.activeElement?.getAttribute('data-settings-search-match')==='memory:settings.crossSessionMemory');
  await page.screenshot({path:process.env.VRCFORGE_SCREENSHOT_DIR+'/settings-search-results.png'});
 }
 await page.setViewportSize({width:900,height:800});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.equal(await page.evaluate(()=>window.mutations),0,'search must not activate a setting or command');
 assert.deepEqual(writes,[],'search must not send a mutating request');
 // A live turn keeps credential edits locked and allows the next model selection in both provider panels.
 await page.evaluate(()=>{window.fixture.setRunning(true);window.fixture.setApiProvider('chatgpt');window.fixture.select(window.fixture.entries.find(e=>e.id==='account:chatgptOAuth.model'));});
 const model=page.locator('[data-chatgpt-model-select]');
 await model.locator('option[value="b-model"]').waitFor({state:'attached'});
 await model.selectOption('b-model');
 await page.locator('[data-chatgpt-reasoning-select]').selectOption('high');
 const activate=page.getByRole('button',{name:await page.evaluate(()=>window.fixture.label('chatgptOAuth.activate')),exact:true});
 assert.ok(await activate.isEnabled());
 assert.ok(await page.getByRole('button',{name:await page.evaluate(()=>window.fixture.label('chatgptOAuth.logout')),exact:true}).isDisabled());
 const submitted=page.waitForRequest(req=>req.url().endsWith('/api/chatgpt/activate'));
 await activate.click();
 const activated=await submitted;
 assert.equal(activated.postDataJSON().model,'b-model');
 assert.equal(activated.postDataJSON().thinking_level,'high');
 await page.evaluate(()=>{window.fixture.setApiProvider('openai');window.fixture.select(window.fixture.entries.find(e=>e.id==='api:provider.model'));});
 const api=page.locator('[data-settings-panel="api"]');
 await api.locator('[data-onboarding-provider="model"] select').selectOption('b-model');
 await api.locator('select').filter({has:page.locator('option[value="low"]')}).selectOption('low');
 assert.ok(await api.locator('input[type=password]').isDisabled());
 assert.ok(await api.locator('[data-onboarding-provider="endpoint"] input').isDisabled());
 await api.locator('button[type="submit"]').click();
 assert.deepEqual(await page.evaluate(()=>[window.pickedModel,window.pickedEffort,window.savedSelection]),['b-model','low',true]);
 await page.evaluate(()=>{window.fixture.setApiProvider('custom');window.fixture.setModel('');window.fixture.select(window.fixture.entries.find(e=>e.id==='api:provider.model'));});
 assert.ok(await api.locator('input[type=password]').isEnabled());
 assert.ok(await api.locator('[data-onboarding-provider="endpoint"] input').isEnabled());
 assert.ok(await api.locator('button[type="submit"]').isDisabled());
 await api.getByText(await page.evaluate(()=>window.fixture.label('provider.chooseModelToSave')),{exact:true}).waitFor();
 await api.locator('[data-onboarding-provider="model"] select').selectOption('a-model');
 assert.ok(await api.locator('button[type="submit"]').isEnabled());
 assert.deepEqual(errors,[]);
 console.log('PASS real sidebar/settings: '+entries.length+' ordinary landing targets, subscription/API tabs, vision disclosure, startup animation, developer visibility, label/help search, keyboard, clear, no result, 4 locales, Telegram disclosure, no mutations');
} finally {await browser?.close();await new Promise(resolve=>server.close(resolve));}
