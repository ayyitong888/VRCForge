import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { build } from 'esbuild';

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || 'playwright');
const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: 'tsx',
    contents: `
      import React, {useState} from 'react';
      import {createRoot} from 'react-dom/client';
      import i18n from 'i18next';
      import {initReactI18next} from 'react-i18next';
      import {useChatSessions} from './src/hooks/use-chat-sessions';
      await i18n.use(initReactI18next).init({lng:'en', resources:{en:{translation:{}}}});
      const noop=()=>{};
      function Fixture(){
        const [paths,setPaths]=useState(location.search.includes('retry=1') ? ['FAIL'] : ['A']);
        const [endpoint,setEndpoint]=useState(location.origin);
        const initial={chats:[],activeChatId:''};
        const sessions=useChatSessions({endpoint,activeView:'chat',runtimeConnected:true,
          projectPrefsReady:true,projectPaths:paths,customProjectPaths:[],activeProjectPath:'',setActiveProjectPath:noop,
          setActiveView:noop,setError:noop,expandProjectGroup:noop,initialChatState:initial});
        window.testApi={sessions, paths, bumpSame:()=>setPaths((current)=>[...current]), switchPath:(path)=>setPaths([path]), switchEndpoint:(suffix)=>setEndpoint(location.origin + suffix)};
        return <pre>{JSON.stringify({count:sessions.chats.length,title:sessions.chats[0]?.title || ''})}</pre>;
      }
      createRoot(document.getElementById('root')).render(<React.StrictMode><Fixture/></React.StrictMode>);
    `,
  },
  bundle: true,
  write: false,
  format: 'esm',
  jsx: 'automatic',
  define: { 'process.env.NODE_ENV': '"test"' },
});

const chats = {
  A: {id:'chat-a',sessionId:'session-a',title:'server A',projectPath:'A',projectType:'general',revision:0,createdAt:'2026-01-01T00:00:00Z',updatedAt:'2026-01-01T00:00:00Z',items:[{id:'a1',type:'user',text:'A'}]},
  B: {id:'chat-b',sessionId:'session-b',title:'server B',projectPath:'B',projectType:'general',revision:0,createdAt:'2026-01-01T00:00:00Z',updatedAt:'2026-01-01T00:00:00Z',items:[{id:'b1',type:'user',text:'B'}]},
  FAIL: {id:'chat-fail',sessionId:'session-fail',title:'server retry',projectPath:'FAIL',projectType:'general',revision:0,createdAt:'2026-01-01T00:00:00Z',updatedAt:'2026-01-01T00:00:00Z',items:[{id:'f1',type:'user',text:'retry'}]},
};
const requestCounts = new Map();
const endpointRequestCounts = new Map();
let failOnce = true;
let releaseA = false;
const server = createServer(async (req, res) => {
  if (!req.url.includes('/api/app/chats')) {
    if (req.url === '/fixture.js') {
      res.setHeader('Content-Type', 'text/javascript');
      res.end(bundle.outputFiles[0].text);
      return;
    }
    res.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
    return;
  }
  const url = new URL(req.url, 'http://127.0.0.1');
  const endpointKey = url.pathname.startsWith('/alt/') ? 'alt' : 'base';
  endpointRequestCounts.set(endpointKey, (endpointRequestCounts.get(endpointKey) || 0) + 1);
  const requested = [...new Set(url.searchParams.getAll('projectPath'))];
  const key = requested.join(',') || 'none';
  requestCounts.set(key, (requestCounts.get(key) || 0) + 1);
  if (requested.includes('FAIL') && failOnce) {
    failOnce = false;
    res.statusCode = 503;
    res.end(JSON.stringify({detail:'transient test failure'}));
    return;
  }
  if (requested.includes('A') && !requested.includes('B')) {
    while (!releaseA) await new Promise((resolve) => setTimeout(resolve, 10));
  }
  const selected = requested.flatMap((path) => chats[path] ? [chats[path]] : []);
  res.setHeader('Content-Type', 'application/json');
  res.end(JSON.stringify({ok:true,path:'test',exists:true,chats:selected,count:selected.length,sources:[],recoveries:[],writeBlocked:false}));
});

