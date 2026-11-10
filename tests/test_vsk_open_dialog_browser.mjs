import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.VRCFORGE_PLAYWRIGHT_PATH||'playwright');
const bundle=await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import React from 'react';import {createRoot} from 'react-dom/client';
import i18n from 'i18next';import {initReactI18next} from 'react-i18next';
import zh from './src/locales/zh-CN.json';import en from './src/locales/en-US.json';
import tw from './src/locales/zh-TW.json';import ja from './src/locales/ja-JP.json';
import {VskOpenDialog} from './src/components/skills/vsk-open-dialog';
window.__TAURI_INTERNALS__={};window.importCalls=0;window.preflightCalls=0;window.manageCalls=0;
const language=new URLSearchParams(location.search).get('lang')||'zh';
i18n.use(initReactI18next).init({lng:language,resources:{zh:{translation:zh},en:{translation:en},tw:{translation:tw},ja:{translation:ja}},interpolation:{escapeValue:false}});
createRoot(document.getElementById('root')).render(<VskOpenDialog ready={true}
 onPreflight={async()=>{window.preflightCalls++;return {ok:true,preview:{manifest:{name:'Fixture Wardrobe',version:'1.1.10',author:'legacy-stable-identity',author_display_name:'ayyitong888',description:'Adds outfit switches to the avatar.'},signature_status:'signed',signer_fingerprint:'a'.repeat(64),permissions:['read_project','write_project_files'],governance:{signerTrustStatus:'trusted',official:true,officialPublisher:'ayyitong888'}}}}}
 onImport={()=>{window.importCalls++;return new Promise((resolve,reject)=>{window.finishImport=resolve;window.failImport=reject})}}
 onManage={()=>{window.manageCalls++}}/>);
`},plugins:[{name:'synthetic-tauri',setup(b){b.onResolve({filter:/^@tauri-apps\/api\/(core|event)$/},args=>({path:args.path,namespace:'fixture'}));b.onLoad({filter:/.*/,namespace:'fixture'},args=>({contents:args.path.endsWith('/core')?'export async function invoke(){return ["D:/Synthetic fixture/package.vsk"]}':'export async function listen(){return ()=>{}}',loader:'js'}))}}],bundle:true,write:false,format:'iife',platform:'browser'});
// Isolated synthetic fixture; no real import, auth, model request or user profile.
// Loopback server and headless browser belong to this test and close in finally.
const server=createServer((_req,res)=>{res.setHeader('Content-Type','text/html; charset=utf-8');res.end('<div id="root"></div><script>'+bundle.outputFiles[0].text.replaceAll('</script','<\\/script')+'</script>')});
let browser;
try{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 browser=await chromium.launch({channel:'msedge',headless:true});
 for(const lang of ['zh','en','tw','ja']){
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port+'/?lang='+lang);
  await page.getByText('Fixture Wardrobe',{exact:true}).waitFor();
  const dialog=page.getByRole('dialog');
  assert.equal(await page.evaluate(()=>window.importCalls),0);
  assert.equal(await page.locator('details[open]').count(),0);
  for (const code of await dialog.locator('code').all()) assert.equal(await code.isVisible(),false);
  const visible=await dialog.innerText();
  assert.ok(visible.includes('ayyitong888'));
  assert.ok(visible.includes('Adds outfit switches to the avatar.'));
  assert.ok(!visible.includes('legacy-stable-identity'));
  assert.ok(!visible.includes('read_project')&&!visible.includes('write_project_files'));
  assert.ok(!visible.includes('a'.repeat(64))&&!visible.includes('D:/Synthetic'));
  assert.ok(!visible.includes('vskOpen.'));
  assert.ok(!visible.match(/Requested permissions|请求的权限|要求的權限|要求される権限/));
  await dialog.locator('button').last().click();
  await page.waitForFunction(()=>window.importCalls===1);
  const progress=dialog.getByRole('progressbar');
  await progress.waitFor();
  assert.equal(await progress.getAttribute('value'),null);
  await page.keyboard.press('Escape');
  assert.equal(await dialog.count(),1,'import remains open while pending');
  if(lang==='ja'){
   await page.evaluate(()=>window.failImport(new Error('Import fixture failed')));
   await dialog.getByRole('alert').getByText('Import fixture failed').waitFor();
   assert.equal(await progress.count(),0);
   await dialog.locator('[data-vsk-cancel]').click();
  }else{
   await page.evaluate(()=>window.finishImport({ok:true}));
   await page.waitForFunction(()=>document.querySelectorAll('dialog button').length===2);
   const actions=dialog.getByRole('button');
   assert.equal(await actions.count(),2);
   assert.equal(await dialog.locator('details').count(),0);
   await actions.nth(lang==='en'?0:1).click();
   assert.equal(await page.evaluate(()=>window.manageCalls),lang==='en'?0:1);
  }
  assert.equal(await page.getByRole('dialog').count(),0);
  assert.deepEqual(errors,[]);await page.close();
 }
 console.log('PASS: four-language package summary, explicit import, progress, completion actions and error.');
}finally{await browser?.close();await new Promise(resolve=>server.close(resolve));}
