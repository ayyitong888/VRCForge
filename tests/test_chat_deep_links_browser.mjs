import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { createServer } from 'node:http';
import { chromium } from 'playwright';

// Keep the actual Tauri deep-link guest bindings: only the native IPC/event
// transport is replaced, so cold-start and running-instance APIs are exercised.
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: 'tsx', contents: `
    import React, {useState} from 'react';
    import {createRoot} from 'react-dom/client';
    import {useChatDeepLinks, chatIdFromDeepLink} from './src/hooks/use-chat-deep-links';
    window.parseLink = chatIdFromDeepLink;
    window.fixture = {current:['vrcforge://chat/cold'], opens:[], reads:0, cleanups:0};
    const chats = ['cold', 'running', 'racing', 'chat space'];
    function Harness() {
      const [ready, setReady] = useState(false);
      window.ready = setReady;
      useChatDeepLinks(ready, id => {if(chats.includes(id)) window.fixture.opens.push(id);});
      return <div id="mounted"/>;
    }
    const root = createRoot(document.getElementById('root'));
    window.unmount = () => root.unmount();
    root.render(<Harness/>);
  ` },
  bundle: true, write: false, format: 'iife', platform: 'browser', jsx: 'automatic',
  plugins: [{ name: 'native-transport', setup(b) {
    b.onResolve({ filter: /api\/http$/ }, a => a.importer.endsWith('use-chat-deep-links.ts') ? { path:'desktop', namespace:'fixture' } : undefined);
    b.onResolve({ filter: /^@tauri-apps\/api\/(core|event)$/ }, a => ({ path:a.path.endsWith('core')?'core':'event', namespace:'fixture' }));
    b.onLoad({ filter: /.*/, namespace:'fixture' }, a => ({ loader:'js', contents: a.path==='desktop'
      ? 'export const hasTauriInternals=()=>true;'
      : a.path==='core'
        ? `export const invoke=async command=>{if(command!=='plugin:deep-link|get_current')throw Error(command);window.fixture.reads++;return new Promise(resolve=>window.resolveCurrent=()=>resolve(window.fixture.current));};`
        : `export const listen=async(name,callback)=>{if(name!=='deep-link://new-url')throw Error(name);window.deliver=urls=>callback({payload:urls});return()=>{window.fixture.cleanups++;window.deliver=()=>{};};};`
    }));
  }}],
});
const server = createServer((_q,r) => {r.setHeader('Content-Type','text/html');r.end(`<div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`);});
let browser;
try {
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  browser = await chromium.launch({headless:true,channel:'msedge'});
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.locator('#mounted').waitFor({state:'attached'});
  assert.equal(await page.evaluate(()=>window.fixture.reads),0,'startup waits for chat hydration');
  await page.evaluate(()=>window.ready(true));
  await page.waitForFunction(()=>window.fixture.reads===1);
  await page.evaluate(()=>window.resolveCurrent());
  await page.waitForFunction(()=>window.fixture.opens.length===1);
  assert.deepEqual(await page.evaluate(()=>window.fixture.opens),['cold']);
  await page.evaluate(()=>window.deliver(['vrcforge://chat/running','vrcforge://chat/unknown','vrcforge://chat/chat%20space']));
  assert.deepEqual(await page.evaluate(()=>window.fixture.opens),['cold','running','chat space']);
  const invalid=['https://chat/cold','vrcforge://chat/cold?command=write','vrcforge://chat/cold#run','vrcforge://chat/a/b','vrcforge://chat/%2Fetc','vrcforge://chat/%5Cfile','vrcforge://chat/%00','vrcforge://chat/%zz','vrcforge://user@chat/cold','vrcforge://chat/..'];
  assert.deepEqual(await page.evaluate(values=>values.map(window.parseLink),invalid),invalid.map(()=>null));
  await page.evaluate(()=>window.ready(false));
  await page.waitForFunction(()=>window.fixture.cleanups===1);
  await page.evaluate(()=>{window.fixture.current=['vrcforge://chat/cold'];window.ready(true);});
  await page.waitForFunction(()=>window.fixture.reads===2);
  await page.evaluate(()=>{window.deliver(['vrcforge://chat/racing']);window.resolveCurrent();});
  await page.waitForFunction(()=>window.fixture.opens.at(-1)==='racing');
  assert.deepEqual(await page.evaluate(()=>window.fixture.opens),['cold','running','chat space','racing'],'stale startup read cannot replace a newly delivered link');
  await page.evaluate(()=>window.unmount());
  assert.equal(await page.evaluate(()=>window.fixture.cleanups),2);
  assert.deepEqual(errors,[]);
  console.log('PASS deep links: cold start, hydration, warm delivery, missing chat, URL validation, startup race, listener cleanup');
} finally {
  await browser?.close();
  await new Promise(resolve=>server.close(resolve));
}
