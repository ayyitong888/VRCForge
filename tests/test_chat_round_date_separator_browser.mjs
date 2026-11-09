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
const taskReplayPath = process.env.VRCFORGE_TASK_REPLAY_PATH;
const taskReplay = taskReplayPath ? JSON.parse(readFileSync(taskReplayPath, "utf8")) : null;
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
import { projectQuestionContinuationActivity } from "./src/hooks/use-runtime-turn-continuation";
import { initializeI18n } from "./src/i18n";
import { questionAnswerItem } from "./src/lib/question-answer";
const noop = () => {};
function Fixture() {
  const [conversation, setConversation] = useState([]);
  const [questions, setQuestions] = useState([]);
  const [dismissedQuestionIds, setDismissedQuestionIds] = useState(new Set());
  const [runs, setRuns] = useState([]);
  const [draft, setDraft] = useState("");
  const conversationEndRef = useRef(null);
  window.setConversation = setConversation;
  window.setQuestions = setQuestions;
  window.setRuns = setRuns;
  const sessionId = "question-replay";
  const chat = projectQuestionContinuationActivity({ id: "fixture", sessionId, items: conversation }, questions, runs);
  return <ChatWorkspace
    projectPromptTitle="New task welcome"
    conversationPending={false}
    conversationPendingMessage="Loading selected conversation…"
    input={draft} setInput={setDraft} sending={false} queueAllowed={false}
    onSubmit={noop} onSwitchMode={noop} commands={[]} actions={[]} onAction={noop} disabledReason=""
    attachments={[]} onAttachFiles={noop} onRemoveAttachment={noop}
    providerLabel="" model="" activeGoal={null} onGoalChanged={noop} goalEndpoint=""
    projects={[]} onBindProject={noop} conversation={chat.items} queued={[]}
    agentQuestions={questions} backgroundGoalDeliveries={[]} backgroundGoalProviderWarnings={[]}
    onBackgroundGoalCatchUpRendered={noop} onBackgroundGoalProviderWarningsRendered={noop}
    onBackgroundGoalCatchUpDismiss={noop} dismissedQuestionIds={dismissedQuestionIds}
    onDismissQuestion={(id) => setDismissedQuestionIds(new Set([id]))}
    onReopenQuestion={() => setDismissedQuestionIds(new Set())}
    onAnswerQuestion={(id, option, value) => {
      const answered = { ...questions.find((question) => question.questionId === id), status: "answered", selectedOptionId: option };
      setQuestions([answered]);
      setConversation((items) => [...items, questionAnswerItem(answered, value)]);
    }}
    conversationEndRef={conversationEndRef} onConversationMouseUp={noop} onConversationScroll={noop}
    showScrollToBottom={false} onScrollToBottom={noop} pendingApprovalForResponse={() => null}
    approvalActions={{}} latestRetryableItemId="" latestEditableUserItemId=""
    editingItemId="" editingText="" editingAttachments={[]} onEditItemChangeText={noop}
    onEditItemRemoveAttachment={noop} onCopyItem={noop} onRetryItem={noop} onEditItem={noop}
    onEditItemSave={noop} onEditItemCancel={noop} scopedPendingApprovals={[]}
    onApprove={noop} onReject={noop} onModifyApproval={noop} onOpenDoctor={noop}
    runtimeRuns={runs} runtimeSessionId={sessionId} onSaveOperationAsSkill={noop}
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
  await render([
    { ...user("markdown-user", start), text: "Inspect **the project** and `Renderer`." },
    {
      id: "markdown-answer", type: "question_answer", questionId: "markdown-question",
      question: "Review **this project**.\n\n1. Check the editor.\n2. Verify `Renderer`.",
      text: "I checked `Renderer`.\n\n- The editor is open.",
    },
  ]);
  const userMarkdown = page.locator('[data-conversation-item-id="markdown-user"] [data-user-message-bubble]');
  assert.equal(await userMarkdown.locator("strong").innerText(), "the project");
  assert.equal(await userMarkdown.locator("code").innerText(), "Renderer");
  const answerQuestion = page.locator("[data-question-answer-question]");
  assert.equal(await answerQuestion.isVisible(), false, "a question after an earlier message starts folded");
  await page.locator("[data-question-answer-quote] summary").click();
  assert.doesNotMatch(await answerQuestion.innerText(), /\*\*/, "the question does not expose synthetic emphasis delimiters");
  assert.equal(await answerQuestion.locator("strong").innerText(), "this project");
  assert.equal(await answerQuestion.locator("ol > li").count(), 2, "the quoted question retains its ordered list");
  assert.equal(await answerQuestion.locator("ol code").innerText(), "Renderer");
  const answerValue = page.locator("[data-question-answer-value]");
  assert.equal(await answerValue.locator("code").innerText(), "Renderer");
  assert.equal(await answerValue.locator("ul > li").innerText(), "The editor is open.");
  assert.ok(await answerValue.locator("ul > li").evaluate((node) => Number(getComputedStyle(node).fontWeight) < 600));
  if (process.env.VRCFORGE_TASK_REPLAY_ONLY !== "1") {
  const referencePrompt = "示例 Question：查看头像截图时，你想先看哪一部分？";
  const referenceTurn = agent(referencePrompt, later, "needs_user_action", "first");
  referenceTurn.response.model = "fixture-question-model";
  referenceTurn.response.timeline = [{
    id: "reference-reply", timestamp: later, sequence: 1, kind: "assistant",
    payload: { summary: referencePrompt, status: "done", phase: "final_answer" },
  }];
  await render([user("first", start), referenceTurn]);
  await page.clock.install();
  await page.evaluate((prompt) => window.setQuestions([{
    questionId: "reference-q", status: "pending", originClientTurnId: "first", question: prompt,
    options: ["脸部细节", "全身效果", "侧面轮廓"].map((label) => ({ id: label, label })),
  }]), referencePrompt);
  const questionDock = page.locator("[data-chat-question-dock]");
  const questionEntry = page.locator('[data-chat-answer-question="reference-q"]');
  await questionDock.waitFor();
  assert.equal(await questionEntry.locator("xpath=ancestor::*[@data-conversation-item-id]").getAttribute("data-conversation-item-id"), referencePrompt);
  assert.equal(await questionEntry.evaluate((node) => node.parentElement.previousElementSibling?.textContent), referencePrompt,
    "the timeline action sits immediately below the question text");
  const modelLabel = page.getByText("fixture-question-model", { exact: true });
  assert.ok((await questionEntry.boundingBox()).y < (await modelLabel.boundingBox()).y, "the question action precedes model metadata");
  assert.ok(await questionDock.locator("xpath=following-sibling::*").count(), "question card sits above the independent composer");
  await page.clock.runFor(30_000);
  assert.equal(await questionDock.count(), 0, "idle collapse leaves no duplicate entry above the composer");
  assert.equal(await questionEntry.count(), 1, "the inline action remains available after idle collapse");
  await questionEntry.click();
  await questionDock.getByRole("button", { name: /close|关闭/i }).click();
  assert.equal(await questionDock.count(), 0, "close hides the dock card");
  await questionEntry.click();
  await questionDock.getByRole("button", { name: /脸部细节/ }).click();
  const referenceAnswer = page.locator('[data-question-answer="reference-q"]');
  await referenceAnswer.waitFor();
  assert.equal(await questionEntry.count(), 0, "the answered question leaves no pending action");
  assert.equal(await questionDock.count(), 0);
  assert.equal(await referenceAnswer.locator("[data-chat-question-prompt]").count(), 0, "the original assistant prompt is kept in its original turn without duplication");
  const referenceQuote = referenceAnswer.locator("[data-question-answer-question]");
  assert.equal(await referenceQuote.isVisible(), false, "a question after the opening message folds when answered");
  assert.equal(await referenceAnswer.locator("[data-question-answer-value]").innerText(), "脸部细节");
  assert.equal(await referenceAnswer.locator("[data-question-answer-value]").isVisible(), true);
  await referenceAnswer.locator("[data-question-answer-quote] summary").click();
  assert.equal(await referenceQuote.innerText(), referencePrompt);
  assert.equal(await referenceQuote.isVisible(), true);
  await referenceAnswer.locator("[data-question-answer-quote] summary").click();
  assert.equal(await referenceQuote.isVisible(), false);
  await page.evaluate(() => window.setQuestions([]));
  const historicalTurn = agent("historic final reply", later, "done", "first");
  historicalTurn.response.timeline = [{
    id: "historic-question", timestamp: start, sequence: 1, kind: "assistant",
    payload: { summary: referencePrompt, status: "done", phase: "commentary" },
  }];
  await render([user("first", start), historicalTurn,
    { id: "historic-answer", type: "question_answer", questionId: "historic-q", question: referencePrompt, text: "脸部细节" },
    { id: "historic-answer-2", type: "question_answer", questionId: "historic-q", question: referencePrompt, text: "侧面轮廓" },
  ]);
  assert.equal(await page.locator('[data-chat-question-prompt="historic-q"]').count(), 0, "a prompt inside an older run is not recreated for each answer");
  assert.equal(await page.locator('[data-question-answer="historic-q"] [data-user-message-bubble]').count(), 2);

  const openingAnswer = { id: "opening-answer", type: "question_answer", questionId: "opening-q", question: referencePrompt, text: "脸部细节" };
  await render([agent(referencePrompt, later, "done", "opening"), openingAnswer]);
  assert.equal(await page.locator('[data-question-answer="opening-q"] [data-question-answer-question]').isVisible(), true,
    "the opening question keeps its quotation expanded");

  for (const phase of ["preparing", "waiting_for_model", "running_tool", "waiting_for_approval", "verifying"]) {
    await render([{ ...stream("status-spacing", start), phase, text: "Complete the requested editor action, then reply so the inspection can continue." }]);
    const body = await page.locator("[data-conversation-streaming-turn]").boundingBox();
    const status = await page.locator("[data-chat-current-activity]").boundingBox();
    const gap = status.y - body.y - body.height;
    assert.ok(gap >= 16 && gap <= 32, `${phase}: current activity needs breathing room below the preceding reply (gap=${gap})`);
  }
  await render([user("first", start), stream("running", nextDay, "first")]);
  assert.equal(await separators.count(), 0, "first task and its long-running model request have no between-task separator");
  await render([user("first", start), agent("pending", later, "needs_user_action", "first"),
    { id: "answer", type: "question_answer", questionId: "question", text: "continue" }, stream("question-continuation", nextDay)]);
  assert.equal(await separators.count(), 0, "question answer and continuation remain the unfinished task even across dates");
  const questionAnswer = { id: "visual-answer", type: "question_answer", questionId: "visual-q", text: "The left shoulder still clips." };
  const visualQuestion = {
    questionId: "visual-q", kind: "visual_checkpoint", status: "answered", sessionId: "question-replay",
    questionFeedbackRevision: 2, runtimeContinuationStatus: "claimed",
  };
  const visualCid = "original:question:visual-q:feedback:2";
  const liveVisualRun = { sessionId: visualQuestion.sessionId, clientTurnId: visualCid, turnId: "visual-runtime", status: "running", startedAt: nextDay };
  const activity = page.locator("[data-chat-current-activity]");
  await render([questionAnswer]);
  await page.evaluate(({ question, cid }) => {
    window.setQuestions([{
      ...question, runtimeContinuationStatus: "queued",
      runtimeContinuation: { sessionId: question.sessionId, clientTurnId: cid, status: "queued" },
    }]);
  }, { question: visualQuestion, cid: visualCid });
  await activity.waitFor({ timeout: 3000 });
  assert.match(await activity.innerText(), /Preparing/i, "an admitted answer shows preparation before the runtime starts");
  await page.evaluate(({ question, run }) => {
    window.setQuestions([question]);
    window.setRuns([run]);
  }, { question: visualQuestion, run: liveVisualRun });
  await activity.waitFor({ timeout: 3000 });
  assert.match(await activity.innerText(), /Waiting for model/i, "a restored visual continuation shows real model activity");
  assert.equal(await activity.count(), 1, "a question continuation uses one activity row");
  await page.evaluate(run => window.setRuns([{ ...run, status: "completed", nextStep: "done" }]), liveVisualRun);
  await activity.waitFor({ state: "detached", timeout: 3000 });
  await page.evaluate(question => {
    window.setQuestions([{ ...question, runtimeContinuationStatus: "delivered", runtimeContinuationClientTurnId: "original:question:visual-q:feedback:2" }]);
    window.setRuns([]);
  }, visualQuestion);
  await render([questionAnswer, stream("question-continuation:" + visualCid, nextDay, visualCid)]);
  assert.equal(await activity.count(), 0, "a stale live row cannot outrank a settled answer when its run leaves the snapshot");
  await page.evaluate(() => window.setQuestions([]));
  await render([user("first", start), agent("approval", later, "approval", "first"),
    { id: "approval-result", type: "result", approvalId: "approval", createdAt: nextDay }, stream("approval-continuation", nextDay)]);
  assert.equal(await separators.count(), 0, "approval and result do not create a new task boundary");
  await render([user("first", start), { ...agent("interrupted", later, "interrupted", "first"),
    response: { ...agent("interrupted", later, "interrupted", "first").response, continuationSource: "foreground_interrupted", status: "interrupted" } },
    user("followup-after-transport", nextDay), stream("resumed-after-transport", nextDay, "followup-after-transport")]);
  assert.equal(await separators.count(), 0, "transport interruption does not finish the logical Agent task");
  await render([user("first", start), agent("failed", later, "failed", "first"), user("followup-after-failure", nextDay)]);
  assert.equal(await separators.count(), 0, "runtime failure is not Agent task completion");
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
  }
  if (taskReplay) {
    await page.evaluate((questions) => window.setQuestions(questions), taskReplay.questions);
    await render(taskReplay.items);
    const humanAnswer = page.locator("[data-question-answer]");
    assert.equal(await humanAnswer.locator("[data-user-message-bubble]").count(), 1, "human answer is one user bubble within the existing task");
    const fullQuestion = humanAnswer.locator("[data-question-answer-question]");
    assert.equal(await fullQuestion.count(), 1, "the complete original question is quoted on the first line of the answer bubble");
    assert.equal(await fullQuestion.innerText(), taskReplay.questions[0].question, "legacy answer uses the full exact-qid question without truncating to its header");
    assert.equal(await humanAnswer.locator("[data-question-answer-value] strong").count(), 0, "the answer's ordinary text is not made bold with the question");
    assert.notEqual(await fullQuestion.evaluate((node) => getComputedStyle(node.querySelector(".chat-markdown")).color), await humanAnswer.locator("[data-question-answer-value]").evaluate((node) => getComputedStyle(node.querySelector(".chat-markdown")).color), "the quoted question is visually secondary");
    assert.ok(await humanAnswer.locator("[data-question-answer-value]").evaluate((node) => Number(getComputedStyle(node).fontWeight) < 600), "the answer retains ordinary font weight");
    if (!(await fullQuestion.isVisible())) await humanAnswer.locator("[data-question-answer-quote] summary").click();
    const questionBounds = await humanAnswer.locator("[data-question-answer-question]").boundingBox();
    const answerValueBounds = await humanAnswer.locator("[data-question-answer-value]").boundingBox();
    assert.ok(questionBounds.y + questionBounds.height <= answerValueBounds.y, "the complete question occupies the line above the answer");
    assert.ok((await humanAnswer.locator("[data-question-answer-value]").innerText()).includes(taskReplay.questions[0].answer.slice(0, 20)), "the next line retains the actual answer");
    const answerBlock = page.locator("[data-conversation-item-id]").filter({ has: page.locator("[data-question-answer]") });
    const continuationBlock = page.locator("[data-conversation-item-id]").filter({ has: page.locator("[data-conversation-streaming-turn]") });
    const answerBounds = await answerBlock.boundingBox();
    const continuationBounds = await continuationBlock.boundingBox();
    assert.ok(continuationBounds.y - answerBounds.y - answerBounds.height <= 16,
      "answer and continuation use continuous task spacing instead of the gap reserved for a new task");
    const tasks = page.locator("[data-chat-task]");
    const taskOwners = await tasks.evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-chat-task")));
    assert.equal(new Set(taskOwners).size, 1, "original question, answer, and exact runtime continuation stay in one visible logical task");
    assert.equal(taskOwners.length, 3, "original run, answer, and continuation retain their separate runtime presentation");
    assert.equal(await tasks.locator("[data-vrcforge-agent-client-turn]").count(), 1, "original asking run remains inside its logical task");
    assert.equal(await tasks.locator("[data-question-answer]").count(), 1, "actual user answer stays visible in the same logical task");
    assert.equal(await tasks.locator("[data-conversation-streaming-turn]").count(), 1, "the actual question continuation does not start a second task card");
    assert.equal(await separators.count(), 0, "the actual answer does not insert a completed-task separator");
    assert.ok((await page.locator("main").innerText()).includes(taskReplay.questions[0].answer.slice(0, 20)), "actual answer content is retained");
    assert.equal(await tasks.locator("[data-turn-initial-user]").count(), 1, "answering does not duplicate the original user prompt");
    assert.equal(await page.locator("[data-chat-task-continuation]").count(), 2, "answer and follow-on run keep visibly continuous task spacing");
    if (process.env.VRCFORGE_TASK_EVIDENCE_DIR) {
      await page.locator("main").screenshot({path:process.env.VRCFORGE_TASK_EVIDENCE_DIR + "/question-task-continuation.png"});
    }
  }
  assert.deepEqual(errors, [], "real ChatWorkspace renders without browser errors");
  console.log("PASS full-task date boundaries, question/approval continuation, unknown timestamps, and canonical replay");
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolveClose) => server.close(resolveClose));
}
