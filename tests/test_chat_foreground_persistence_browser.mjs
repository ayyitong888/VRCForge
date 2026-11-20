import assert from "node:assert/strict";
import { createServer } from "node:http";
import { build } from "esbuild";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";

const parent = { clientTurnId: "delegating-parent", turnId: "parent-run", sessionId: "session-fixture",
  plan: { nextStep: "waiting_for_tool", reason: "sub_agent_pending" } };
const subagentReplay = process.env.VRCFORGE_SUBAGENT_PARENT_REPLAY
  ? JSON.parse(await readFile(process.env.VRCFORGE_SUBAGENT_PARENT_REPLAY, "utf8"))
  : {
    parent,
    continuation: { sessionId: parent.sessionId, clientTurnId: "resumed-parent", turnId: "resumed-run",
      parentClientTurnId: parent.clientTurnId, parentTurnId: parent.turnId, continuationSource: "sub_agent_finished" },
    lifecycle: [{ id: "worker-done", type: "timeline_event", createdAt: "2026-01-01T10:00:01Z",
      event: { id: "worker-done", kind: "subagent", clientTurnId: parent.clientTurnId, payload: { status: "completed" } } }],
    events: [{ id: "parent-tool", sequence: 1, timestamp: "2026-01-01T10:00:02Z", kind: "tool_call",
      payload: { tool: "fixture_inspect", label: "Inspect parent tool" } }],
  };

const { chromium } = createRequire(import.meta.url)(
  process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright",
);

const apiStub = `
export const cancelAgentRunFollowup = async () => ({ ok: true });
export const listAgentRunFollowups = async () => ({ ok: true, items: [] });
export const fetchAgentQuestions = async () => ({ ok: true, questions: [] });
export const issueComputerUseTurnGrant = async () => null;
export const requestAgentRunCancel = async () => ({ ok: true });
export const requestChatTitle = async () => ({ title: "Fixture" });
export const sendAgentMessage = (_endpoint, _message, session, _history, _agent, options) => {
  window.__providerRequests.push({ clientTurnId: options?.clientTurnId });
  if (window.__deferParent) return new Promise(resolve => { window.__releaseParent = resolve; });
  const override = window.__nextResponse;
  window.__nextResponse = undefined;
  return Promise.resolve({
    ok: true, status: "completed", sessionId: session, turnId: options?.clientTurnId, clientTurnId: options?.clientTurnId,
    plan: { reply: "fixture reply", summary: "fixture", nextStep: "done", planner: "fixture", shellNeeded: false },
    timeline: [],
    ...override,
  });
};
export const recordAgentRunQueued = async () => ({ ok: true, accepted: true, mode: "followup", queueId: "queue-fixture", sequence: 1 });
`;

const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
import React from "react";
import { createRoot } from "react-dom/client";
import { useRef, useState } from "react";
import { useChatRunController } from "./src/hooks/use-chat-run-controller";
import { useRuntimeTurnActivity } from "./src/hooks/use-runtime-turn-activity";
import { useRuntimeTurnContinuationDelivery } from "./src/hooks/use-runtime-turn-continuation";
import { mergeConversationTimelineItems, stripSupersededStreamingItems } from "./src/lib/chat-thread";
import { projectProcessRounds } from "./src/lib/chat-process-rounds";
import { ConversationCard } from "./src/components/chat/conversation-card";
import { ComposerLinkInput } from "./src/components/chat/composer-link-input";
import { chatInputPredictionOwner, useChatInputPrediction } from "./src/hooks/use-chat-input-prediction";

