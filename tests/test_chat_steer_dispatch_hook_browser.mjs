import assert from "node:assert/strict";
import { createServer } from "node:http";
import { build } from "esbuild";
import { createRequire } from "node:module";

const { chromium } = createRequire(import.meta.url)(
  process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright",
);
const apiStub = `
export const cancelAgentRunFollowup = async (_endpoint, queueId) => ({ ok: true, queueId, status: "cancelled" });
export const listAgentRunFollowups = async () => ({ ok: true, items: window.__recoveryQueue || window.__durableQueue || [] });
export const fetchAgentQuestions = async (_endpoint, params = {}) => ({
  ok: true,
  questions: window.__agentQuestions.filter((question) => !params.sessionId || question.sessionId === params.sessionId),
});
export const issueComputerUseTurnGrant = async () => null;
export const requestAgentRunCancel = async () => ({ ok: true });
export const requestChatTitle = async () => ({ title: "Fixture" });
export const sendAgentMessage = (_endpoint, message, _session, history, _agent, options) => new Promise((resolve, reject) => {
  window.__providerRequests.push({ message, history, followupQueueId: options?.followupQueueId, clientTurnId: options?.clientTurnId });
  if (window.__agentQuestions.some((question) => question.sessionId === _session && question.status === "pending")) {
    window.__questionBlockedDispatches.push(options?.followupQueueId || "direct");
    reject(new Error("409 native conversation has pending tool calls"));
    return;
  }
  window.__providerSignal = options?.signal;
  window.__resolveProvider = resolve;
  window.__providerStarted = true;
});
export const recordAgentRunQueued = async (endpoint, payload) => {
  window.__queueRequests.push(payload);
  window.__queueObserved = { sessionId: payload.sessionId, clientTurnId: payload.clientTurnId, targetClientTurnId: payload.targetClientTurnId };
  const result = payload.targetClientTurnId
    ? { ok: true, accepted: true, mode: "steer" }
    : { ok: true, accepted: true, mode: "followup", queueId: "queue-" + payload.clientTurnId, sequence: window.__queueRequests.length };
  if (result.mode === "followup") window.__durableQueue.push({
    ...payload, queueId: result.queueId, sequence: result.sequence, sessionId: payload.laneId || payload.sessionId,
    runtimeSessionId: payload.sessionId, status: "pending",
  });
  return result;
};
`;

const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
import React from "react";
import { createRoot } from "react-dom/client";
import { useState } from "react";
import { useChatRunController } from "./src/hooks/use-chat-run-controller";

