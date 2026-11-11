import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { build } from "esbuild";

const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React, { useState } from "react";
    import { createRoot } from "react-dom/client";
    import { RightRuntimeSidebar } from "./src/components/runtime/runtime-sidebar";
    import { AgentQuestionCard } from "./src/components/chat/agent-question-card";
    import { pendingQuestionReviewTargets, filterQuestionsForRuntimeScope, runtimeReviewItemForChat } from "./src/lib/runtime-scope";
    import { initializeI18n } from "./src/i18n";
    const root = createRoot(document.getElementById("root"));
    const ready = initializeI18n();
    window.showSidebar = async (state, pendingApprovals = 0, projectType = "unity", hasExternalPendingApprovals = false, reviewFixture) => {
      await ready;
      window.approvalsOpened = false;
      window.reviewItemOpened = undefined;
      const bridge = state === "offline" ? "warning" : state === "checking" ? "unknown" : "ok";
      const tools = state === "ready" ? "ok" : state === "checking" ? "unknown" : "warning";
      function Sidebar() {
        const [active, setActive] = useState(reviewFixture?.activeChatId || "");
        const [reveal, setReveal] = useState();
        const { chats = [], questions = [], runtimeAttention = [] } = reviewFixture || {};
        const review = pendingQuestionReviewTargets(chats, questions, active);
        const currentChat = chats.find((chat) => chat.id === active);
        const scopedQuestions = currentChat ? filterQuestionsForRuntimeScope(questions,
          {sessionId: currentChat.sessionId, projectRoot: currentChat.projectPath}) : [];
        const runtimeReview = runtimeReviewItemForChat(currentChat, runtimeAttention, scopedQuestions);
        return <><RightRuntimeSidebar
        runtimeConnected loadingUnityStatus={state === "checking"} workspaceProjectLabel="VRCForge"
        workspaceProjectType={projectType} projectWorkspace
        selectedProjectComponent={{status:"ok"}} backendComponent={{status:"ok"}} mcpPackageComponent={{status:"ok"}}
        unityBridgeComponent={{status:bridge}} unityInstanceComponent={{status:bridge}}
        unityToolsComponent={{status:tools, detail:state === "checking" ? undefined : {readiness:{
          ready:state === "ready", blockerCode:state === "offline" ? "unity_core_connection_failed" : "unknown",
        }}}}
        authorizationStatus={{source:"none",status:"unknown"}} agentProgress={[]}
        subAgentTaskCount={0} subAgentRunningTaskCount={0} subAgentCompletedTaskCount={0}
        userAttachmentSources={[]} approvalsLoaded pendingApprovals={pendingApprovals} workspaceSummary={null}
        hasExternalPendingApprovals={hasExternalPendingApprovals}
        runtimeReviewCount={review.count || (runtimeReview ? 1 : 0)}
        onOpenRuntimeReview={() => {
          if (review.target) {
            setActive(review.target.chat.id);
            setReveal({ questionId: review.target.question.questionId });
          } else {
            window.reviewItemOpened = runtimeReview?.id;
          }
        }}
        activeDesktopActions={[]} refreshUnityStatus={() => {}} providerTestAttempted={false}
        onHideSidebar={() => {}} onOpenPendingApprovals={() => window.approvalsOpened = true}
        localizeHealthMessage={() => ""} />
        <output data-review-owner>{active}</output>
        {reveal && currentChat && <AgentQuestionCard
          questions={filterQuestionsForRuntimeScope(questions, {sessionId: currentChat.sessionId, projectRoot: currentChat.projectPath || ""})}
          reveal={reveal} onAnswerQuestion={() => { throw new Error("Navigation must not answer the question"); }} onDismissQuestion={() => {}} />}
        </>;
      }
      root.render(<Sidebar />);
    };
  ` },
  bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
});
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const server = createServer((_, response) => {
  response.setHeader("Content-Type", "text/html; charset=utf-8");
  response.end('<main id="root"></main>');
});
let browser;
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage({ viewport: { width: 300, height: 600 } });
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  const show = (...args) => page.evaluate((sidebarArgs) => window.showSidebar(...sidebarArgs), args);
  await show("blocked", 1);
  const blocked = page.locator('[data-unity-readiness="blocked"]');
  await blocked.waitFor({ timeout: 4000 });
  assert.match(await blocked.innerText(), /Unity is connected.*Avatar tools are unavailable/);
  assert.match(await blocked.innerText(), /Check the selected project and Unity Console/);
  assert.equal(await blocked.locator("details").evaluate((element) => element.open), false);
  const details = page.getByRole("button", { name: "Environment details" });
  assert.equal(await details.getAttribute("aria-expanded"), "false");
  assert.equal(await page.locator("[data-vrcforge-project-environment]").count(), 0);
  const approval = page.locator('[data-vrcforge-status="approval"]');
  await approval.click();
  assert.equal(await page.evaluate(() => window.approvalsOpened), true);
  assert.equal(await details.getAttribute("aria-expanded"), "false");
  await details.click();
  await page.locator("[data-vrcforge-project-environment]").waitFor();
  await show("ready", 0);
  await page.locator('[data-unity-readiness="ready"]').waitFor();
  assert.equal(await approval.count(), 0);
  await show("offline", 0);
  const unavailable = page.locator('[data-unity-readiness="unavailable"]');
  await unavailable.waitFor();
  assert.match(await unavailable.innerText(), /Not connected to Unity yet/);
  await show("checking", 0);
  await page.locator('[data-unity-readiness="checking"]').waitFor();
  assert.equal(await page.getByRole("alert").count(), 0);
  await show("ready", 1, "general", true);
  await approval.click();
  assert.equal(await page.evaluate(() => window.approvalsOpened), true);
  await show("ready", 1, "general", false);
  await approval.waitFor({ state: "detached" });
  const fixture = process.env.VRCFORGE_REVIEW_REPLAY
    ? JSON.parse(readFileSync(process.env.VRCFORGE_REVIEW_REPLAY, "utf8"))
    : {
      activeChatId: "unity",
      chats: [
        { id: "unity", sessionId: "unity-session", projectPath: "D:/Fixture", items: [{ type: "question_answer", questionId: "answered" }] },
        { id: "other", sessionId: "other-session", projectPath: "", items: [] },
      ],
      questions: [
        { questionId: "answered", sessionId: "unity-session", projectRoot: "D:/Fixture", status: "pending", needsUserAction: true },
        { questionId: "pending", sessionId: "other-session", status: "pending", needsUserAction: true, question: "Which view should we look at first?", options: [{ id: "face", label: "Face" }] },
      ],
    };
  const pending = fixture.questions.at(-1);
  const owner = fixture.chats.find((chat) => chat.sessionId === pending.sessionId);
  await show("ready", 0, "unity", false, fixture);
  const review = page.getByRole("button", { name: /Task needs review/ });
  assert.match(await review.innerText(), /1 task check/);
  await review.click();
  const card = page.locator("[data-chat-question-dock]");
  await card.waitFor();
  assert.equal(await page.locator("[data-review-owner]").textContent(), owner.id);
  assert.ok((await card.innerText()).includes(pending.question));
  assert.ok((await card.innerText()).includes(pending.options[0].label));

  const blockedRun = { sessionId: "review-session", clientTurnId: "review-client", turnId: "review-turn", status: "blocked" };
  const blockedReply = {
    id: "blocked-reply", type: "agent",
    response: { clientTurnId: blockedRun.clientTurnId, turnId: blockedRun.turnId,
      plan: { completionGate: { reason: "question_pending", status: "needs_user_action" } } },
  };
  const answered = { type: "question_answer", questionId: "previously-answered" };
  const stale = {
    activeChatId: "review-chat",
    chats: [{ id: "review-chat", sessionId: blockedRun.sessionId, projectPath: "D:/Fixture", items: [blockedReply, answered] }],
    questions: [{ questionId: answered.questionId, sessionId: blockedRun.sessionId, projectRoot: "D:/Fixture", status: "answered" }],
    runtimeAttention: [blockedRun],
  };
  await show("ready", 0, "unity", false, stale);
  await page.locator('[data-review-owner]').getByText("review-chat").waitFor();
  assert.equal(await review.count(), 0, "a blocked origin must not show a review badge after its question is answered");

  delete blockedReply.response.plan.completionGate;
  stale.questions[0].originClientTurnId = blockedRun.clientTurnId;
  await show("ready", 0, "unity", false, stale);
  assert.equal(await review.count(), 0, "an answered Question owns its old continuation receipt even without a gate");

  stale.questions[0].originClientTurnId = "another-client";
  await show("ready", 0, "unity", false, stale);
  await review.waitFor();

  blockedReply.response.plan.completionGate = { reason: "verification_required" };
  await show("ready", 0, "unity", false, stale);
  await review.click();
  assert.equal(await page.evaluate(() => window.reviewItemOpened), blockedReply.id);

  blockedReply.response.plan.completionGate.reason = "question_pending";
  blockedReply.response.steps = [{ tool: "vrcforge_request_project_selection", result: { projectSelectionRequested: true } }];
  await show("ready", 0, "unity", false, stale);
  await review.click();
  assert.equal(await page.evaluate(() => window.reviewItemOpened), blockedReply.id);

  delete blockedReply.response.clientTurnId;
  delete blockedReply.response.turnId;
  stale.runtimeAttention = [{ sessionId: blockedRun.sessionId, status: "blocked" }];
  await show("ready", 0, "unity", false, stale);
  await review.waitFor({ state: "detached" });
  if (process.env.VRCFORGE_QUESTION_CONTINUATION_REPLAY) {
    const { run, terminal, question } = JSON.parse(readFileSync(process.env.VRCFORGE_QUESTION_CONTINUATION_REPLAY, "utf8"));
    assert.equal(question.status, "answered");
    await show("ready", 0, "unity", false, {
      activeChatId: "original",
      chats: [{ id: "original", sessionId: run.sessionId, projectPath: run.projectRoot, items: [
        { id: "original-reply", type: "agent", response: terminal },
        { id: "original-answer", type: "question_answer", questionId: question.questionId },
      ] }],
      questions: [question],
      runtimeAttention: [run],
    });
    await page.locator("[data-review-owner]").getByText("original").waitFor();
    assert.equal(await review.count(), 0, "the original answered Milltina question cannot keep the review badge active");
  }
  console.log("Unity readiness, approval access and pending review navigation passed");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
