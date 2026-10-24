import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import { build } from "esbuild";

const replay = process.env.VRCFORGE_RUNTIME_ACTIVITY_REPLAY
  ? JSON.parse(await readFile(process.env.VRCFORGE_RUNTIME_ACTIVITY_REPLAY, "utf8")) : undefined;
const run = replay?.run || { sessionId: "external-session", clientTurnId: "external-cid", turnId: "backend-turn", status: "running", startedAt: "2026-10-23T12:00:00Z" };
const events = replay?.events || Array.from({ length: 450 }, (_, sequence) => ({ id: `accepted-${sequence}`, sequence,
  timestamp: new Date(Date.parse("2026-10-23T12:00:00Z") + sequence * 1000).toISOString(), kind: "assistant",
  payload: { phase: "commentary", summary: `Accepted public dialogue ${sequence}` } }));
assert.ok(events.length > 2, "a history/live overlap needs multiple original events");
const pageSize = Math.min(200, Math.ceil(events.length / 2));
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
import React, { useCallback, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { useRuntimeTurnActivity } from './src/hooks/use-runtime-turn-activity';
import { useRuntimeTurnContinuationDelivery } from './src/hooks/use-runtime-turn-continuation';
const identity = ${JSON.stringify(run)};
const initial = { id: 'owner', sessionId: identity.sessionId, projectPath: '', title: 'Visible chat', items: [{ id: 'user', type: 'user', text: 'Continue', clientTurnId: identity.clientTurnId }] };
function Harness() {
  const [chats, setChats] = useState([initial]);
  const current = useRef(chats);
  const [runs, setRuns] = useState([{ ...identity, status: 'running' }]);
  const [endpoint, setEndpoint] = useState('http://fixture-a');
  const updateChat = useCallback((id, updater) => {
    let changed = false;
    current.current = current.current.map(chat => { if (chat.id !== id) return chat; const next = updater(chat); changed ||= next !== chat; return next; });
    if (changed) setChats(current.current);
    window.visibleChats = current.current;
    return changed;
  }, []);
  current.current = chats;
  const appendToChat = useCallback((id, item) => updateChat(id, chat => ({ ...chat, items: [...chat.items, item] })), [updateChat]);
  const deliver = useRuntimeTurnContinuationDelivery({ chats, appendToChat, updateChat });
  const delta = useRuntimeTurnActivity({ endpoint, runtimeConnected: true, chats, runs, updateChat, deliver });
  window.delta = delta;
  window.deliver = deliver;
  window.replaceRuns = setRuns;
  window.setEndpoint = setEndpoint;
  window.resetChats = next => { current.current = next; window.visibleChats = next; setChats(next); };
  window.visibleChats = chats;
  return <div id='mounted' />;
}
window.requests = [];
window.pendingPages = [];
window.finalState = { ok: true, sessionId: identity.sessionId, clientTurnId: identity.clientTurnId, status: 'running' };
window.identity = identity;
window.root = createRoot(document.getElementById('root'));
window.root.render(<Harness />);
` },
  bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
  plugins: [{ name: "timeline-transport-fixture", setup(builder) {
    builder.onResolve({ filter: /^\.\.\/lib\/api\/agent-runtime$/ }, args => args.importer.endsWith("use-runtime-turn-activity.ts")
      ? { path: "timeline-api", namespace: "fixture" } : undefined);
    builder.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({ loader: "js", contents: `
export const fetchAgentRunTimeline = (endpoint, sessionId, clientTurnId, afterSequence, signal) => new Promise((resolve, reject) => {
  const request = { endpoint, sessionId, clientTurnId, afterSequence, aborted: signal.aborted };
  window.requests.push(request); window.pendingPages.push(resolve);
  signal.addEventListener('abort', () => { request.aborted = true; reject(new Error('aborted')); }, {once:true});
});
export const fetchAgentTurnResponse = async () => window.finalState;
` }));
  } }],
});
const server = createServer((_request, response) => {
  response.setHeader("Content-Type", "text/html");
  response.end(`<div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
});
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
let browser;
try {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ headless: true, channel: "msedge" });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.waitForFunction(() => window.pendingPages.length === 1);
  assert.equal(await page.evaluate(() => window.visibleChats[0].items.filter(i => i.type === "streaming").length), 1,
    "an externally admitted turn is discovered without the local send map");
  const overlap = events[pageSize];
  await page.evaluate(event => window.delta({ type: "agentRuntimeDelta", ...window.identity, timelineEvent: event }), overlap);
  await page.evaluate(({ run, events, pageSize }) => window.pendingPages.shift()({ ok: true, run: { ...run, status: "running" },
    events: events.slice(0, pageSize), nextSequence: events[pageSize - 1].sequence + 1, hasMore: true }), { run, events, pageSize });
  await page.waitForFunction(() => window.requests.length >= 2);
  // A final receipt arrives while the next history page is in flight.
  const terminal = replay?.terminal || { schema: "vrcforge.runtime_turn_event.v1", continuationSource: "foreground_completed", ...run,
    plan: { reply: "Original terminal reply", summary: "Original terminal reply", planner: "fixture", nextStep: "needs_user_action" }, timeline: [events.at(-1)] };
  await page.evaluate(event => window.deliver(event), terminal);
  let consumed = pageSize;
  while (consumed < events.length) {
    const portion = events.slice(consumed, consumed + pageSize);
    consumed += portion.length;
    await page.evaluate(({ run, portion, hasMore }) => window.pendingPages.shift()({ ok: true, run: { ...run, status: "running" }, events: portion,
      nextSequence: portion.at(-1).sequence + 1, hasMore }), { run, portion, hasMore: consumed < events.length });
    if (consumed < events.length) await page.waitForFunction(() => window.pendingPages.length > 0);
  }
  await page.waitForFunction(count => window.visibleChats[0].items.find(i => i.type === "agent")?.timeline?.length === count, events.length);
  await page.evaluate(event => window.deliver(event), terminal);
  const restored = await page.evaluate(() => window.visibleChats[0]);
  const replies = restored.items.filter(i => i.type === "agent");
  assert.equal(replies.length, 1, "history/live/terminal replay must settle exactly once");
  assert.equal(restored.items.some(i => i.type === "streaming"), false);
  assert.deepEqual(new Set(replies[0].timeline.map(e => e.id)), new Set(events.map(e => e.id)), "all original public occurrences survive overlap and terminal race");
  assert.equal(replies[0].response.plan.nextStep, terminal.plan.nextStep, "settling transport does not invent task completion");
  assert.equal(replies[0].completedAt, terminal.completedAt, "no terminal timestamp is fabricated");
  assert.equal(replies[0].response.plan.reply, terminal.plan.reply);
  // A duplicate live occurrence on an already observed turn must not refetch
  // history for every delta (snapshot stamps remain owned by snapshots).
  assert.ok(await page.evaluate(() => window.requests.length) <= Math.ceil(events.length / pageSize) + 1);
  // Storage reconciliation can restore a stale stream after delivery was recorded.
  // A settled observer must still reconcile it without another terminal event.
  await page.evaluate(({ restored, run }) => {
    window.resetChats([{ ...restored, items: [...restored.items, {
      id: 'runtime-activity:' + run.clientTurnId, type: 'streaming', clientTurnId: run.clientTurnId,
      text: '', phase: 'verifying', timeline: [{ id: 'late-progress', kind: 'assistant',
        timestamp: '2026-10-23T12:10:00Z', payload: { phase: 'commentary', summary: 'Late accepted progress' } }],
    }] }]);
    window.replaceRuns([{ ...run, status: 'blocked' }]);
  }, { restored, run });
  await page.waitForTimeout(100);
  assert.equal(await page.evaluate(() => window.visibleChats[0].items.some(i => i.type === 'streaming')), false,
    'exact terminal receipt removes a restored stale activity even after observer marked the turn finished');
  assert.ok(await page.evaluate(() => window.visibleChats[0].items.find(i => i.type === 'agent').timeline.some(e => e.id === 'late-progress')),
    'settlement transfers accepted stream history into the existing receipt');
  // A reused session with two chats cannot be guessed; an exact CID owns one.
  await page.evaluate(() => {
    window.resetChats([{ id: 'wrong', sessionId: 'other-session', items: [] }, { id: 'owner', sessionId: window.identity.sessionId, items: [] }]);
    window.delta({ type: 'agentRuntimeDelta', ...window.identity, clientTurnId: 'second-cid', turnId: 'second-turn', activity: true });
  });
  await page.waitForFunction(() => window.requests.some(r => r.clientTurnId === 'second-cid'));
  const before = await page.evaluate(() => window.visibleChats[0]);
  assert.deepEqual(before.items, [], "external deltas never reach another session");
  await page.evaluate(() => window.setEndpoint('http://fixture-b'));
  await page.waitForFunction(() => window.requests.find(r => r.clientTurnId === 'second-cid').aborted);
  await page.evaluate(() => window.root.unmount());
  assert.deepEqual(errors, []);
  console.log(`runtime activity browser replay: passed (${events.length} ${replay ? "original ledger" : "fixture"} events; pagination/live/terminal overlap, no duplicate stream, exact ownership, abort)`);
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
