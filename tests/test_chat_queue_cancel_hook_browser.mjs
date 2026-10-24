import assert from "node:assert/strict";
import { createServer } from "node:http";
import { build } from "esbuild";
import { createRequire } from "node:module";

const { chromium } = createRequire(import.meta.url)(
  process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright",
);
const apiStub = `
export const cancelAgentRunFollowup = async (_endpoint, queueId) => { window.__cancelCalls.push(queueId); if(window.__cancelFailure) throw new Error("cancel transport failed"); return {ok:true,queueId,status:window.__cancelStatus||"cancelled"}; };
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
window.__traceEvents = []; window.__cancelCalls=[]; window.__cancelFailure=false;
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
window.__editFixtureQueued = () => window.__controller.editQueuedTurn("console-queued-fixture");
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
  await new Promise(r=>server.listen(0,"127.0.0.1",r));
  browser=await chromium.launch({headless:true,channel:"msedge"});const page=await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}`);await page.locator('#hook-mounted').waitFor({state:'attached'});
  await page.evaluate(()=>window.__startFixture());await page.waitForTimeout(40);await page.evaluate(()=>window.__releasePersist());
  await page.waitForFunction(()=>window.__providerRequests.length===1);
  await page.evaluate(()=>window.__queueFixture('cancel-failed','retain this message'));
  await page.evaluate(async()=>{window.__cancelFailure=true;await window.__controller.cancelQueuedTurns();});
  let queue=await page.evaluate(()=>window.__controller.queued);
  assert.equal(queue.length,1,'failed cancellation must retain visible pending message');
  assert.notEqual(queue[0].queueStatus,'cancelled');
  assert.equal(await page.evaluate(()=>window.__durableQueue.find(x=>x.clientTurnId==='cancel-failed').status),'pending');
  await page.evaluate(async()=>{window.__cancelFailure=false;window.__cancelStatus='claimed';await window.__controller.cancelQueuedTurns();});
  assert.equal(await page.evaluate(()=>window.__controller.queued.length),1,'claimed is not cancellation acknowledgement');
  await page.evaluate(async()=>{window.__cancelStatus='cancelled';await window.__controller.cancelQueuedTurns();});
  assert.equal(await page.evaluate(()=>window.__controller.queued.length),0,'confirmed cancellation removes only acknowledged message');
  assert.equal(await page.evaluate(()=>window.__providerRequests.length),1,'cancellation never dispatches another writer');
  console.log('PASS failed and claimed cancel receipts preserve queue; confirmed cancel removes; no extra dispatch');
} finally {await browser?.close();await new Promise(r=>server.close(r));}
