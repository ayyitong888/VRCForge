import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { build } from "esbuild";
import { chromium } from "playwright";

const replayDir = process.env.VRCFORGE_LOOP_HISTORY_REPLAY_DIR;
const actual = (name) => JSON.parse(readFileSync(join(replayDir, name), "utf8"));
const originalQuestion = replayDir ? actual("questions-post-pause.json").questions[0] : {
  questionId: "obsolete-loop-question", sourceTool: "runtime_tool_loop", question: "Tool loop technical prompt",
  originClientTurnId: "original-turn", sessionId: "session", status: "pending", blocking: true, needsUserAction: true,
};
const response = replayDir ? {
  ...actual("run-completed-pause.json").continuationEvent,
  timeline: actual("full-question-timeline.json").events,
} : {
  clientTurnId: originalQuestion.originClientTurnId, sessionId: originalQuestion.sessionId,
  plan: { nextStep: "paused", reply: "Diagnostic pause text", summary: "Technical result" },
  timeline: [{
    id: "terminal", kind: "assistant", sequence: 1, timestamp: "2026-11-19T00:00:00Z",
    payload: { phase: "final_answer", status: "paused", summary: "Diagnostic pause text" },
  }],
};
const retired = {
  questionId: originalQuestion.questionId, sourceTool: originalQuestion.sourceTool,
  originClientTurnId: originalQuestion.originClientTurnId, sessionId: originalQuestion.sessionId,
  projectRoot: originalQuestion.projectRoot, status: "cancelled", blocking: false, retiredLoopPause: true,
};
const realQuestion = {
  ...originalQuestion, questionId: "genuine-question", question: "应该使用哪种颜色？",
  header: "配色确认", sourceTool: "vrcforge_ask_user", options: [{ id: "red", label: "红色" }],
};
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React, { useState } from "react";
    import { createRoot } from "react-dom/client";
    import { ChatWorkspace } from "./src/components/chat/chat-workspace";
    import { RuntimeAttentionRows } from "./src/components/runtime/runtime-sidebar-ui";
    import { pendingQuestionReviewTargets } from "./src/lib/runtime-scope";
    import { projectRuntimeResponseForDisplay } from "./src/lib/chat-timeline-presentation";
    import { initializeI18n, setLocale } from "./src/i18n";
    const response = ${JSON.stringify(response)};
    const realQuestion = ${JSON.stringify(realQuestion)};
    const chatId = "chat-history";
    window.projectedToolResults = projectRuntimeResponseForDisplay(response, key => key, true).timeline
      .filter(event => event.kind === "tool_result").map(event => event.payload.summary || "");
    const items = [{ id: "old-turn", type: "agent", response, elapsedSeconds: 2 },
      { id: "real-answer", type: "question_answer", questionId: "already-answered", text: "保留的真实回答" }];
    const noop = () => {};
    function Fixture() {
      const [input, setInput] = useState("");
      const [questions, setQuestions] = useState([window.legacyQuestion]);
      window.showRealQuestion = () => setQuestions([...questions, realQuestion]);
      const answers = pendingQuestionReviewTargets([{
        id: chatId, items, sessionId: response.sessionId, projectPath: realQuestion.projectRoot,
      }], questions, chatId);
      return <>
        <div id="right-sidebar"><RuntimeAttentionRows pendingQuestions={{ count: answers.questionIds.size, onOpen: noop }} /></div>
        <ChatWorkspace input={input} setInput={setInput} sending={false} queueAllowed={false}
          onSubmit={event => { event?.preventDefault(); window.chatRequests.push(input); }}
          onSwitchMode={noop} commands={[]} actions={[]} attachments={[]} projects={[]}
          activeGoal={null} disabledReason="" conversation={items} queued={[]} runtimeRuns={[]}
          runtimeSessionId={response.sessionId} agentQuestions={questions} backgroundGoalDeliveries={[]}
          backgroundGoalProviderWarnings={[]} scopedPendingApprovals={[]}
          approvalActions={{}} pendingApprovalForResponse={() => null}
          onAnswerQuestion={question => window.questionRequests.push(question.questionId)}
          sessionHandoffSourceChatId={chatId} onConversationScroll={noop} onConversationMouseUp={noop} />
      </>;
    }
    void initializeI18n().then(() => setLocale("zh-CN")).then(() =>
      createRoot(document.getElementById("root")).render(<Fixture />));
  ` },
  bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
});
const browser = await chromium.launch({
  headless: true,
  channel: process.env.VRCFORGE_BROWSER_CHANNEL || (process.platform === "win32" ? "msedge" : undefined),
});
try {
  const page = await browser.newPage();
  await page.goto("about:blank");
  await page.setContent('<main id="root"></main>');
  await page.evaluate((question) => {
    window.legacyQuestion = question;
    window.chatRequests = [];
    window.questionRequests = [];
  }, process.env.VRCFORGE_LOOP_HISTORY_BASELINE ? originalQuestion : retired);
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  await page.getByText("保留的真实回答").waitFor();
  assert.equal(await page.locator("#right-sidebar [data-vrcforge-status='runtime-question']").count(), 0);
  assert.equal(await page.getByText("反复尝试后仍没有新结果，本轮已结束。你可以发消息继续。", { exact: true }).count(), 1);
  const oldReply = response.plan.reply;
  assert.equal(await page.getByText(oldReply, { exact: true }).count(), 0);
  const projectedToolResults = await page.evaluate(() => window.projectedToolResults);
  assert.ok(projectedToolResults.every(text => !/^when[- ]to[- ]use\s*:/i.test(text)));
  if (replayDir) assert.ok(projectedToolResults.some(text => text.includes("Unity compilation is stable")));
  const composer = page.locator("[data-chat-composer-dock]");
  const input = composer.locator("textarea");
  await input.fill("继续检查已有服装");
  await input.press("Enter");
  assert.deepEqual(await page.evaluate(() => window.chatRequests), ["继续检查已有服装"]);
  assert.deepEqual(await page.evaluate(() => window.questionRequests), []);
  await page.evaluate(() => window.showRealQuestion());
  await page.locator("#right-sidebar [data-vrcforge-status='runtime-question']").waitFor();
  assert.ok(await page.getByText("应该使用哪种颜色？").first().isVisible());
} finally {
  await browser.close();
}
console.log("A retired loop pause is explained once, leaves chat submission available, and preserves real questions and answers.");
