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
      const [state, setState] = useState({ busy: true, eligible: true, hasDraft: false, provider: "chatgpt", model: "first" });
      const owner = chatInputPredictionOwner(location.origin, "chat", "session");
      const [history, setHistory] = useState();
      const latestReply = history && predictionTurnForChat(location.origin, {
        id: "chat", sessionId: "session", items: [{ id: "input", type: "user", text: "Check this" }, {
          id: history.id, type: "agent", response: { ok: true, turnId: history.id, turnStatus: { status: history.status },
            approvalId: history.approvalId, plan: { nextStep: history.nextStep, reply: history.reply,
              summary: "", completionGate: { reason: history.reason } },
          },
        }, ...(history.trailing || [])],
      }, history.questions || [], history.approvals || []);
      const prediction = useChatInputPrediction({ ...state, endpoint: location.origin, owner, latestReply });
      window.openHistory = setHistory;
      window.change = (patch) => setState(prev => ({ ...prev, ...patch }));
      window.start = () => { window.submission = prediction.dismiss(); window.change({ busy: true }); };
      window.type = prediction.onInputActivity;
      window.finish = (turnId) => prediction.offer({
        owner, turnId, context: {recent_user_messages: ["继续"], assistant_tail: turnId}
      }, window.submission);
      window.finishDelivered = ({ turnId, continuationSource, status, nextStep }) => {
        const now = new Date().toISOString();
        return prediction.offerAfterResume({
          sessionId: "session", turnId, continuationSource, status, startedAt: now, completedAt: now,
          plan: { nextStep, reply: turnId },
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
      res.end(JSON.stringify({ suggestion: "继续" }));
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
  for (const continuationSource of ["foreground_completed", "approval_finished", "shell_process_finished", "sub_agent_finished"]) {
    await start();
    const observed = page.waitForRequest(request => request.url().endsWith("/chat/input-prediction"));
    await finishDelivered({ turnId: continuationSource, continuationSource, nextStep: "done" });
    await change({ busy: false });
    assert.equal((await observed).postDataJSON().assistant_tail, continuationSource);
    await page.waitForFunction(() => document.querySelector("output").textContent === "继续");
    await finish(continuationSource);
  }
  assert.deepEqual(requests.slice(2).map(request => request.assistant_tail),
    ["foreground_completed", "approval_finished", "shell_process_finished", "sub_agent_finished"]);
  await start();
  await finishDelivered({ turnId: "blocked", continuationSource: "approval_finished", nextStep: "pending_approval", status: "blocked" });
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
  const restored = { id: "saved", status: "completed", nextStep: "done", reply: "Saved final reply" };
  await expectHistorySuggestion(restored, restored.reply);
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
  const modelSwitch = page.waitForRequest(entry => entry.url().endsWith("/chat/input-prediction"));
  await change({ model: "dedicated-completion-model" });
  assert.equal((await modelSwitch).postDataJSON().model, "dedicated-completion-model");
  assert.equal(requests.length, 11);
  await change({ busy: true });
  await restoreHistory({ ...skippedQuestion, id: "answered-question", trailing: [
    { id: "answer-record", type: "question_answer", selectedOptionId: "custom", text: "Already closed" },
  ] });
  await change({ busy: false, model: "model-after-answer" });
  await page.waitForFunction(() => document.querySelector("output").textContent === "");
  assert.equal(requests.length, 11, "a newer user answer suppresses the previous model reply");
  await expectHistorySuggestion(restored, restored.reply, "model-after-answer");
  const effortChange = page.waitForRequest(entry => entry.url().endsWith("/chat/input-prediction"), { timeout: 3000 });
  await change({ reasoningEffort: "high" });
  const effortPayload = (await effortChange).postDataJSON();
  assert.equal(effortPayload.reasoning_effort, "high");
  assert.equal(effortPayload.model, "model-after-answer");
  console.log("PASS: completed and restored replies, user decisions, model changes, deduplication, typing and cancellation");
} finally {
  finishSlow?.();
  await browser?.close();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
