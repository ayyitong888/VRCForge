import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { build } from 'esbuild';

// Real useChatSessions browser regression. The loopback server owns only this
// fixture; no installed profile, credentials, provider, or external traffic.
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
      await i18n.use(initReactI18next).init({lng:'en', resources:{en:{translation:{
        chat:{
          sessionConcurrentConflict:'CONCURRENT_CONFLICT',
          sessionSaveBlocked:'SAVE_BLOCKED',
          sessionRecoveryBlocked:'RECOVERY_BLOCKED',
          sessionRestoreFailed:'RESTORE_FAILED'
        }
      }}}});
      const noop=()=>{};
      const initial={chats:[],activeChatId:''};
      function Fixture(){
        const [error,setError]=useState('');
        const sessions=useChatSessions({endpoint:location.origin,activeView:'chat',runtimeConnected:true,
          projectPrefsReady:true,projectPaths:[],customProjectPaths:[],activeProjectPath:'',
          setActiveProjectPath:noop,setActiveView:noop,setError,expandProjectGroup:noop,initialChatState:initial});
        window.testApi={sessions};
        return <pre id="state">{JSON.stringify({error,blocked:sessions.chatPersistenceBlocked,title:sessions.chats[0]?.title || ''})}</pre>;
      }
      createRoot(document.getElementById('root')).render(<Fixture/>);
    `,
  },
  bundle: true,
  write: false,
  format: 'esm',
  jsx: 'automatic',
  define: {'process.env.NODE_ENV': '"test"'},
});

const localChat = {
  id:'chat', sessionId:'session', title:'initial', projectPath:'', projectType:'general', revision:0,
  createdAt:'2026-01-01T00:00:00Z', updatedAt:'2026-01-01T00:00:00Z',
  items:[{id:'u1',type:'user',text:'initial'}],
};
let remoteChat = structuredClone(localChat);
let remoteVersion = 'a'.repeat(64);
let remoteChanged = false;
const server = createServer(async (req, res) => {
  if (req.url?.startsWith('/api/app/chats')) {
    res.setHeader('Content-Type', 'application/json');
    if (req.method === 'POST') {
      // The blocked save must never reach the server after the conflict.
      res.statusCode = 500;
      res.end(JSON.stringify({detail:'unexpected save after conflict'}));
      return;
    }
    const chat = remoteChanged ? remoteChat : localChat;
    res.end(JSON.stringify({ok:true,chats:[chat],count:1,sources:[],recoveries:[],writeBlocked:false,
      chatDigests:{chat:remoteVersion},chatMetadataShapes:{chat:Object.fromEntries(Object.keys(chat).filter(key=>key!=='items').map(key=>[key,true]))}}));
    return;
  }
  if (req.url === '/fixture.js') {
    res.setHeader('Content-Type', 'text/javascript');
    res.end(bundle.outputFiles[0].text);
    return;
  }
  res.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
});

await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
let browser;
try {
  browser = await chromium.launch({headless:true, channel:process.env.VRCFORGE_BROWSER_CHANNEL || 'msedge'});
  const page = await browser.newPage();
  const origin = `http://127.0.0.1:${server.address().port}`;
  await page.route('**/*', (route) => route.request().url().startsWith(origin) ? route.continue() : route.abort());
  await page.goto(origin);
  await page.waitForFunction(() => window.testApi?.sessions?.chats.length === 1);
  await page.evaluate(() => window.testApi.sessions.updateChat('chat', (chat) => ({...chat, title:'local dirty'})));
  remoteChat = {...remoteChat, title:'remote dirty'};
  remoteVersion = 'b'.repeat(64);
  remoteChanged = true;
  await page.evaluate(() => window.testApi.sessions.notifyChatStorageChanged());
  await page.waitForFunction(() => window.testApi.sessions.chatPersistenceBlocked === true);
  await page.waitForFunction(() => document.querySelector('#state')?.textContent.includes('CONCURRENT_CONFLICT'));
  await page.waitForTimeout(1000);
  const state = await page.evaluate(() => JSON.parse(document.querySelector('#state').textContent));
  assert.equal(state.blocked, true, 'conflict remains persistence-blocked');
  assert.equal(state.title, 'local dirty', 'local dirty chat remains visible');
  assert.equal(state.error, 'CONCURRENT_CONFLICT', 'autosave failure must not overwrite conflict reason');
  console.log('PASS hook conflict message survives blocked autosave');
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
