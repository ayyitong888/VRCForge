import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';

const replay = process.env.VRCFORGE_COMPLETION_REPLAY_DIR;
const original = replay ? JSON.parse(await readFile(`${replay}/model-final-plan.json`, 'utf8')) : {
  reply: 'The outfit is unfinished: local mesh adjustment is unavailable and the shoulder still needs inspection.',
  completionClaim: { satisfied: false },
};
const response = replay ? JSON.parse(await readFile(`${replay}/receipt.json`, 'utf8')).response : {
  sessionId: 'fixture', clientTurnId: 'fixture-turn', steps: [], timeline: [], plan: {},
};
response.plan = { ...response.plan, ...original, nextStep: 'completion_unverified', completionGate: { status: 'failed' } };
response.timeline = [{ id: 'final', sequence: 1, kind: 'assistant', timestamp: '2026-01-01T00:00:00Z',
  payload: { summary: original.reply, status: 'failed', phase: 'final_answer' } }];
const bundle = await build({ stdin: { resolveDir: process.cwd(), loader: 'tsx', contents: `
  import React from 'react'; import {createRoot} from 'react-dom/client';
  import {ConversationCard} from './src/components/chat/conversation-card';
  import {initializeI18n} from './src/i18n';
  const root=createRoot(document.getElementById('root'));
  window.mount=async response=>{await initializeI18n();root.render(<ConversationCard item={{id:'reply',type:'agent',response}}/>);};
` }, bundle: true, write: false, format: 'iife', platform: 'browser', jsx: 'automatic' });
const server = createServer((_req, res) => {
  res.setHeader('Content-Type', 'text/html');
  res.end('<div id="root"></div><script>' + bundle.outputFiles[0].text.replaceAll('</script', '<\\/script') + '</script>');
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || 'playwright');
let browser;
try {
  browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.evaluate(response => window.mount(response), response);
  const reply = page.locator('[data-chat-quote-content]').filter({ hasText: original.reply.split('\n')[0].replaceAll('`', '').replaceAll('**', '') }).first();
  await reply.waitFor();
  assert.equal(await reply.evaluate(element => Boolean(element.closest('[data-agent-turn-process-group]'))), false,
    'an honest unfinished explanation is a final answer outside collapsed activity');
  assert.match(await reply.textContent(), /unfinished|未完成|不能宣称全部完成/);
  assert.deepEqual(errors, []);
  console.log('PASS incomplete explanation is visibly rendered outside collapsed process, without a completion claim');
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
