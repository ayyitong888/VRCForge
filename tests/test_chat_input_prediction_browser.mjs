import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React, { useState } from "react";
    import { createRoot } from "react-dom/client";
    import { chatInputPredictionOwner, predictionTurnForChat, useChatInputPrediction } from "./src/hooks/use-chat-input-prediction";
    function Chat() {
      const [state, setState] = useState({ busy: true, eligible: true, enabled: true, hasDraft: false, provider: "chatgpt", model: "first", chatId: "chat" });
      const owner = chatInputPredictionOwner(location.origin, state.chatId, "session");
      const [history, setHistory] = useState();
      const latestReply = history && predictionTurnForChat(location.origin, {
        id: state.chatId, sessionId: "session", items: [{ id: "input", type: "user", text: "Check this" }, {
          id: history.id, type: "agent", response: { ok: history.ok ?? true, turnId: history.id, turnStatus: { status: history.status },
            approvalId: history.approvalId, plan: { nextStep: history.nextStep, reply: history.reply,
              publicMessages: history.publicMessages, summary: "", completionGate: { reason: history.reason } },
          },
        }, ...(history.trailing || [])],
      }, history.questions || [], history.approvals || []);
      const prediction = useChatInputPrediction({ ...state, eligible: state.eligible && state.enabled, endpoint: location.origin, owner, latestReply });
      window.openHistory = setHistory;
      window.change = (patch) => setState(prev => ({ ...prev, ...patch }));
      window.openChat = ({ chatId, history }) => { window.change({ chatId }); setHistory(history); };
      window.start = () => { window.submission = prediction.dismiss(); window.change({ busy: true }); };
      window.type = prediction.onInputActivity;
      window.finish = (turnId) => prediction.offer({
        owner, turnId, context: {recent_user_messages: ["继续"], assistant_tail: turnId}
      }, window.submission);
      window.finishDelivered = ({ turnId, continuationSource, status, nextStep, reply = turnId }) => {
        const now = new Date().toISOString();
        return prediction.offerAfterResume({
          sessionId: "session", turnId, continuationSource, status, startedAt: now, completedAt: now,
          plan: { nextStep, reply },
        }, {
          chatId: "chat", refreshRuntime: async () => {},
          getChatById: () => ({ sessionId: "session", items: [{ id: turnId, type: "agent", response: { turnId } }] }),
        });
      };
      return <output data-state={JSON.stringify(state)}>{prediction.suggestion || ""}</output>;
    }
    createRoot(document.getElementById("root")).render(<Chat/>);
  ` },
  bundle: true, write: false, jsx: "automatic", format: "iife",
});
const requests = [];
const cancellations = [];
let finishSlow;
const server = createServer(async (req, res) => {
  if (req.method === "POST") {
    let text = "";
    for await (const chunk of req) text += chunk;
    const payload = JSON.parse(text);
    res.setHeader("Content-Type", "application/json");
    if (req.url.endsWith("/cancel")) {
      cancellations.push(payload.request_id);
      res.end("{}");
    } else {
      requests.push(payload);
      if (payload.assistant_tail === "slow") await new Promise(resolve => { finishSlow = resolve; });
      res.end(JSON.stringify({ suggestion: payload.assistant_tail === "Other chat reply." ? "另一个对话" : "继续" }));
    }
    return;
  }
  res.setHeader("Content-Type", "text/html;charset=utf-8");
  res.end('<div id="root"></div><script>' + bundle.outputFiles[0].text.replaceAll("</script", "<\\/script") + "</script>");
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
let browser;
try {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage();
  await page.goto("http://127.0.0.1:" + server.address().port);
  await page.waitForFunction(() => typeof window.start === "function");
  async function render(action, arg) {
    await page.evaluate(action, arg);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  }
  const start = () => render(() => window.start());
  const change = (patch) => render(value => window.change(value), patch);
  const finish = (id) => render(value => window.finish(value), id);
  await start();
  await change({ provider: "deepseek", model: "second" });
  await finish("completed");
  assert.equal(requests.length, 0);
  await change({ busy: false });
  await page.waitForFunction(() => document.querySelector("output").textContent === "继续");
  assert.deepEqual(requests.map(r => [r.assistant_tail, r.provider, r.model]), [["completed", "deepseek", "second"]]);
  await finish("completed");
  assert.equal(requests.length, 1);

  await start();
  await finish("user typed");
  await render(() => window.type());
  await change({ busy: false });
  assert.equal(requests.length, 1);
  await start();
  await change({ eligible: false });
  await finish("approval pending");
  await change({ busy: false, eligible: true });
  assert.equal(requests.length, 1);

  await start();
  await finish("slow");
  await Promise.all([
    page.waitForRequest(request => request.url().endsWith("/chat/input-prediction")),
    change({ busy: false }),
  ]);
  const cancelled = page.waitForResponse(response => response.url().endsWith("/input-prediction/cancel"));
  await change({ provider: "chatgpt", model: "third" });
  await cancelled;
  assert.equal(requests.length, 2);
  finishSlow();
  await render(() => {});
  assert.deepEqual(cancellations, [requests[1].request_id]);
  assert.equal(requests.length, 2);
  assert.equal(await page.locator("output").textContent(), "");
  const finishDelivered = (receipt) => render(value => window.finishDelivered(value), receipt);
  const continuationReply = "Beginning of public reply. " + "Continuation details. ".repeat(100) + "End of public reply.";
  for (const continuationSource of ["foreground_completed", "approval_finished", "shell_process_finished", "sub_agent_finished"]) {
    await start();
    const observed = page.waitForRequest(request => request.url().endsWith("/chat/input-prediction"));
    await finishDelivered({ turnId: continuationSource, continuationSource, nextStep: "done", reply: continuationReply });
    await change({ busy: false });
    assert.equal((await observed).postDataJSON().assistant_tail, continuationReply);
    await page.waitForFunction(() => document.querySelector("output").textContent === "继续");
    await finish(continuationSource);
  }
  assert.deepEqual(requests.slice(2).map(request => request.assistant_tail),
    Array(4).fill(continuationReply));
  await start();
  await finishDelivered({ turnId: "interrupted", continuationSource: "foreground_interrupted", nextStep: "done" });
  await change({ busy: false });
  assert.equal(requests.length, 6);
  assert.equal(await page.locator("output").textContent(), "");
  const restoreHistory = (response) => render(value => window.openHistory(value), response);
  const expectHistorySuggestion = async (response, expectedTail, expectedModel = "third") => {
    const request = page.waitForRequest(entry => entry.url().endsWith("/chat/input-prediction"), { timeout: 3000 });
    await restoreHistory(response);
    const payload = (await request).postDataJSON();
    assert.equal(payload.assistant_tail, expectedTail);
    assert.deepEqual(payload.recent_user_messages, ["Check this"]);
    assert.equal(payload.model, expectedModel);
    await page.waitForFunction(() => document.querySelector("output").textContent === "继续");
  };
  const restored = { id: "saved", status: "completed", nextStep: "done", reply: "Saved final reply. " + continuationReply };
  await expectHistorySuggestion(restored, restored.reply);
  const completedRequestCount = requests.length;
  await change({ eligible: false });
  assert.equal(await page.locator("output").textContent(), "");
  await change({ eligible: true });
  await page.waitForFunction(() => document.querySelector("output").textContent === "继续");
  assert.equal(requests.length, completedRequestCount, "returning from settings restores the completed prediction without asking the model again");
  await page.reload();
  await page.waitForFunction(() => typeof window.openHistory === "function");
  await restoreHistory(restored);
  await change({ busy: false, model: "third" });
  await page.waitForFunction(() => document.querySelector("output").textContent === "继续");
  assert.equal(requests.length, completedRequestCount, "reopening the app restores the cached suggestion without a new prediction");
  const openChat = (chatId, history) => render(value => window.openChat(value), { chatId, history });
  await openChat("other-chat", { ...restored, reply: "Other chat reply." });
  await page.waitForFunction(() => document.querySelector("output").textContent === "另一个对话");
  assert.equal(requests.length, completedRequestCount + 1, "another chat makes its own prediction even if its turn ID is the same");
  await openChat("chat", restored);
  await page.waitForFunction(() => document.querySelector("output").textContent === "继续");
  assert.equal(requests.length, completedRequestCount + 1, "switching back restores the original chat's result");
  await change({ eligible: false, enabled: false });
  await change({ eligible: true });
  assert.equal(await page.locator("output").textContent(), "", "a disabled setting hides a stored prediction");
  assert.equal(requests.length, completedRequestCount + 1);
  await change({ enabled: true });
  await page.waitForFunction(() => document.querySelector("output").textContent === "继续");
  assert.equal(requests.length, completedRequestCount + 1);
  const pendingApproval = { id: "pending-approval", status: "blocked", nextStep: "pending_approval", approvalId: "approval",
    approvals: [{ id: "approval", status: "pending", reason: "Approve the proposed operation?" }] };
  await expectHistorySuggestion(pendingApproval, "Approve the proposed operation?");
  const pendingQuestion = { id: "pending-question", status: "waiting_user", nextStep: "needs_user_action", reason: "question_pending",
    questions: [{ status: "pending", originTurnId: "pending-question", question: "Which option should we use?" }] };
  await expectHistorySuggestion(pendingQuestion, "Which option should we use?");
  const skippedQuestion = { id: "skipped-question", nextStep: "needs_user_action", reason: "question_pending",
    reply: "Close the editor and reply when it is ready.",
    trailing: [{ id: "skip-record", type: "question_answer", selectedOptionId: "skip", text: "Skipped confirmation" }] };
  await expectHistorySuggestion(skippedQuestion, skippedQuestion.reply);
  const beforeModelSwitch = requests.length;
  await change({ model: "dedicated-completion-model" });
  assert.equal(requests.length, beforeModelSwitch, "the completed turn keeps its cached prediction when the model changes");
  await expectHistorySuggestion({ ...restored, id: "new-reply", reply: "New reply." }, "New reply.", "dedicated-completion-model");
  assert.equal(requests.length, beforeModelSwitch + 1, "a new reply invalidates the old result and uses the selected model");
  await change({ busy: true });
  await restoreHistory({ ...skippedQuestion, id: "answered-question", trailing: [
    { id: "answer-record", type: "question_answer", selectedOptionId: "custom", text: "Already closed" },
  ] });
  await change({ busy: false, model: "model-after-answer" });
  await page.waitForFunction(() => document.querySelector("output").textContent === "");
  assert.equal(requests.length, beforeModelSwitch + 1, "a newer user answer suppresses the previous model reply");
  await expectHistorySuggestion(restored, restored.reply, "model-after-answer");
  const beforeEffortSwitch = requests.length;
  await change({ reasoningEffort: "high" });
  assert.equal(requests.length, beforeEffortSwitch);
  const effortRequest = page.waitForRequest(entry => entry.url().endsWith("/chat/input-prediction"), { timeout: 3000 });
  await restoreHistory({ ...restored, id: "new-reasoning-reply" });
  const effortPayload = (await effortRequest).postDataJSON();
  assert.equal(effortPayload.reasoning_effort, "high");
  assert.equal(effortPayload.model, "model-after-answer");
  const failed = { id: "tool-failed-history", nextStep: "tool_failed", reason: "tool_failed",
    reply: "The editor tool failed. I checked the result and found the patch was not applied." };
  await expectHistorySuggestion(failed, failed.reply, "model-after-answer");
  const blocked = { id: "other-finished-outcome", ok: false, status: "blocked", nextStep: "pending_verification", reply: "The work is ready for visual verification." };
  await expectHistorySuggestion(blocked, blocked.reply, "model-after-answer");
  for (const [status, nextStep] of [["failed", "tool_failed"], ["blocked", "pending_approval"], ["denied", "policy_denied"]]) {
    const count = requests.length;
    await start();
    await restoreHistory(undefined);
    const delivered = page.waitForRequest(entry => entry.url().endsWith("/chat/input-prediction"), { timeout: 3000 });
    await finishDelivered({ turnId: nextStep, continuationSource: "question_answered", status, nextStep, reply: failed.reply });
    await change({ busy: false });
    assert.equal((await delivered).postDataJSON().assistant_tail, failed.reply);
    assert.equal(requests.length, count + 1);
  }
  const beforeUnfinished = requests.length;
  await start();
  await restoreHistory({ ...failed, id: "tool-failed-still-running", status: "running" });
  await change({ busy: false });
  await restoreHistory({ ...failed, id: "still-waiting-for-tool", nextStep: "waiting_for_tool" });
  await restoreHistory({ id: "only-commentary", status: "failed", nextStep: "policy_denied",
    publicMessages: [{ id: "progress", text: "Checking the action.", phase: "commentary" }] });
  assert.equal(await page.locator("output").textContent(), "");
  assert.equal(requests.length, beforeUnfinished, "running, waiting or commentary-only entries cannot trigger prediction");
  console.log("PASS: completed and restored replies, cached suggestions, user decisions, model changes, deduplication, typing and cancellation");
} finally {
  finishSlow?.();
  await browser?.close();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
