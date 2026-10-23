import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const root = process.cwd();
const replayPath = process.env.VRCFORGE_ROUND_REPLAY_PATH;
const replay = replayPath ? JSON.parse(readFileSync(replayPath, "utf8")) : null;
const overlayPath = process.env.VRCFORGE_ROUND_OVERLAY_PATH;
const overlay = overlayPath ? JSON.parse(readFileSync(overlayPath, "utf8")) : null;
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const css = (await postcss([tailwindcss(resolve(root, "tailwind.config.ts"))]).process(
  readFileSync(resolve(root, "src/styles.css"), "utf8").replace(
    '@import "./theme.css";',
    readFileSync(resolve(root, "src/theme.css"), "utf8"),
  ),
  { from: resolve(root, "src/styles.css") },
)).css;

const bundled = await build({
  stdin: {
    resolveDir: root,
    loader: "tsx",
    contents: `
import React, { useState, useRef } from "react";
import { createRoot } from "react-dom/client";
import { ChatWorkspace } from "./src/components/chat/chat-workspace";
import { initializeI18n } from "./src/i18n";
const noop = () => {};
function Fixture() {
  const [conversation, setConversation] = useState([]);
  const [draft, setDraft] = useState("");
  const conversationEndRef = useRef(null);
  window.setConversation = setConversation;
  return <ChatWorkspace
    projectPromptTitle="New task welcome"
    conversationPending={false}
    conversationPendingMessage="Loading selected conversation…"
    input={draft} setInput={setDraft} sending={false} queueAllowed={false}
    onSubmit={noop} onSwitchMode={noop} commands={[]} actions={[]} onAction={noop} disabledReason=""
    attachments={[]} onAttachFiles={noop} onRemoveAttachment={noop}
    providerLabel="" model="" activeGoal={null} onGoalChanged={noop} goalEndpoint=""
    projects={[]} onBindProject={noop} conversation={conversation} queued={[]}
    agentQuestions={[]} backgroundGoalDeliveries={[]} backgroundGoalProviderWarnings={[]}
    onBackgroundGoalCatchUpRendered={noop} onBackgroundGoalProviderWarningsRendered={noop}
    onBackgroundGoalCatchUpDismiss={noop} onAnswerQuestion={noop}
    conversationEndRef={conversationEndRef} onConversationMouseUp={noop} onConversationScroll={noop}
    showScrollToBottom={false} onScrollToBottom={noop} pendingApprovalForResponse={() => null}
    approvalActions={{}} latestRetryableItemId="" latestEditableUserItemId=""
    editingItemId="" editingText="" editingAttachments={[]} onEditItemChangeText={noop}
    onEditItemRemoveAttachment={noop} onCopyItem={noop} onRetryItem={noop} onEditItem={noop}
    onEditItemSave={noop} onEditItemCancel={noop} scopedPendingApprovals={[]}
    onApprove={noop} onReject={noop} onModifyApproval={noop} onOpenDoctor={noop}
    runtimeRuns={[]} onSaveOperationAsSkill={noop}
    sessionHandoffEndpoint="" sessionHandoffSourceChatId="" sessionHandoffTargetChats={[]}
    handoffSendOpen={false} onHandoffSendOpenChange={noop}
  />;
}
void initializeI18n().then(() => createRoot(document.getElementById("root")).render(<Fixture />));
`,
  },
  bundle: true,
  write: false,
  format: "iife",
  jsx: "automatic",
  platform: "browser",
});

