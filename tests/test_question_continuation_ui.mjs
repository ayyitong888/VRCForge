import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const temp = path.join(root, "tests/.tmp-question-ui");
await fs.rm(temp, { recursive: true, force: true });
await fs.mkdir(temp, { recursive: true });
const hookOutput = path.join(temp, "runtime-continuation.mjs");
await build({ entryPoints: [path.join(root, "src/hooks/use-runtime-turn-continuation.ts")], outfile: hookOutput, bundle: true, format: "esm", platform: "node", external: ["react"] });
const continuation = await import(`${pathToFileURL(hookOutput).href}?logic=question-continuation`);
const appSource = await fs.readFile(path.join(root, "src/App.tsx"), "utf8");
assert.match(appSource, /queueAllowed=\{\(chatRunSending \|\| pendingQuestionSessionIds\.has\(activeChat\?\.sessionId \|\| ""\)\) && !stopRequested\}/, "question continuations allow the same durable Enter queue as foreground turns");
const { applyQuestionContinuationDelta, projectAgentQuestionContinuation, projectQuestionContinuationActivity, questionContinuationCancelTarget, questionContinuationDisplay, questionContinuationStatus, responseFromRuntimeTurnEvent } = continuation;
const workspaceSource = await fs.readFile(path.join(root, "src/components/chat/chat-workspace.tsx"), "utf8");
const cardSource = await fs.readFile(path.join(root, "src/components/chat/conversation-card.tsx"), "utf8");
assert.match(workspaceSource, /activeStreamingItems\.map\(\(item\) => \(\s*<StreamingPhaseStatus/);
assert.doesNotMatch(cardSource, /<StreamingPhaseStatus item=\{item\}/, "live phase status is not left underneath the empty continuation card");

assert.equal(questionContinuationDisplay({ status: "answered", runtimeContinuation: { status: "queued" } }), "active");
assert.deepEqual(
  questionContinuationCancelTarget({ sessionId: "session", runtimeContinuation: { turnId: "turn", clientTurnId: "client" } }),
  { sessionId: "session", turnId: "turn", clientTurnId: "client" },
);
assert.equal(questionContinuationCancelTarget({ sessionId: "session", runtimeContinuation: { status: "queued" } }), null);
assert.equal(questionContinuationStatus("delivered", "needs_user_action"), "blocked");
assert.equal(questionContinuationStatus("delivered", "cancel_requested"), "cancelling");
assert.equal(questionContinuationStatus("delivered"), "delivered");
const responseWithActualModel = responseFromRuntimeTurnEvent({ sessionId: "s", turnId: "t", model: "provider-confirmed-model", plan: { reply: "Done" } });
assert.equal(responseWithActualModel.model, "provider-confirmed-model", "final summary carries only the backend-reported actual model");
const responseWithoutActualModel = responseFromRuntimeTurnEvent({ sessionId: "s", turnId: "t", plan: { reply: "Done" } });
assert.equal(responseWithoutActualModel.model, undefined, "missing model evidence is not guessed from provider settings");
const projected = projectAgentQuestionContinuation(
  { questionId: "q-run", sessionId: "s", status: "answered", runtimeContinuationStatus: "claimed" },
  [{ sessionId: "s", turnId: "t", clientTurnId: "c:question:q-run", status: "running" }],
);
assert.equal(projected.runtimeContinuation?.status, "running");
assert.equal(projected.runtimeContinuation?.clientTurnId, "c:question:q-run");

// Replay the production trace's blocked -> answered/running -> terminal lifecycle.
const replayChat = {
  id: "chat-low-outfit", sessionId: "session-turn-1792377836765-342daa27c80c58", title: "Outfit", projectPath: "", items: [
    { id: "question-answer-q-live", type: "user", text: "Use the low outfit" },
  ],
};
const replayQuestion = {
  questionId: "q-live", sessionId: replayChat.sessionId, status: "answered", runtimeContinuationStatus: "delivered",
  runtimeContinuation: { status: "blocked", sessionId: replayChat.sessionId, clientTurnId: "run-live:question:q-live" },
};
const blockedRun = [{ sessionId: replayChat.sessionId, turnId: "turn-blocked", clientTurnId: "run-live:question:q-live", status: "blocked", nextStep: "pending_approval" }];
assert.equal(projectQuestionContinuationActivity(replayChat, [replayQuestion], blockedRun).items.some((item) => item.type === "streaming"), false);
const runningQuestion = { ...replayQuestion, runtimeContinuation: { ...replayQuestion.runtimeContinuation, status: "queued" } };
const runningRun = [{ sessionId: replayChat.sessionId, turnId: "turn-live", clientTurnId: "run-live:question:q-live", status: "running", startedAt: "2026-10-19T02:52:15.249Z", createdAt: "2026-10-19T02:52:16.200Z", lastEvent: "provider_request_metrics", providerLabel: "Test provider", model: "test-model" }];
const runningChat = projectQuestionContinuationActivity(replayChat, [runningQuestion], runningRun);
const liveActivity = runningChat.items.find((item) => item.type === "streaming");
assert.equal(liveActivity?.id, "question-continuation:run-live:question:q-live");
assert.equal(liveActivity?.clientTurnId, "run-live:question:q-live");
assert.equal(liveActivity?.providerLabel, undefined, "live continuation must not show the old run's provider signature");
assert.equal(liveActivity?.model, undefined, "live continuation must not show the old run's model signature");
assert.equal(liveActivity?.createdAt, "2026-10-19T02:52:15.249Z", "continuation activity uses the ledger-backed runtime_turn_started timestamp, not the latest run event");
const unverifiedStart = projectQuestionContinuationActivity(replayChat, [runningQuestion], [{ ...runningRun[0], startedAt: undefined }]);
assert.equal(unverifiedStart.items.find((item) => item.type === "streaming")?.createdAt, undefined, "missing runtime_turn_started evidence never fabricates elapsed time");
const queuedWithoutStart = projectQuestionContinuationActivity(replayChat, [runningQuestion], [{ ...runningRun[0], startedAt: undefined }]);
const queuedRow = queuedWithoutStart.items.find((item) => item.type === "streaming");
const queuedWithProgress = { ...queuedWithoutStart, items: queuedWithoutStart.items.map((item) => item.type === "streaming" ? { ...item, text: "kept progress", timeline: [{ id: "kept-event", sequence: 1 }] } : item) };
const startedAfterQueue = projectQuestionContinuationActivity(queuedWithProgress, [runningQuestion], runningRun);
const updatedStartedRow = startedAfterQueue.items.find((item) => item.type === "streaming");
assert.equal(updatedStartedRow?.createdAt, runningRun[0].startedAt, "later ledger start enriches an existing queued activity row");
assert.equal(updatedStartedRow?.text, "kept progress", "start enrichment preserves progress text");
assert.equal(updatedStartedRow?.timeline?.[0]?.id, "kept-event", "start enrichment preserves timeline without reordering the row");
assert.equal(startedAfterQueue.items.indexOf(updatedStartedRow), queuedWithProgress.items.indexOf(queuedWithProgress.items.find((item) => item.type === "streaming")), "same CID update preserves row position");
let deltaChat = runningChat;
assert.equal(applyQuestionContinuationDelta([deltaChat], (chatId, updater) => {
  assert.equal(chatId, replayChat.id);
  const next = updater(deltaChat);
  const changed = next !== deltaChat;
  deltaChat = next;
  return changed;
}, { type: "agentRuntimeDelta", sessionId: replayChat.sessionId, clientTurnId: liveActivity.clientTurnId, phase: "running_tool", timelineEvent: { id: "tool-1", sequence: 1, timestamp: "2026-10-19T02:52:16Z", kind: "tool_call", payload: { tool: "read_scene" } } }), true);
const deltaActivity = deltaChat.items.find((item) => item.type === "streaming");
assert.equal(deltaActivity?.phase, "running_tool");
assert.equal(deltaActivity?.timeline?.[0]?.payload.tool, "read_scene");
const terminalRun = [{ ...runningRun[0], status: "completed", nextStep: "done" }];
const terminalChat = projectQuestionContinuationActivity(deltaChat, [runningQuestion], terminalRun);
assert.equal(terminalChat.items.find((item) => item.id === liveActivity.id)?.timeline?.[0]?.payload.tool, "read_scene", "terminal snapshots preserve progress until the authoritative receipt transfers it");
// A delayed stale run snapshot after the terminal event must not resurrect the row.
const settledChat = projectQuestionContinuationActivity(deltaChat, [runningQuestion], runningRun, new Set([liveActivity.clientTurnId]));
assert.equal(settledChat.items.find((item) => item.id === liveActivity.id)?.timeline?.[0]?.payload.tool, "read_scene", "a settled flag cannot delete progress before receipt delivery");
const foregroundItem = { id: "stream-foreground", type: "streaming", clientTurnId: "foreground-turn", text: "", phase: "waiting_for_model" };
const foregroundChat = { ...replayChat, items: [...replayChat.items, foregroundItem] };
assert.equal(projectQuestionContinuationActivity(foregroundChat, [], []).items.some((item) => item.clientTurnId === "foreground-turn"), true);
for (const [runStatus, nextStep, expected] of [
  ["cancel_requested", "", "cancelling"],
  ["blocked", "pending_approval", "blocked"],
  ["completed", "done", "completed"],
  ["cancelled", "cancelled", "cancelled"],
]) {
  const actual = projectAgentQuestionContinuation(
    { questionId: "q-run", sessionId: "s", status: "answered", runtimeContinuationStatus: "delivered" },
    [{ sessionId: "s", clientTurnId: "c:question:q-run", status: runStatus, nextStep }],
  );
  assert.equal(actual.runtimeContinuation?.status, expected, `wrong projection for ${runStatus}`);
}

const mockTranslation = path.join(temp, "react-i18next.mjs");
const mockIcons = path.join(temp, "lucide-react.mjs");
const mockI18n = path.join(temp, "i18n.mjs");
const entry = path.join(temp, "entry.mjs");
const output = path.join(temp, "rendered.mjs");
await fs.writeFile(mockTranslation, 'export function useTranslation(){return {t:(key,fallback)=>typeof fallback === "string" ? fallback : key};}\n');
await fs.writeFile(mockIcons, 'export const Check=()=>null; export const ChevronLeft=()=>null; export const ChevronRight=()=>null; export const Copy=()=>null; export const FilePlus2=()=>null; export const HelpCircle=()=>null; export const Loader2=()=>null; export const MessageSquare=()=>null; export const Pencil=()=>null; export const Save=()=>null; export const RotateCcw=()=>null; export const Wrench=()=>null; export const X=()=>null;\n');
await fs.writeFile(mockI18n, 'export default { t: (key) => key };\n');
await fs.writeFile(path.join(temp, "chat-markdown.mjs"), 'export const ChatMarkdown=({text})=>text; export const ChatResourceImageContext=({children})=>children;\n');
await fs.writeFile(path.join(temp, "composer.mjs"), 'export const AttachmentStrip=()=>null;\n');
await fs.writeFile(path.join(temp, "conversation-timeline.mjs"), 'export const RunRow=()=>null; export const buildAgentTimelineRows=()=>[]; export const buildDurableTimelineRows=()=>[]; export const displayPlanner=()=>""; export const formatPayload=()=>""; export const formatRuntimeModelLine=()=>"";\n');
await fs.writeFile(path.join(temp, "badge.mjs"), 'export const Badge=()=>null;\n');
await fs.writeFile(path.join(temp, "button.mjs"), 'export const Button=()=>null;\n');
await fs.writeFile(path.join(temp, "data-line.mjs"), 'export const DataLine=()=>null;\n');
await fs.writeFile(path.join(temp, "output-block.mjs"), 'export const OutputBlock=()=>null;\n');
await fs.writeFile(entry, `
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AgentQuestionCard } from ${JSON.stringify(path.join(root, "src/components/chat/agent-question-card.tsx"))};
import { ConversationCard, StreamingPhaseStatus } from ${JSON.stringify(path.join(root, "src/components/chat/conversation-card.tsx"))};
import { projectAgentQuestionContinuation } from ${JSON.stringify(path.join(root, "src/hooks/use-runtime-turn-continuation.ts"))};
const base = { questionId: "q-1", status: "answered", question: "Approve repair?", options: [] };
const active = renderToStaticMarkup(React.createElement(AgentQuestionCard, {
  questions: [{ ...base, runtimeContinuation: { questionId: "q-1", status: "queued", sessionId: "s", clientTurnId: "c" } }],
  onAnswerQuestion: async () => {},
  onStopContinuation: () => {},
}));
if (active.includes("Continuing this task") || active.includes(">Stop<")) throw new Error("answered question card must leave the dock even while continuation runs");
const terminal = renderToStaticMarkup(React.createElement(AgentQuestionCard, {
  questions: [{ ...base, runtimeContinuation: { questionId: "q-1", status: "failed", error: "planner failed" } }],
  onAnswerQuestion: async () => {},
}));
if (terminal.includes("planner failed") || terminal.includes("Approve repair?")) throw new Error("settled continuation must leave the question card");
console.log(JSON.stringify({ activeCardHidden: true, settledCardHidden: true }));
for (const [runStatus, nextStep] of [["cancel_requested", ""]]) {
  const question = projectAgentQuestionContinuation({ ...base, sessionId: "s", runtimeContinuationStatus: "delivered" },
    [{ sessionId: "s", turnId: "t", clientTurnId: "c:question:q-1", status: runStatus, nextStep }]);
  const html = renderToStaticMarkup(React.createElement(AgentQuestionCard, { questions: [question], onAnswerQuestion: () => {}, onStopContinuation: () => {} }));
  if (html.includes("Approve repair?") || html.includes(">Stop<")) throw new Error("settled continuation must leave the question card: " + runStatus);
}
const blocked = projectAgentQuestionContinuation({ ...base, sessionId: "s", runtimeContinuationStatus: "delivered" },
  [{ sessionId: "s", turnId: "t", clientTurnId: "c:question:q-1", status: "blocked", nextStep: "pending_approval" }]);
const blockedHtml = renderToStaticMarkup(React.createElement(AgentQuestionCard, { questions: [blocked], onAnswerQuestion: () => {} }));
if (blockedHtml.includes("Approve repair?")) throw new Error("blocked settled continuation must leave the question card");
const liveCard = renderToStaticMarkup(React.createElement(StreamingPhaseStatus, { item: {
  id: "question-continuation:run-live:question:q-live", type: "streaming", clientTurnId: "run-live:question:q-live", text: "", createdAt: "2026-10-19T02:52:15.249Z",
}, nowMs: Date.parse("2026-10-19T02:52:16Z"), showElapsed: false }));
if (!liveCard.includes("data-chat-current-activity") || !liveCard.includes('role="status"')) throw new Error("active question continuation must render the bottom live activity row");
const answerCard = renderToStaticMarkup(React.createElement(ConversationCard, { attachmentPayloads: { "e2c4bf98685a8d0674e42fe055e6768d7da848691d4fa7c9dbd5b0703d9dfaf4": { payloadHash: "e2c4bf98685a8d0674e42fe055e6768d7da848691d4fa7c9dbd5b0703d9dfaf4", payloadKind: "data_url", dataUrl: "data:image/png;base64,AA==" } }, item: {
  id: "question-answer-q-live", type: "question_answer", questionId: "q-live", header: "Inspect preview", text: "The left shoe still needs review.",
  attachments: [{ id: "image-ref", name: "review.png", type: "image/png", payloadHash: "e2c4bf98685a8d0674e42fe055e6768d7da848691d4fa7c9dbd5b0703d9dfaf4" }],
} }));
if (!answerCard.includes('data-question-answer="q-live"') || !answerCard.includes("The left shoe still needs review.") || !answerCard.includes("data:image/png;base64,AA==") || answerCard.includes("data-user-message-bubble")) {
  throw new Error("ask answer must be visible as a question-linked item, separate from ordinary user turns: " + answerCard);
}
const emptyContinuationCard = renderToStaticMarkup(React.createElement(ConversationCard, { item: {
  id: "question-continuation:run-live:question:q-live", type: "streaming", clientTurnId: "run-live:question:q-live", text: "", createdAt: "2026-10-19T02:52:15.249Z", providerLabel: "Old provider", model: "old-model",
} }));
if (emptyContinuationCard) throw new Error("an empty continuation must not leave an old-time/model placeholder below the top progress area");
const terminalActivity = [];
const settledCard = terminalActivity.length ? renderToStaticMarkup(React.createElement(StreamingPhaseStatus, { item: terminalActivity[0], nowMs: Date.now(), showElapsed: false })) : "";
if (settledCard.includes("data-chat-current-activity")) throw new Error("terminal reply must not retain the bottom live activity row");
`);
try {
  await build({
    entryPoints: [entry], outfile: output, bundle: true, format: "esm", platform: "node",
    external: ["react", "react-dom/server"],
    plugins: [{ name: "question-ui-mocks", setup(plugin) {
      plugin.onResolve({ filter: /^react-i18next$/ }, () => ({ path: mockTranslation }));
      plugin.onResolve({ filter: /^lucide-react$/ }, () => ({ path: mockIcons }));
      plugin.onResolve({ filter: /\/i18n$/ }, () => ({ path: mockI18n }));
      plugin.onResolve({ filter: /\/chat-markdown$/ }, () => ({ path: path.join(temp, "chat-markdown.mjs") }));
      plugin.onResolve({ filter: /\/composer$/ }, () => ({ path: path.join(temp, "composer.mjs") }));
      plugin.onResolve({ filter: /\/conversation-timeline$/ }, () => ({ path: path.join(temp, "conversation-timeline.mjs") }));
      plugin.onResolve({ filter: /\/components\/ui\/(badge|button|data-line|output-block)$/ }, (args) => ({ path: path.join(temp, `${path.basename(args.path)}.mjs`) }));
    } }],
  });
  await import(`${pathToFileURL(output).href}?case=question-continuation`);
} finally {
  await fs.rm(temp, { recursive: true, force: true });
}