await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const waitForRequest = async (counts, key, baseline = 0) => {
  const deadline = Date.now() + 5000;
  while ((counts.get(key) || 0) <= baseline) {
    if (Date.now() >= deadline) throw new Error(`timed out waiting for ${key} request`);
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
};
let browser;
try {
  browser = await chromium.launch({headless:true,channel:process.env.VRCFORGE_BROWSER_CHANNEL || 'msedge'});
  const page = await browser.newPage();
  const origin = `http://127.0.0.1:${server.address().port}`;
  await page.route('**/*', (route) => route.request().url().startsWith(origin) ? route.continue() : route.abort());
  await page.goto(origin);
  await page.waitForFunction(() => window.testApi && [...window.testApi.paths].length === 1);
  await waitForRequest(requestCounts, 'A');
  await page.evaluate(() => window.testApi.bumpSame());
  await new Promise((resolve) => setTimeout(resolve, 50));
  releaseA = true;
  await page.waitForFunction(() => window.testApi.sessions.chats.length === 1);
  assert.equal(requestCounts.get('A'), 1, 'same storage scope during startup must not enqueue a duplicate fetch');

  await page.evaluate(() => {
    window.testApi.sessions.updateChat('chat-a', (chat) => ({...chat, title:'local dirty A'}));
    window.testApi.switchPath('B');
  });
  await page.waitForFunction(() => window.testApi.sessions.chats.some((chat) => chat.id === 'chat-b'));
  assert.equal(await page.evaluate(() => window.testApi.sessions.chats.find((chat) => chat.id === 'chat-a')?.title), 'local dirty A',
    'scope refresh must retain dirty local chat content');
  assert.equal(await page.evaluate(() => window.testApi.sessions.chats.find((chat) => chat.id === 'chat-b')?.title), 'server B',
    'a newer scope must not commit the stale scope response');

  releaseA = false;
  const scopeARequest = requestCounts.get('A') || 0;
  await page.goto(`${origin}/?scope=1`);
  await page.waitForFunction(() => window.testApi);
  await waitForRequest(requestCounts, 'A', scopeARequest);
  await page.evaluate(() => window.testApi.switchPath('B'));
  await page.evaluate(() => window.testApi.switchPath('A'));
  releaseA = true;
  await page.waitForFunction(() => window.testApi.sessions.chats.some((chat) => chat.id === 'chat-a'));
  assert.deepEqual(await page.evaluate(() => window.testApi.sessions.chats.map((chat) => chat.id)), ['chat-a'],
    'A-to-B-to-A while A is in flight must not commit a stale B scope');

  const altEndpointRequest = endpointRequestCounts.get('alt') || 0;
  await page.evaluate(() => window.testApi.switchEndpoint('/alt'));
  await waitForRequest(endpointRequestCounts, 'alt', altEndpointRequest);

  const retryPage = await browser.newPage();
  await retryPage.goto(`${origin}/?retry=1`);
  await retryPage.waitForFunction(() => window.testApi);
  await retryPage.waitForFunction(() => window.testApi && window.testApi.sessions.chats.length === 0 && (window.testApi.paths || []).includes('FAIL'));
  await retryPage.waitForTimeout(100);
  assert.equal(requestCounts.get('FAIL'), 1, 'the first failed restore must reach the server');
  await retryPage.evaluate(() => window.testApi.bumpSame());
  await retryPage.waitForFunction(() => window.testApi.sessions.chats.some((chat) => chat.id === 'chat-fail'));
  assert.equal(requestCounts.get('FAIL'), 2, 'a failed restore must be retryable for the same scope');
  await retryPage.close();
  console.log('PASS restore queue scope dedupe, dirty merge, and retry fixture');
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