const server = createServer((_request, response) => {
  response.setHeader("Content-Type", "text/html; charset=utf-8");
  response.end(`<style>${css}</style><main id="root"></main><script>${bundled.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
});
await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));

let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage({ viewport: { width: 1100, height: 800 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  await page.goto(`http://127.0.0.1:${server.address().port}`);

  const separators = page.locator("[data-chat-date-separator]");
  const user = (id, at, turn = id) => ({ id, type: "user", text: id, clientTurnId: turn, createdAt: at });
  const agent = (id, at, nextStep = "done", turn = id) => ({
    id, type: "agent", completedAt: at,
    response: { ok: true, clientTurnId: turn, plan: { reply: id, summary: id, planner: "fixture", shellNeeded: false, nextStep }, observe: {} },
  });
  const stream = (id, at, turn = id) => ({ id, type: "streaming", clientTurnId: turn, createdAt: at, text: "", phase: "waiting_for_model" });
  const firstCompleted = "2026-10-23T12:10:00Z";
  const nextStarted = "2026-10-23T12:45:00Z";
  const start = "2026-10-23T12:00:00Z";
  const later = "2026-10-23T14:00:00Z";
  const nextDay = "2026-10-24T14:00:00Z";
  const render = async (items) => {
    await page.evaluate((value) => window.setConversation(value), items);
    await page.waitForTimeout(80);
  };
  await page.waitForFunction(() => typeof window.setConversation === "function");
  await render([user("first", start), stream("running", nextDay, "first")]);
  assert.equal(await separators.count(), 0, "first task and its long-running model request have no between-task separator");
  await render([user("first", start), agent("pending", later, "needs_user_action", "first"),
    { id: "answer", type: "question_answer", questionId: "question", text: "continue" }, stream("question-continuation", nextDay)]);
  assert.equal(await separators.count(), 0, "question answer and continuation remain the unfinished task even across dates");
  await render([user("first", start), agent("approval", later, "approval", "first"),
    { id: "approval-result", type: "result", approvalId: "approval", createdAt: nextDay }, stream("approval-continuation", nextDay)]);
  assert.equal(await separators.count(), 0, "approval and result do not create a new task boundary");
  await render([user("first", start), agent("done", later, "done", "first"), user("next", nextDay), stream("next-running", nextDay, "next")]);
  assert.equal(await separators.count(), 1, "completed task followed by next user task receives one date separator");
  const markerOwner = await separators.first().evaluate((node) => node.parentElement.getAttribute("data-conversation-item-id"));
  assert.equal(markerOwner, "next-running", "projected initial user owns boundary instead of an arbitrary assistant timestamp");

  await render([user("first", start), agent("done", later, "done", "first"), user("near", "2026-10-23T14:01:00Z")]);
  assert.equal(await separators.count(), 0, "existing quiet-period threshold is preserved");
  await render([user("first", start), agent("done", firstCompleted, "done", "first"), user("next", nextStarted), agent("next-done", nextDay, "done", "next")]);
  assert.equal(await separators.count(), 1, "marker survives settling the next task without using its completion time");
  const settledLabel = await separators.innerText();
  await render([user("first", start), agent("done", firstCompleted, "done", "first"), user("next", nextStarted), stream("next-live", nextDay, "next")]);
  assert.equal(await separators.innerText(), settledLabel, "live and durable projections show the same user-start marker");
  await render([user("first", start), agent("pending", later, "needs_user_action", "first"), user("answer-as-user", nextDay), stream("answered-running", nextDay, "answer-as-user")]);
  assert.equal(await separators.count(), 0, "legacy user-shaped question answer continues the pending task");
  await render([user("first", start), agent("done", firstCompleted, "done", "first"), stream("recovered-request", nextDay, "first")]);
  assert.equal(await separators.count(), 0, "resaved runtime request cannot become a new user task");
  const midnightEnd = new Date(2026, 9, 23, 23, 59).toISOString();
  const midnightStart = new Date(2026, 9, 24, 0, 1).toISOString();
  await render([user("first", start), agent("midnight-done", midnightEnd, "done", "first"), user("after-midnight", midnightStart)]);
  assert.equal(await separators.count(), 1, "crossing local midnight still separates completed tasks without waiting thirty minutes");
  await render([user("first", start), agent("done", undefined, "done", "first"), user("unknown-end", nextDay)]);
  assert.equal(await separators.count(), 0, "unknown prior completion time does not invent a boundary date");
  if (replay) {
    const snapshots = replay.items.filter((item) => item.type !== "streaming");
    // Replay the canonical pending question with its later continuation overlay.
    await render([...snapshots, stream("question-overlay", "2026-10-23T21:41:00Z", "question-runtime")]);
    assert.equal(await separators.count(), 0, "canonical interrupted-recovery chat and question overlay stay in one task");
    if (overlay) {
      const live = { ...stream("question-continuation:" + overlay.run.clientTurnId, overlay.run.startedAt, overlay.run.clientTurnId), timeline: overlay.timeline.events };
      await render([...snapshots, live]);
      assert.equal(await separators.count(), 0, "real question runtime events do not split the original task");
      const beforeRestore = await separators.count();
      await render(JSON.parse(JSON.stringify([...snapshots, live, { ...live, id: "runtime-activity:" + overlay.run.clientTurnId }])));
      assert.equal(await separators.count(), beforeRestore, "duplicate restore overlay cannot introduce a date marker");
    }
  }
  assert.deepEqual(errors, [], "real ChatWorkspace renders without browser errors");
  console.log("PASS full-task date boundaries, question/approval continuation, unknown timestamps, and canonical replay");
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolveClose) => server.close(resolveClose));
}
