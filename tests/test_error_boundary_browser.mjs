import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';

const bundle=await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
import React from'react';import{createRoot}from'react-dom/client';import{RootErrorBoundary}from'./src/error-boundary';
function Bomb(){throw new Error('render exploded');}
createRoot(document.getElementById('root')).render(
  React.createElement(RootErrorBoundary,null,React.createElement(Bomb)),
);
`},bundle:true,write:false,format:'iife',platform:'browser',jsx:'automatic'});
const server=createServer((_q,r)=>{r.setHeader('Content-Type','text/html');r.end(`<div id='root'></div><script>${bundle.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`);});const{chromium}=createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH||'playwright');let browser;
try{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  browser=await chromium.launch({headless:true,channel:'msedge'});
  const page=await browser.newPage();
  const consoleErrors=[];
  page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text());});
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.waitForSelector('[data-error-boundary-reload]');
  assert.match(await page.textContent('main'),/VRCForge could not continue/,'crash renders a visible failure card instead of a blank window');
  assert.match(await page.textContent('main'),/render exploded/,'failure card carries the underlying error message');
  assert.ok(consoleErrors.some(text=>text.includes('render exploded')),'crash is logged for diagnosis');
  await page.click('[data-error-boundary-reload]');
  await page.waitForLoadState('load');
}catch(error){throw error;}
finally{
  await browser?.close();
  await new Promise(r=>server.close(r));
}
console.log('PASS root error boundary keeps crashes visible');
