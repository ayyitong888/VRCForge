import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import { build } from "esbuild";

const replay = process.env.VRCFORGE_RUNTIME_ACTIVITY_REPLAY
  ? JSON.parse(await readFile(process.env.VRCFORGE_RUNTIME_ACTIVITY_REPLAY, "utf8")) : undefined;
const questionReplay = process.env.VRCFORGE_QUESTION_CONTINUATION_REPLAY
  ? JSON.parse(await readFile(process.env.VRCFORGE_QUESTION_CONTINUATION_REPLAY, "utf8")) : undefined;
const run = replay?.run || { sessionId: "external-session", clientTurnId: "external-cid", turnId: "backend-turn", status: "running", startedAt: "2026-10-23T12:00:00Z" };
const events = replay?.events || Array.from({ length: 450 }, (_, sequence) => ({ id: `accepted-${sequence}`, sequence,
  timestamp: new Date(Date.parse("2026-10-23T12:00:00Z") + sequence * 1000).toISOString(), kind: "assistant",
  payload: { phase: "commentary", summary: `Accepted public dialogue ${sequence}` } }));
const captureIds = ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"];
const questionRun = { sessionId: "question-session", clientTurnId: "turn:question:first", turnId: "question-turn", status: "blocked" };
const questionEvents = Array.from({ length: 410 }, (_, sequence) => {
  const event = { id: `question-event-${sequence}`, sequence, timestamp: new Date(1792972800000 + sequence * 1000).toISOString() };
  if (sequence === 402 || sequence === 404) {
    const captureId = captureIds[sequence === 402 ? 0 : 1];
    return { ...event, kind: "tool_result", payload: { captures: [{ preview: {
      schema: "vrcforge.scene_capture_preview.v1", mimeType: "image/png", captureId,
      resourceUri: `vrcforge://scene-capture-preview/${captureId}?context=runtime%3Aquestion-session&revision=1&store=11111111111111111111111111111111`,
    } }] } };
  }
  return { ...event, kind: "assistant", payload: { phase: sequence === 409 ? "final_answer" : "commentary" } };
});
const questionCase = questionReplay || {
  run: questionRun, events: questionEvents,
  terminal: { ...questionRun, continuationSource: "question_answered", plan: { nextStep: "needs_user_action" }, timeline: questionEvents.slice(0, 256) },
  question: { sessionId: questionRun.sessionId, originClientTurnId: questionRun.clientTurnId },
  lastCaptureIds: captureIds,
};
assert.ok(events.length > 2, "a history/live overlap needs multiple original events");
const pageSize = Math.min(200, Math.ceil(events.length / 2));
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
import React, { useCallback, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { useRuntimeTurnActivity } from './src/hooks/use-runtime-turn-activity';
import { useRuntimeTurnContinuationDelivery } from './src/hooks/use-runtime-turn-continuation';
import { capturePreviewsForQuestion } from './src/lib/chat-timeline-presentation';
window.questionCaptures = question => capturePreviewsForQuestion(question, window.visibleChats[0].items);
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
  return <div id='mounted' data-session={chats.at(-1)?.sessionId} />;
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
  {
    const { run: origin, events: originalEvents, terminal: oldTerminal, question, lastCaptureIds } = questionCase;
    await page.evaluate(({ origin, oldTerminal }) => {
      const timeline = oldTerminal.timeline.map(event => {
        const { captures, ...payload } = event.payload;
        return { ...event, payload };
      });
      window.resetChats([{ id: 'original-question-owner', sessionId: origin.sessionId, projectPath: origin.projectRoot,
        items: [{ id: 'saved-question-continuation', type: 'agent', response: { ...oldTerminal, timeline }, timeline }] }]);
      window.replaceRuns([origin]);
    }, { origin, oldTerminal });
    for (let cursor = 0; cursor < originalEvents.length; cursor += 200) {
      const portion = originalEvents.slice(cursor, cursor + 200);
      await page.waitForFunction(() => window.pendingPages.length === 1);
      await page.evaluate(({ origin, portion, hasMore }) => window.pendingPages.shift()({ ok: true, run: origin,
        events: portion, nextSequence: portion.at(-1).sequence + 1, hasMore }),
      { origin, portion, hasMore: cursor + 200 < originalEvents.length });
    }
    await page.waitForFunction(({ question, lastCaptureIds }) => lastCaptureIds.every(id =>
      window.questionCaptures(question).some(capture => capture.resourceUri.includes(id))), { question, lastCaptureIds });
    assert.equal(await page.evaluate(() => window.visibleChats[0].items.some(i => i.type === 'streaming')), false,
      'repairing saved question images never starts another turn');
  }
  {
    const oldRun = { sessionId: "retry-session", clientTurnId: "retry-cid", turnId: "old-turn",
      status: "running", startedAt: "2026-10-23T13:00:00Z", updatedAt: "2026-10-23T13:03:00Z" };
    const newRun = { ...oldRun, turnId: "new-turn", startedAt: "2026-10-23T13:01:00Z", updatedAt: "2026-10-23T13:02:00Z" };
    await page.evaluate(run => {
      window.resetChats([{ id: 'retry-owner', sessionId: run.sessionId, items: [
        { type: 'user', id: 'retry-user', clientTurnId: run.clientTurnId, text: 'Retry' },
      ] }]);
      window.replaceRuns([run]);
    }, oldRun);
    await page.waitForFunction(() => window.pendingPages.length === 1);
    const oldEvent = { ...events[0], id: "old-event" };
    await page.evaluate(({ run, event }) => window.pendingPages.shift()({ ok: true, run, events: [event], nextSequence: 1, hasMore: true }),
      { run: oldRun, event: oldEvent });
    await page.waitForFunction(() => window.requests.at(-1).afterSequence === 1);
    await page.evaluate(run => window.deliver({ ...run, schema: 'vrcforge.runtime_turn_event.v1',
      continuationSource: 'foreground_completed', status: 'cancelled', plan: { reply: 'Older stopped' } }), oldRun);
    await page.evaluate(({ oldRun, newRun }) => window.replaceRuns([oldRun, newRun]), { oldRun, newRun });
    await page.waitForFunction(() => window.requests.slice(-2).every(r => r.clientTurnId === 'retry-cid')
      && window.requests.at(-2).aborted && window.requests.at(-1).afterSequence === 0);
    const newEvent = { ...events[0], id: "new-event" };
    await page.evaluate(({ run, event }) => {
      window.pendingPages.shift(); // Aborted request for the superseded generation.
      window.pendingPages.shift()({ ok: true, run, events: [event], nextSequence: 1, hasMore: false });
    }, { run: newRun, event: newEvent });
    await page.waitForFunction(() => window.visibleChats[0].items.some(i => i.type === 'streaming' && i.timeline?.some(e => e.id === 'new-event')));
    await page.evaluate(run => window.deliver({ ...run, schema: 'vrcforge.runtime_turn_event.v1',
      continuationSource: 'foreground_completed', status: 'completed', plan: { reply: 'Retry completed' } }), newRun);
    const retryItems = await page.evaluate(() => window.visibleChats[0].items);
    const receipts = retryItems.filter(i => i.type === "agent");
    assert.equal(receipts.length, 1);
    assert.equal(receipts[0].response.turnId, newRun.turnId, "the old generation's delivered terminal cannot suppress the retry");
    assert.ok(receipts[0].timeline.some(e => e.id === newEvent.id));
    assert.ok(!receipts[0].timeline.some(e => e.id === oldEvent.id), "the replacement receipt only owns the latest generation's history");
  }
  // A reused session with two chats cannot be guessed; an exact CID owns one.
  await page.evaluate(() => {
    window.resetChats([{ id: 'wrong', sessionId: 'other-session', items: [] }, { id: 'owner', sessionId: window.identity.sessionId, items: [] }]);
  });
  await page.waitForFunction(() => document.querySelector('#mounted')?.dataset.session === window.identity.sessionId);
  await page.evaluate(() => {
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
  console.log(`question continuation: restored both late screenshots from ${questionCase.events.length} ${questionReplay ? "original ledger" : "fixture"} events`);
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