const chat = { id: "chat-fixture", sessionId: "session-fixture", projectPath: "", items: [], title: "Existing chat" };
let persistCalls = 0;
let releasePersist;
let failNextPersist = false;
const persist = () => {
  persistCalls += 1;
  window.__events.push("persist_started");
  if (failNextPersist) {
    failNextPersist = false;
    return Promise.reject(new Error("fixture chat snapshot save failed"));
  }
  if (persistCalls === 1) return new Promise((resolve) => { releasePersist = resolve; });
  return Promise.resolve();
};
function Harness() {
  const [, setChatState] = useState([chat]);
  const updateChat = React.useCallback((id, update) => {
    if (id !== chat.id) return false;
    const next = update(chat);
    if (next === chat) return false;
    Object.assign(chat, next, { items: stripSupersededStreamingItems(next.items) });
    setChatState([{ ...chat }]);
    return true;
  }, []);
  const appendToChat = React.useCallback((id, item) => updateChat(id, current => ({ ...current, items: [...current.items, item] })), [updateChat]);
  const chats = [{ ...chat }];
  const inputRef = useRef(null);
  const predictionOwner = chatInputPredictionOwner(location.origin, chat.id, chat.sessionId);
  const prediction = useChatInputPrediction({
    endpoint: location.origin, owner: predictionOwner, provider: "fixture", model: "fixture-model",
    eligible: true, busy: false, hasDraft: false,
  });
  window.__predictReply = (reply, turnId) => prediction.offer({
    owner: predictionOwner, turnId, context: { recent_user_messages: ["Apply the change"], assistant_tail: reply },
  }, window.__predictionGeneration);
  window.__dismissPrediction = () => { window.__predictionGeneration = prediction.dismiss(); };
  const deliver = useRuntimeTurnContinuationDelivery({ chats, appendToChat, updateChat });
  const delta = useRuntimeTurnActivity({ endpoint: "http://fixture", runtimeConnected: true,
    chats, runs: [], updateChat, deliver });
  const controller = useChatRunController({
    endpoint: "http://127.0.0.1:8757", runtimeConnected: true, sessionId: chat.sessionId,
    activeRuntimeProjectPath: "", getChatById: (id) => id === chat.id ? chat : undefined,
    ensureActiveChat: () => chat.id,
    updateChat,
    appendToChat,
    touchChat: (value) => value,
    startRuntime: async () => "http://127.0.0.1:8757", refresh: async () => {},
    refreshRuntimeRuns: async () => {}, refreshBackgroundGoals: () => {},
    handleRuntimeSessionFailure: () => {}, setError: (message) => window.__errors.push(message),
    persistChatsNow: persist, reportChatSaveError: (cause) => window.__storageErrors.push(cause.message), chats: [chat],
  });
  window.__controller = controller;
  window.__applyContinuationDelta = delta;
  window.__deliverContinuation = deliver;
  window.__switchToParent = (identity) => updateChat(chat.id, current => ({ ...current, sessionId: identity.sessionId, items: [] }));
  window.__appendLifecycle = (items) => updateChat(chat.id, current => ({ ...current, items: [...current.items, ...items] }));
  const projection = projectProcessRounds(mergeConversationTimelineItems(chat.items), chat.sessionId);
  return <div id="hook-mounted">
    <ComposerLinkInput inputRef={inputRef} value="" onChange={() => {}} suggestion={prediction.suggestion} />
    {window.__deferParent && projection.items.map(item => {
    const round = projection.rounds.get(item);
    return <ConversationCard key={item.id} item={item} sharedProcess={round && {
      first: round.first === item, last: round.last === item, live: round.last.type === "streaming",
      open: true, failures: round.failures, onOpenChange: () => {},
    }} />;
  })}</div>;
}
window.__events = [];
window.__errors = [];
window.__storageErrors = [];
window.__items = () => chat.items;
window.__providerRequests = [];
window.__completedReplies = [];
window.__releasePersist = () => releasePersist?.();
window.__failNextPersist = () => { failNextPersist = true; };
window.__submit = (id, text) => window.__controller.submitTurn({
  id, text, attachments: [], provider: "fixture", providerLabel: "Fixture", model: "fixture-model",
  sessionId: chat.sessionId, planMode: false,
  onCompletedReply: (reply, _sessionId, turnId) => {
    window.__completedReplies.push(turnId);
    if (turnId === "applied-auto-approval" || turnId === "waiting-for-approval") {
      window.__predictReply(reply, turnId);
    }
  },
});
createRoot(document.getElementById("root")).render(<Harness />);
`,
  },
  bundle: true,
  write: false,
  format: "iife",
  jsx: "automatic",
  platform: "browser",
  plugins: [{
    name: "foreground-persistence-test-stubs",
    setup(builder) {
      builder.onResolve({ filter: /^\.\.\/lib\/api$/ }, (args) => args.importer.endsWith("use-chat-run-controller.ts")
        ? ({ path: "app-api", namespace: "foreground-stub" }) : undefined);
      builder.onResolve({ filter: /^\.\.\/lib\/api\/agent-runtime$/ }, (args) => args.importer.endsWith("use-chat-run-controller.ts")
        ? ({ path: "agent-runtime-api", namespace: "foreground-stub" })
        : args.importer.endsWith("use-runtime-turn-activity.ts") ? ({ path: "activity-api", namespace: "foreground-stub" }) : undefined);
      builder.onLoad({ filter: /.*/, namespace: "foreground-stub" }, (args) => ({
        contents: args.path === "agent-runtime-api"
            ? "export const DEFAULT_BACKGROUND_MAX_AGENTIC_TURNS = 12;"
            : args.path === "activity-api"
              ? "export const fetchAgentRunTimeline = async () => ({ ok: false }); export const fetchAgentTurnResponse = async () => ({ ok: false });"
            : apiStub,
        loader: "js",
      }));
    },
  }],
});

const predictionRequests = [];
const server = createServer(async (req, res) => {
  if (req.method === "POST" && req.url === "/api/app/chat/input-prediction") {
    let text = "";
    for await (const chunk of req) text += chunk;
    predictionRequests.push(JSON.parse(text));
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ suggestion: "Check the result" }));
    return;
  }
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

  await page.evaluate(() => {
    void window.__submit("foreground-first", "first foreground message").then(result => { window.__firstResult = result; });
  });
  await page.waitForFunction(() => window.__events.includes("persist_started"));
  await page.waitForFunction(() => window.__firstResult, null, { timeout: 3000 });
  assert.equal(await page.evaluate(() => window.__firstResult), "started",
    JSON.stringify(await page.evaluate(() => ({ errors: window.__errors, requests: window.__providerRequests, items: window.__items() }))));
  assert.deepEqual(await page.evaluate(() => window.__providerRequests[0]), { clientTurnId: "foreground-first" });

  await page.evaluate(() => window.__failNextPersist());
  assert.equal(await page.evaluate(() => window.__submit("foreground-conflict", "continue despite the save conflict")), "started");
  await page.waitForFunction(() => window.__storageErrors.length === 1);
  assert.deepEqual(await page.evaluate(() => window.__errors), [], "saving failure does not create a generic action error");
  assert.equal(await page.evaluate(() => window.__items().filter(item => item.type === "error").length), 0, "saving failure does not become a chat send error");
  assert.deepEqual(await page.evaluate(() => window.__providerRequests.map(r => r.clientTurnId)), ["foreground-first", "foreground-conflict"]);
  assert.ok(await page.evaluate(() => window.__items().some(item => item.type === "user" && item.clientTurnId === "foreground-conflict")));
  assert.equal(await page.evaluate(() => window.__events.filter((event) => event === "persist_started").length), 2);
  await page.evaluate(() => window.__releasePersist());

  for (const [turnId, status] of [["applied-auto-approval", "completed"], ["waiting-for-approval", "blocked"]]) {
    assert.equal(await page.evaluate(async ({ turnId, status }) => {
      window.__dismissPrediction();
      window.__nextResponse = {
        status: undefined, turnStatus: { status }, approvalId: "approval-fixture", approval_id: "approval-fixture",
        write: { status: status === "completed" ? "executed" : "pending_approval", approvalId: "approval-fixture" },
      };
      return window.__submit(turnId, "Apply the change");
    }, { turnId, status }), "started");
    await page.waitForFunction(id => window.__items().some(item => item.type === "agent"
      && item.response.clientTurnId === id), turnId);
    const ghost = page.locator("[data-composer-input-suggestion]");
    if (status === "completed") {
      await ghost.waitFor({ state: "visible", timeout: 3000 });
      assert.equal(await ghost.textContent(), "Check the result");
    } else {
      assert.equal(await ghost.count(), 0);
    }
  }
  assert.equal(predictionRequests.length, 1);
  assert.deepEqual(await page.evaluate(() => window.__completedReplies),
    ["foreground-first", "foreground-conflict", "applied-auto-approval"],
    "the prediction callback accepts completed auto-approved replies and still skips turns pending approval");

  const { parent: waiting, continuation, lifecycle, events, user, terminal } = subagentReplay;
  await page.evaluate(({ waiting, user }) => {
    window.__deferParent = true;
    window.__switchToParent(waiting);
    void window.__submit(waiting.clientTurnId, user?.text || "Start delegated work");
  }, { waiting, user });
  await page.waitForFunction(() => Boolean(window.__releaseParent));
  await page.evaluate(lifecycle => window.__appendLifecycle(lifecycle), lifecycle);
  assert.ok(await page.evaluate(cid => window.__items().some(i => i.type === "streaming" && i.clientTurnId === cid), waiting.clientTurnId),
    "the child's completed lifecycle entry leaves the initial parent's live history intact");
  await page.evaluate(continuation => window.__applyContinuationDelta({ ...continuation, type: "agentRuntimeDelta", phase: "preparing" }), continuation);
  await page.waitForFunction(cid => window.__items().some(i => i.type === "streaming" && i.clientTurnId === cid), continuation.clientTurnId);
  await page.evaluate(waiting => window.__releaseParent({ ok: true, status: "parked", ...waiting }), waiting);
  await page.waitForFunction(cid => window.__items().some(i => i.type === "agent" && i.response.clientTurnId === cid), waiting.clientTurnId);
  const liveEvents = events.filter(event => event.payload.phase !== "final_answer");
  await page.evaluate(({ continuation, events }) => {
    for (const timelineEvent of events) window.__applyContinuationDelta({ ...continuation, type: "agentRuntimeDelta", timelineEvent });
  }, { continuation, events: liveEvents });
  await page.waitForFunction(cid => window.__items().some(i => i.type === "streaming" && i.clientTurnId === cid
    && i.timeline?.some(event => event.kind === "tool_call")), continuation.clientTurnId, { timeout: 3000 });
  assert.equal(await page.locator("[data-agent-waiting-for-subagent]").count(), 0);
  assert.equal(await page.locator("[data-agent-turn-process-group]").count(), 1,
    "the parent and live continuation share one visible process header");
  assert.equal(await page.locator("[data-vrcforge-live-runtime-timeline]").count(), 1);
  assert.ok(await page.locator("[data-vrcforge-live-runtime-timeline] [data-agent-tool-call-group]").count() > 0,
    "the parent's resumed tools are rendered live before terminal delivery");
  const final = terminal || { ...continuation, status: "failed",
    plan: { nextStep: "planner_failed", plannerFailure: { code: "provider_timeout" }, reply: "Provider timeout" },
    timeline: [{ id: "parent-terminal", sequence: 2, timestamp: "2026-01-01T10:00:03Z", kind: "assistant",
      payload: { phase: "final_answer", status: "planner_failed", summary: "Provider timeout" } }] };
  assert.equal(await page.evaluate(final => window.__deliverContinuation({ schema: "vrcforge.runtime_turn_event.v1", ...final }), final), true);
  await page.locator("[data-vrcforge-terminal-status=provider_timeout]").waitFor({ state: "visible", timeout: 3000 });
  assert.equal(await page.locator("[data-agent-waiting-for-subagent]").count(), 0);
  assert.equal(await page.locator("[data-vrcforge-live-runtime-timeline]").count(), 0);
  assert.equal(await page.locator("[data-agent-turn-process-group]").count(), 1);
  const accepted = await page.evaluate(cid => window.__items().find(i => i.type === "agent" && i.response.clientTurnId === cid)?.timeline?.map(e => e.id), continuation.clientTurnId);
  assert.ok(events.every(event => accepted.includes(event.id)), "terminal delivery retains the original live tool timeline");
  assert.equal(pageErrors.length, 0, pageErrors.join("\n"));
  console.log("foreground dispatch proceeds through a delayed or rejected transcript save: ok");
  console.log(`sub-agent handoff preserves parent live tools (${events.length} ${process.env.VRCFORGE_SUBAGENT_PARENT_REPLAY ? "original" : "fixture"} events)`);
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
