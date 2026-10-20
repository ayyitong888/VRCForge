import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { build } from "esbuild";
import { readFile } from "node:fs/promises";
import { basename, dirname, join } from "node:path";

const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
import React, { useCallback, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { useQuestionContinuationActivity, useRuntimeTurnContinuationDelivery } from './src/hooks/use-runtime-turn-continuation';
import { stripSupersededStreamingItems } from './src/lib/chat-thread';
import { ConversationCard } from './src/components/chat/conversation-card';
import { projectConversationSteeringMessages } from './src/lib/chat-steering-projection';
import { initializeI18n } from './src/i18n';
const initial = { id: 'chat', sessionId: 'session', items: [
  { id: 'user-active-turn', type: 'user', text: 'Initial user', clientTurnId: 'active-turn', createdAt: '2026-10-19T12:00:00Z' },
  { id: 'stream', type: 'streaming', clientTurnId: 'active-turn', text: 'Current model commentary', phase: 'running_tool', createdAt: '2026-10-19T12:00:00Z' },
  { id: 'confirmed', type: 'user', text: 'same text', clientTurnId: 'confirmed-input', queuedFrom: true, queueStatus: 'steering', createdAt: '2026-10-19T12:00:01Z' },
  { id: 'pending', type: 'user', text: 'same text', clientTurnId: 'pending-input', queuedFrom: true, queueStatus: 'steering', createdAt: '2026-10-19T12:00:02Z' },
] };
const questions = []; const runs = []; const settled = new Set();
function Harness() {
 const latest = useRef(initial); const [chats, setChats] = useState([initial]);
 const update = useCallback((id, updater) => {
  const updated = updater(latest.current); if (updated === latest.current) return false;
  const next = {...updated, items: stripSupersededStreamingItems(updated.items)};
  latest.current = next; setChats([next]); window.chat = next; return true;
 }, []);
 const append = useCallback((id, item) => update(id, current => ({...current, items: [...current.items, item]})), [update]);
 const delta = useQuestionContinuationActivity({chats, questions, runs, updateChat: update, settledClientTurnIds: settled});
 const terminal = useRuntimeTurnContinuationDelivery({chats, updateChat: update, appendToChat: append, onContinuation: () => { window.terminalCalls++; }});
 window.terminalCalls ??= 0;
 window.receipt = () => delta({type: 'agentRuntimeDelta', sessionId: 'session', clientTurnId: 'active-turn', consumedSteerInputIds: ['confirmed-input']});
 window.wrongSession = () => delta({type: 'agentRuntimeDelta', sessionId: 'other-session', clientTurnId: 'active-turn', consumedSteerInputIds: ['pending-input']});
 window.finish = () => { update('chat', current => ({...current, items: current.items.filter(item => item.type !== 'streaming')})); };
 window.terminal = () => terminal({schema: 'vrcforge.runtime_turn_event.v1', continuationSource: 'foreground_completed', sessionId: 'session', turnId: 'server-turn', clientTurnId: 'active-turn', consumedSteerInputIds: ['confirmed-input'], plan: {reply: 'Final response', nextStep: 'done'}});
 const projection = projectConversationSteeringMessages(chats[0].items);
 window.projectedSteers = projection.messagesByTurnId.get('active-turn')?.map(item => item.id) || [];
 return <div><section data-real-cards>{chats[0].items.filter(item => item.type === 'user' || item.type === 'streaming').map(item => <div key={item.id} data-real-id={item.id}><ConversationCard item={item} /></div>)}</section>{chats[0].items.map(item => <div key={item.id} data-kind={item.type} data-id={item.id} data-provenance={String(item.queuedFrom === true)} data-status={item.queueStatus || 'ordinary'}>{item.type === 'agent' ? item.response.plan.reply : item.text}</div>)}{chats[0].items.some(item => item.type === 'streaming') && <button data-stop>Stop</button>}</div>;
}
void initializeI18n().then(() => createRoot(document.getElementById('root')).render(<Harness />));
` }, bundle: true, write: false, format: "iife", platform: "browser", jsx: "automatic",
 plugins: process.env.VRCFORGE_STEER_CALLER_BASELINE ? [{name:'original-callers',setup(build) {
   build.onLoad({filter: /(?:conversation-card\.tsx|chat-steering-projection\.ts)$/}, async args => ({
     contents: await readFile(join(process.env.VRCFORGE_STEER_CALLER_BASELINE,basename(args.path)),'utf8'),
     loader: args.path.endsWith('.tsx') ? 'tsx' : 'ts', resolveDir: dirname(args.path),
   }));
 }}] : [],
});
const server = createServer((_req, response) => {
  response.setHeader("Content-Type", "text/html");
  response.end(`<div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
});
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
let browser;
try {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({channel: "msedge", headless: true});
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.waitForFunction(() => typeof window.receipt === "function");
  await page.evaluate(() => window.receipt());
  await page.waitForFunction(() => document.querySelector('[data-id="confirmed"]')?.getAttribute('data-status') === 'ordinary');
  assert.equal(await page.locator('[data-id="pending"]').getAttribute('data-status'), 'steering');
  assert.equal(await page.locator('[data-kind="streaming"]').count(), 1);
  assert.equal(await page.locator('[data-kind="streaming"]').textContent(), 'Current model commentary');
  assert.equal(await page.locator('[data-stop]').count(), 1);
  assert.equal(await page.locator('[data-id="confirmed"]').getAttribute('data-provenance'), 'true');
  assert.equal(await page.locator('[data-real-id="confirmed"] [data-user-message-status]').count(), 0, 'real ConversationCard shows no pending metadata after consumption');
  assert.equal(await page.locator('[data-real-id="pending"] [data-user-message-status="steering"]').count(), 1, 'unconfirmed input retains its visible pending metadata');
  const pendingMetadata=page.locator('[data-real-id="pending"] [data-user-message-status]');
  assert.equal(await pendingMetadata.evaluate(element=>Boolean(element.closest('[data-real-id]').querySelector('[data-user-message-bubble]').compareDocumentPosition(element)&Node.DOCUMENT_POSITION_FOLLOWING)),true,'pending metadata follows the actual message bubble');
  assert.doesNotMatch(await pendingMetadata.getAttribute('class'),/rounded|bg-|border/,'pending metadata has no capsule decoration');
  assert.deepEqual(await page.evaluate(() => window.projectedSteers), ['confirmed'], 'delivered same-turn provenance joins the turn; unconfirmed input stays pending');
  assert.equal(await page.locator('[data-real-cards] [data-conversation-streaming-turn]').count(), 1, 'real streaming Card remains visible');
  assert.equal(await page.evaluate(() => window.terminalCalls), 0);
  await page.evaluate(() => { window.wrongSession(); window.receipt(); });
  assert.equal(await page.locator('[data-id="pending"]').getAttribute('data-status'), 'steering');
  await page.evaluate(() => window.finish());
  await page.waitForFunction(() => !document.querySelector('[data-stop]'));
  await page.evaluate(() => window.terminal());
  await page.waitForFunction(() => document.querySelector('[data-kind="agent"]')?.textContent === 'Final response');
  assert.equal(await page.locator('[data-kind="agent"]').count(), 1);
  assert.equal(await page.locator('[data-id="confirmed"]').getAttribute('data-provenance'), 'false');
  console.log('live consumed-ID delta: pending becomes ordinary; active stream/Stop remain; unconfirmed retained; final still delivered');
} finally {
  await browser?.close(); await new Promise(resolve => server.close(resolve));
}
