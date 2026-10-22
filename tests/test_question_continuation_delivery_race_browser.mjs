import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { build } from "esbuild";

const fixture = process.env.VRCFORGE_QUESTION_CONTINUATION_REPLAY
  ? JSON.parse(await readFile(process.env.VRCFORGE_QUESTION_CONTINUATION_REPLAY, "utf8"))
  : {
    events: [{ schema: "vrcforge.runtime_turn_event.v1", continuationSource: "question_answered", sessionId: "session-replay", turnId: "answer-turn", clientTurnId: "first:question:choose-file", plan: { reply: "Read the selected file.", nextStep: "done" } }],
    chat: { id: "chat-replay", sessionId: "session-replay", items: [{ id: "first", type: "agent", response: { clientTurnId: "first", turnId: "initial-turn", plan: { reply: "Choose a file", nextStep: "needs_user_action" } } }, { id: "answer", type: "user", text: "Selected file" }] },
  };
assert.equal(fixture.events.length, 1);
const confirmedId = "runtime-confirmed-steer";
const unconfirmedId = "runtime-unconfirmed-steer";
const event = { ...fixture.events[0], consumedSteerInputIds: [confirmedId] };
const initialChat = {
  ...fixture.chat,
  items: [...fixture.chat.items,
    { id: "confirmed-steer", type: "user", text: "Additional read request", clientTurnId: confirmedId, queuedFrom: true, queueStatus: "steering" },
    { id: "unconfirmed-steer", type: "user", text: "Unconfirmed read request", clientTurnId: unconfirmedId, queuedFrom: true, queueStatus: "steering" },
    { id: `question-continuation:${event.clientTurnId}`, type: "streaming", clientTurnId: event.clientTurnId, text: "", phase: "waiting_for_model" }],
};
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { useRuntimeTurnContinuationDelivery, useQuestionContinuationActivity } from './src/hooks/use-runtime-turn-continuation';
const event = ${JSON.stringify(event)};
const initial = ${JSON.stringify(initialChat)};
const questions = [{ questionId: 'replay-question', sessionId: event.sessionId, status: 'answered', runtimeContinuation: { sessionId: event.sessionId, clientTurnId: event.clientTurnId, status: 'completed' } }];
const runs = [];
function Harness() {
  const latest = useRef(initial);
  const [chats, setChats] = useState([initial]);
  const settled = useRef(new Set());
  const updateChat = useCallback((id, updater) => {
    if (latest.current.id !== id) return false;
    const next = updater(latest.current);
    if (next === latest.current) return false;
    latest.current = next;
    window.visibleChat = next;
    setChats([next]);
    return true;
  }, []);
  const append = useCallback((id, item) => updateChat(id, current => ({...current, items: [...current.items, item]})), [updateChat]);
  const deliver = useRuntimeTurnContinuationDelivery({ chats, appendToChat: append, updateChat, onContinuation: value => {
    settled.current.add(value.clientTurnId);
    updateChat(initial.id, current => ({ ...current, items: current.items.filter(item => item.id !== 'question-continuation:' + value.clientTurnId) }));
  }});
  // Same ordering as App: bootstrap delivery effect precedes question activity projection.
  useEffect(() => { deliver(event); window.deliveryEffectRan = true; }, [deliver]);
  useQuestionContinuationActivity({ chats, questions, runs, updateChat, settledClientTurnIds: settled.current });
  return <div data-mounted />;
}
createRoot(document.getElementById('root')).render(<Harness />);
` },
  bundle: true, write: false, format: "iife", platform: "browser", jsx: "automatic",
});
const server = createServer((_req, response) => {
  response.setHeader("Content-Type", "text/html");
  response.end(`<div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
});
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
let browser;
try {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.waitForFunction(() => window.deliveryEffectRan && window.visibleChat);
  await page.waitForTimeout(100);
  const items = await page.evaluate(() => window.visibleChat.items);
  const replies = items.filter(item => item.type === "agent" && item.response.clientTurnId === event.clientTurnId);
  assert.equal(replies.length, 1, "question activity cleanup must preserve the final reply appended earlier in the same effect round");
  assert.equal(replies[0].response.plan.reply, event.plan.reply, "the original model final text survives unchanged");
  if (event.completedAt) {
    assert.equal(replies[0].completedAt, event.completedAt, "terminal display time follows the ledger completion timestamp");
  } else {
    assert.equal(replies[0].completedAt, undefined, "updatedAt or missing server completion time is never presented as completion");
  }
  assert.equal(items.some(item => item.type === "streaming"), false);
  assert.deepEqual(items.filter(item => item.type === "user" && ![confirmedId, unconfirmedId].includes(item.clientTurnId)), fixture.chat.items.filter(item => item.type === "user"));
  assert.equal(items.find(item => item.clientTurnId === confirmedId).queueStatus, undefined, "only the backend-confirmed consumed steer becomes ordinary history");
  assert.equal(items.find(item => item.clientTurnId === unconfirmedId).queueStatus, "steering", "an absent consumption receipt never claims delivery");
  console.log(`question continuation delivery race replay: passed (${process.env.VRCFORGE_QUESTION_CONTINUATION_REPLAY ? "original production final" : "fixture"}; confirmed-ID contract)`);
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