const chat = { id: "chat-fixture", sessionId: "session-fixture", items: [], title: "Existing chat" };
let releasePersist;
let persistCalls = 0;
let failNextPersist = false;
const persist = () => {
  persistCalls += 1;
  if (failNextPersist) {
    failNextPersist = false;
    return Promise.reject(new Error("fixture chat snapshot save failed"));
  }
  if (persistCalls === 1) return new Promise((resolve) => { releasePersist = resolve; });
  return Promise.resolve();
};
function Harness({ initialItems = [] }) {
  const [chatState, setChatState] = useState([{ ...chat, items: initialItems }]);
  const controller = useChatRunController({
    endpoint: "http://127.0.0.1:8757", runtimeConnected: true, sessionId: "session-fixture",
    activeRuntimeProjectPath: "", getChatById: (id) => id === chat.id ? chat : undefined,
    ensureActiveChat: () => chat.id,
    updateChat: (id, update) => { if (id === chat.id) Object.assign(chat, update(chat)); },
    appendToChat: (id, item) => { if (id === chat.id) chat.items = [...chat.items, item]; },
    touchChat: (value) => value,
    startRuntime: async () => "http://127.0.0.1:8757", refresh: async () => {},
    refreshRuntimeRuns: async () => {}, refreshBackgroundGoals: () => {},
    handleRuntimeSessionFailure: () => {}, setError: () => {},
    persistChatsNow: persist,
    traceSteerDelivery: (event) => window.__traceEvents.push(event),
    chats: chatState,
  });
  window.__controller = controller;
  window.__setQuestionActivity = (active) => {
    chat.items = active ? [{
      id: "question-continuation:question-fixture", type: "streaming", clientTurnId: "question-turn", text: "",
    }] : [];
    setChatState([{ ...chat }]);
  };
  window.__chat = chat;
  window.__queuedTurns = () => controller.queued;
  window.__isRunning = () => controller.isRunning();
  window.__releasePersist = () => releasePersist?.();
  window.__persistCalls = () => persistCalls;
  window.__failNextPersist = () => { failNextPersist = true; };
  return <div id="hook-mounted" />;
}
const root = createRoot(document.getElementById("root"));
root.render(<Harness />);
window.__mountRestoredController = (items) => {
  const host = document.createElement("div");
  document.body.append(host);
  const restoredRoot = createRoot(host);
  restoredRoot.render(<Harness initialItems={items} />);
};
window.__traceEvents = [];
window.__providerStarted = false;
window.__providerRequests = [];
window.__agentQuestions = [];
window.__questionBlockedDispatches = [];
window.__queueObserved = null;
window.__queueRequests = [];
window.__durableQueue = [];
window.__activeRunPromise = null;
window.__steerPromise = null;
window.__startFixture = () => {
  window.__providerStarted = false;
  window.__activeRunPromise = window.__controller.submitTurn({
    id: "active-turn-fixture", text: "read-only active turn", attachments: [], provider: "fixture",
    providerLabel: "Fixture", model: "fixture", sessionId: "session-fixture", planMode: false,
  });
};
window.__submitFixtureSteer = () => {
  window.__steerPromise = window.__controller.submitTurn({
    id: "console-steer-fixture", text: "sensitive fixture text must not enter trace", attachments: [],
    provider: "fixture", providerLabel: "Fixture", model: "fixture", sessionId: "session-fixture", planMode: false,
  });
};
window.__submitFixtureQueued = () => window.__controller.submitTurn({
  id: "console-queued-fixture", text: "queued fixture text", attachments: [], provider: "fixture",
  providerLabel: "Fixture", model: "fixture", sessionId: "session-fixture", planMode: false,
}, { deliveryMode: "queue" });
window.__editFixtureQueued = () => window.__controller.editQueuedTurn("console-queued-fixture", { text: "edited queued fixture" });
window.__queueFixture = (id, text) => window.__controller.submitTurn({
  id, text, attachments: [], provider: "fixture", providerLabel: "Fixture", model: "fixture",
  sessionId: "session-fixture", planMode: false,
}, { deliveryMode: "queue" });
window.__removeFixtureQueued = (id) => window.__controller.removeQueuedTurn(id);
window.__steerFixtureQueued = (id) => window.__controller.steerQueuedTurn(id);
`,
  },
  bundle: true,
  write: false,
  format: "iife",
  jsx: "automatic",
  platform: "browser",
  plugins: [{
    name: "steer-hook-test-stubs",
    setup(buildApi) {
      buildApi.onResolve({ filter: /^react-i18next$/ }, () => ({ path: "react-i18next", namespace: "steer-stub" }));
      buildApi.onResolve({ filter: /^\.\.\/lib\/api$/ }, (args) => args.importer.endsWith("use-chat-run-controller.ts")
        ? ({ path: "app-api", namespace: "steer-stub" }) : undefined);
      buildApi.onResolve({ filter: /^\.\.\/lib\/api\/agent-runtime$/ }, (args) => args.importer.endsWith("use-chat-run-controller.ts")
        ? ({ path: "agent-runtime-api", namespace: "steer-stub" }) : undefined);
      buildApi.onLoad({ filter: /.*/, namespace: "steer-stub" }, (args) => ({
        contents: args.path === "react-i18next"
          ? "export const useTranslation = () => ({ t: (key) => key });"
          : args.path === "agent-runtime-api"
            ? "export const DEFAULT_BACKGROUND_MAX_AGENTIC_TURNS = 12;"
            : apiStub,
        loader: "js",
      }));
    },
  }],
});

const server = createServer((_req, res) => {
  res.setHeader("Content-Type", "text/html");
  res.end(`<div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
});
let browser;
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.locator("#hook-mounted").waitFor({ state: "attached" });
  await page.evaluate(() => window.__startFixture());
  await page.waitForTimeout(60);
  await page.evaluate(() => window.__releasePersist());
  await page.waitForFunction(() => window.__providerRequests.length === 1);
  await page.evaluate(() => window.__submitFixtureSteer());
  await page.waitForFunction(() => window.__traceEvents.some((event) => event.stage === "persist_started"));
  await page.waitForTimeout(40);
  let state = await page.evaluate(() => ({
    stages: window.__traceEvents.map((event) => event.stage),
    rpcCalls: window.__queueObserved ? 1 : 0,
  }));
  assert.ok(state.stages.includes("persist_started"), "hook reports the exact save boundary before waiting");
  assert.equal(state.rpcCalls, 1, "text steer reaches the running turn while the unrelated full transcript save remains unresolved");
  assert.equal(state.stages.includes("rpc_started"), true);

  await page.evaluate(() => window.__releasePersist());
  await page.waitForFunction(() => window.__traceEvents.some((event) => event.stage === "rpc_ack"));
  state = await page.evaluate(() => ({ events: window.__traceEvents, queue: window.__queueObserved }));
  assert.deepEqual(state.queue, {
    sessionId: "session-fixture",
    clientTurnId: "console-steer-fixture",
    targetClientTurnId: "active-turn-fixture",
  }, "the native RPC receives the submitted ID and current active target separately");
  assert.ok(state.events.findIndex((event) => event.stage === "persist_resolved")
    < state.events.findIndex((event) => event.stage === "rpc_started"), "runtime admission waits for the durable transcript snapshot");
  const traceJson = JSON.stringify(state.events);
  assert.equal(traceJson.includes("sensitive fixture text"), false, "diagnostics never retain message text");
  assert.equal(pageErrors.length, 0, pageErrors.join("\n"));

  await page.evaluate(() => window.__resolveProvider({
    ok: true, status: "completed", sessionId: "session-fixture", turnId: "active-turn",
    plan: { reply: "fixture done", summary: "fixture done", nextStep: "done", planner: "fixture", shellNeeded: false },
    timeline: [],
  }));
  await page.evaluate(() => window.__activeRunPromise);

  await page.evaluate(() => window.__startFixture());
  await page.waitForFunction(() => window.__providerRequests.length === 2);
  const queueResult = await page.evaluate(() => window.__submitFixtureQueued());
  assert.equal(queueResult, "queued");
  let queueState = await page.evaluate(() => ({
    request: window.__queueRequests.at(-1),
    queued: window.__queuedTurns().map((turn) => ({ id: turn.id, queueId: turn.queueId, status: turn.queueStatus })),
    item: window.__chat.items.find((item) => item.clientTurnId === "console-queued-fixture"),
  }));
  assert.equal(Object.hasOwn(queueState.request, "targetClientTurnId"), false, "Enter queue mode omits the active target so the backend stores a durable follow-up");
  assert.deepEqual(queueState.queued.at(-1), { id: "console-queued-fixture", queueId: "queue-console-queued-fixture", status: "queued" });
  assert.equal(queueState.item.composerQueued, true, "pending entry remains durable but is marked for queue UI instead of transcript history");
  const edited = await page.evaluate(() => window.__editFixtureQueued());
  assert.ok(edited && edited.id !== "console-queued-fixture", "editing cancels and re-enqueues with a fresh idempotency identity");
  queueState = await page.evaluate(() => ({
    request: window.__queueRequests.at(-1),
    queued: window.__queuedTurns().map((turn) => turn.id),
    cancelled: window.__chat.items.find((item) => item.clientTurnId === "console-queued-fixture")?.queueStatus,
  }));
  assert.equal(queueState.request.message, "edited queued fixture");
  assert.equal(queueState.cancelled, "cancelled");
  assert.ok(queueState.queued.includes(edited.id));
  assert.equal(await page.evaluate(() => window.__queueFixture("console-remove-fixture", "remove fixture")), "queued");
  const removalId = await page.evaluate(() => window.__queuedTurns().find((turn) => turn.id === "console-remove-fixture")?.id);
  assert.equal(await page.evaluate((id) => window.__removeFixtureQueued(id), removalId), true, "removing a pending entry confirms backend cancellation first");
  await page.evaluate(() => window.__resolveProvider({
    ok: true, status: "completed", sessionId: "session-fixture", turnId: "active-turn-2",
    plan: { reply: "fixture done", summary: "fixture done", nextStep: "done", planner: "fixture", shellNeeded: false },
    timeline: [],
  }));
  await page.waitForFunction(() => window.__providerRequests.length === 3);
  const delivered = await page.evaluate((id) => ({
    request: window.__providerRequests.at(-1),
    item: window.__chat.items.find((item) => item.clientTurnId === id),
  }), edited.id);
  assert.equal(delivered.request.followupQueueId, `queue-${edited.id}`, "the existing durable follow-up id is claimed by the normal runtime delivery path");
  assert.equal(delivered.item.composerQueued, undefined, "delivery converts the pending card to a normal transcript bubble");
  await page.evaluate(() => window.__resolveProvider({
    ok: true, status: "completed", sessionId: "session-fixture", turnId: "queued-turn",
    plan: { reply: "queued fixture done", summary: "queued fixture done", nextStep: "done", planner: "fixture", shellNeeded: false },
    timeline: [],
  }));
  await page.evaluate(() => window.__activeRunPromise);
  await page.evaluate(() => window.__startFixture());
  await page.waitForFunction(() => window.__providerRequests.length === 4);
  assert.equal(await page.evaluate(() => window.__queueFixture("console-guide-fixture", "guide fixture text")), "queued");
  const guideId = await page.evaluate(() => window.__queuedTurns().find((turn) => turn.id === "console-guide-fixture")?.id);
  assert.equal(await page.evaluate((id) => window.__steerFixtureQueued(id), guideId), "steered", "Guide cancels the durable queue entry and dispatches one immediate steer");
  const guideDispatch = await page.evaluate(() => window.__queueRequests.at(-1));
  assert.equal(guideDispatch.targetClientTurnId, "active-turn-fixture");
  const guidedTurnId = guideDispatch.clientTurnId;
  await page.evaluate((clientTurnId) => window.__resolveProvider({
    ok: true, status: "completed", sessionId: "session-fixture", turnId: "guided-active-turn",
    consumedSteerInputIds: [clientTurnId],
    plan: { reply: "guided fixture done", summary: "guided fixture done", nextStep: "done", planner: "fixture", shellNeeded: false },
    timeline: [],
  }), guidedTurnId);
  await page.evaluate(() => window.__activeRunPromise);
  const guidedBubble = await page.evaluate((id) => window.__chat.items.find((item) => item.clientTurnId === id), guidedTurnId);
  assert.equal(guidedBubble.composerQueued, undefined);
  assert.equal(guidedBubble.queueStatus, undefined);

  await page.evaluate(() => window.__startFixture());
  await page.waitForFunction(() => window.__providerRequests.length === 5);
  assert.equal(await page.evaluate(() => window.__queueFixture("pending-question-queue", "wait for answer")), "queued");
  await page.evaluate(() => {
    window.__agentQuestions = [{ questionId: "question-fixture", status: "pending", sessionId: "session-fixture" }];
    window.__setQuestionActivity(true);
  });
  await page.evaluate(() => window.__resolveProvider({
    ok: true, status: "completed", sessionId: "session-fixture", turnId: "question-turn",
    plan: { reply: "A question is pending", summary: "pending question", nextStep: "needs_user_action", planner: "fixture", shellNeeded: false },
    timeline: [],
  }));
  await page.evaluate(() => window.__activeRunPromise);
  await page.waitForTimeout(100);
  assert.equal(await page.evaluate(() => window.__providerRequests.length), 5, "queued follow-up waits while the same session has an unanswered question");
  assert.equal(await page.evaluate(() => window.__queuedTurns().some((turn) => turn.id === "pending-question-queue")), true, "blocked follow-up remains queued");
  assert.deepEqual(await page.evaluate(() => window.__questionBlockedDispatches), [], "no follow-up reaches a conversation with a pending tool call");
  await page.evaluate(() => {
    window.__agentQuestions = [{
      questionId: "question-fixture", status: "answered", sessionId: "session-fixture",
      runtimeContinuation: { status: "running", sessionId: "session-fixture" },
    }];
  });
  await page.evaluate(() => window.__controller.resumeQueuedTurns());
  await page.waitForTimeout(60);
  assert.equal(await page.evaluate(() => window.__providerRequests.length), 5, "answering the question does not release the queue while its continuation is running");
  await page.evaluate(() => {
    window.__agentQuestions = [{
      questionId: "question-fixture", status: "answered", sessionId: "session-fixture",
      runtimeContinuation: { status: "completed", sessionId: "session-fixture" },
    }];
    window.__setQuestionActivity(false);
  });
  await page.evaluate(() => window.__controller.resumeQueuedTurns());
  await page.waitForFunction(() => window.__providerRequests.length === 6);
  assert.equal(await page.evaluate(() => window.__providerRequests.at(-1).followupQueueId), "queue-pending-question-queue");
  await page.evaluate(() => window.__resolveProvider({
    ok: true, status: "completed", sessionId: "session-fixture", turnId: "after-question-turn",
    plan: { reply: "Follow-up resumed after question settled", summary: "resumed", nextStep: "done", planner: "fixture", shellNeeded: false },
    timeline: [],
  }));
  await page.waitForFunction(() => !window.__queuedTurns().some((turn) => turn.id === "pending-question-queue"));
  assert.deepEqual(await page.evaluate(() => window.__questionBlockedDispatches), []);

  await page.waitForFunction(() => window.__isRunning() === false);
  await page.evaluate(() => {
    window.__agentQuestions = [{ questionId: "idle-question", status: "pending", sessionId: "session-fixture" }];
    window.__setQuestionActivity(true);
    window.__failNextPersist();
  });
  assert.equal(await page.evaluate(() => window.__queueFixture("idle-question-queue", "idle wait for answer")), "not_accepted", "a failed transcript snapshot blocks durable queue admission");
  await page.waitForFunction(() => window.__traceEvents.some((event) => event.stage === "persist_rejected" && event.requestedDeliveryMode === "queue"));
  const failedSnapshotQueue = await page.evaluate(() => ({
    queued: window.__queuedTurns().find((turn) => turn.id === "idle-question-queue"),
    item: window.__chat.items.find((item) => item.clientTurnId === "idle-question-queue"),
    stages: window.__traceEvents.filter((event) => event.requestedDeliveryMode === "queue" && event.clientTurnId === "idle-question-queue").map((event) => event.stage),
  }));
  assert.equal(failedSnapshotQueue.queued, undefined, "failed snapshot leaves no durable queue entry");
  assert.equal(failedSnapshotQueue.item, undefined, "failed snapshot removes the unsent queue bubble");
  assert.equal(failedSnapshotQueue.stages.includes("rpc_ack"), false, "runtime admission is skipped after a failed transcript snapshot");
  await page.waitForTimeout(80);
  assert.equal(await page.evaluate(() => window.__providerRequests.length), 6, "idle enqueue also waits for a same-session pending question");

  await page.evaluate(() => {
    window.__agentQuestions = [{ questionId: "question-fixture", status: "answered", sessionId: "session-fixture", runtimeContinuation: { status: "running", clientTurnId: "question-turn", sessionId: "session-fixture" } }];
    window.__setQuestionActivity(true);
  });
  const requestsBeforeContinuationSteer = await page.evaluate(() => window.__providerRequests.length);
  await page.evaluate(() => window.__submitFixtureSteer());
  await page.evaluate(() => window.__steerPromise);
  assert.equal(await page.evaluate(() => window.__queueRequests.at(-1).targetClientTurnId), "question-turn", "CtrlEnter during a question continuation targets its actual runtime turn");
  assert.equal(await page.evaluate(() => window.__providerRequests.length), requestsBeforeContinuationSteer, "question steering never starts another native turn");
  await page.evaluate(() => { window.__agentQuestions = []; window.__setQuestionActivity(false); });

  await page.evaluate(() => {
    window.__agentQuestions = [{ questionId: "restore-question", status: "pending", sessionId: "session-fixture" }];
    window.__recoveryQueue = [{
      queueId: "queue-idle-question-queue", sequence: 1, sessionId: "chat-fixture",
      runtimeSessionId: "session-fixture", clientTurnId: "idle-question-queue",
      message: "idle wait for answer", attachments: [], status: "pending",
      provider: "fixture", providerLabel: "Fixture", model: "fixture-model",
    }];
    window.__chat.items = window.__chat.items.filter((item) => item.clientTurnId !== "idle-question-queue");
    window.__mountRestoredController([{
      id: "question-continuation:restore-question", type: "streaming", clientTurnId: "question-turn", text: "",
    }]);
  });
  await page.waitForFunction(() => window.__queuedTurns().some((turn) => turn.id === "idle-question-queue"));
  await page.waitForTimeout(80);
  assert.equal(await page.evaluate(() => window.__providerRequests.length), 6, "reload recovery keeps a durable follow-up parked while its question is pending");
  assert.equal(await page.evaluate(() => window.__queueRequests.filter((request) => request.clientTurnId === "idle-question-queue").length), 0, "refresh recovery does not dispatch the parked clientTurnId while its question is pending");
  assert.equal(await page.evaluate(() => window.__chat.items.filter((item) => item.clientTurnId === "idle-question-queue").length), 1, "recovery recreates one visible queued item when the transcript snapshot missed it");
  await page.evaluate(() => window.__mountRestoredController([
    window.__chat.items.find((item) => item.clientTurnId === "idle-question-queue"),
    { id: "question-continuation:restore-question", type: "streaming", clientTurnId: "question-turn", text: "" },
  ]));
  await page.waitForTimeout(80);
  assert.equal(await page.evaluate(() => window.__chat.items.filter((item) => item.clientTurnId === "idle-question-queue").length), 1, "merging the transcript and durable queue by clientTurnId does not duplicate the pending bubble");
  await page.evaluate(() => {
    window.__agentQuestions = [{
      questionId: "restore-question", status: "answered", sessionId: "session-fixture",
      runtimeContinuation: { status: "running", sessionId: "session-fixture" },
    }];
  });
  await page.evaluate(() => window.__controller.resumeQueuedTurns());
  await page.waitForTimeout(50);
  assert.equal(await page.evaluate(() => window.__providerRequests.length), 6, "reload recovery also waits while an answered question continuation is still running");
  await page.evaluate(() => {
    window.__agentQuestions = [{
      questionId: "restore-question", status: "answered", sessionId: "session-fixture",
      runtimeContinuation: { status: "completed", sessionId: "session-fixture" },
    }];
  });
  await page.evaluate(() => window.__controller.resumeQueuedTurns());
  await page.waitForFunction(() => window.__providerRequests.length === 7);
  assert.equal(await page.evaluate(() => window.__providerRequests.at(-1).followupQueueId), "queue-idle-question-queue");
  await page.evaluate(() => window.__resolveProvider({
    ok: true, status: "completed", sessionId: "session-fixture", turnId: "restored-resumed-turn",
    plan: { reply: "Restored queue resumed", summary: "resumed", nextStep: "done", planner: "fixture", shellNeeded: false },
    timeline: [],
  }));
  console.log("steer dispatch hook promise-boundary replay: ok");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
