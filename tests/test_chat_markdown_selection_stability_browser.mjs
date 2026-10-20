import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {readFile,writeFile} from 'node:fs/promises';
import {dirname} from 'node:path';
import {build} from 'esbuild';

const {chromium}=createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH||'playwright');
const uri=`vrcforge://scene-capture-preview/8d886299-9be1-4baf-8e1f-e205f3a8e5fc?context=runtime%3Asession-fixture&revision=1&store=${'1'.repeat(32)}`;
const text=`Stable **selection** remains attached while resource context objects change. Drag this answer without losing the selected text.\n\n![Preview](${uri})`;
const bundle=await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import React,{useEffect,useState} from 'react';import {createRoot} from 'react-dom/client';
import {ChatMarkdown,ChatResourceImageContext} from './src/components/chat/chat-markdown';
import {initializeI18n} from './src/i18n';
function Harness(){const [tick,setTick]=useState(0);const [turn,setTurn]=useState('turn-first');const [enabled,setEnabled]=useState(true);useEffect(()=>{const timer=setInterval(()=>setTick(n=>n+1),50);return()=>clearInterval(timer)},[]);window.__changeTurn=setTurn;window.__setEnabled=setEnabled;window.__renders=tick;return <ChatResourceImageContext.Provider value={enabled?{endpoint:location.origin,chatId:'chat-fixture',projectPath:'D:/fixture-project',sessionId:'session-fixture',clientTurnId:turn}:null}><ChatMarkdown text={${JSON.stringify(text)}}/></ChatResourceImageContext.Provider>;}
void initializeI18n().then(()=>createRoot(document.getElementById('root')).render(<Harness/>));
`},bundle:true,write:false,format:'iife',platform:'browser',jsx:'automatic',plugins:process.env.VRCFORGE_MARKDOWN_BEFORE?[{name:'original-markdown',setup(api){api.onLoad({filter:/chat-markdown\.tsx$/},async args=>({contents:await readFile(process.env.VRCFORGE_MARKDOWN_BEFORE,'utf8'),resolveDir:dirname(args.path),loader:'tsx'}));}}]:[]});
const server=createServer((_req,res)=>{res.setHeader('Content-Type','text/html');res.end(`<style>body{font:20px sans-serif;margin:24px}#root{width:620px;user-select:text}</style><div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`)});
let browser;
try{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage();
 const calls=[];const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/app/agent/resource-image',async route=>{calls.push(route.request().postDataJSON());await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({uri,mimeType:'image/png',sha256:'a'.repeat(64),pngBase64:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jQWQAAAAASUVORK5CYII='})});});
 await page.goto(`http://127.0.0.1:${server.address().port}`);await page.locator('img').waitFor();
 await page.evaluate(()=>{const p=document.querySelector('.chat-markdown p');window.__originalText=p.firstChild;window.__originalStrong=p.querySelector('strong').firstChild;});
 const box=await page.locator('.chat-markdown p').first().boundingBox();
 await page.mouse.move(box.x+3,box.y+12);await page.mouse.down();
 const samples=[];
 for(let i=1;i<=30;i++){await page.mouse.move(box.x+Math.min(box.width-4,i*18),box.y+12);await page.waitForTimeout(100);samples.push(await page.evaluate(()=>({connected:window.__originalText.isConnected,strongConnected:window.__originalStrong.isConnected,selection:window.getSelection()?.toString()||'',renders:window.__renders})));}
 await page.mouse.up();
 const selected=await page.evaluate(()=>window.getSelection()?.toString()||'');
 const report={samples:30,disconnectedSamples:samples.filter(s=>!s.connected||!s.strongConnected).length,selectedTextLength:selected.length,rerenders:samples.at(-1).renders,errors};
 if(process.env.VRCFORGE_MARKDOWN_STABILITY_REPORT)await writeFile(process.env.VRCFORGE_MARKDOWN_STABILITY_REPORT,JSON.stringify(report,null,2));
 assert.equal(report.disconnectedSamples,0,'real pointer drag must retain its original text and strong DOM nodes through fresh equal-field context values');
 assert.ok(selected.length>20&&selected.includes('selection'),'real mouseup retains the selected answer text');
 assert.equal(calls.length,1,'equal-field context rerenders do not refetch or remount the authenticated image');
 await page.evaluate(()=>window.__changeTurn('turn-second'));await page.waitForFunction(()=>window.__renders>65);
 await page.waitForFunction(()=>document.querySelector('img')?.getAttribute('src')?.startsWith('data:image/png;base64,'));
 assert.equal(calls.length,2,'a real authorization-context field change must fetch again');assert.equal(calls[1].clientTurnId,'turn-second');
 assert.equal(await page.evaluate(()=>window.__originalText.isConnected&&window.__originalStrong.isConnected),true,'genuine resource authorization changes also preserve answer text');
 await page.evaluate(()=>window.__setEnabled(false));await page.waitForFunction(()=>!document.querySelector('img'));
 assert.equal(calls.length,2,'missing resource context never navigates to an internal Resource URI');
 assert.deepEqual(errors,[]);
 console.log('markdown pointer selection stability and dynamic authenticated image context: passed');
}finally{await browser?.close();await new Promise(resolve=>server.close(resolve));}
