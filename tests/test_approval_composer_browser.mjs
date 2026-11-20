import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { build } from "esbuild";
import { chromium } from "playwright";
import postcss from "postcss";
import tailwind from "tailwindcss";

const replayPath = process.env.VRCFORGE_APPROVAL_COMPOSER_REPLAY;
const replay = replayPath ? JSON.parse(readFileSync(replayPath, "utf8")) : undefined;
const chat = replay?.chat ?? {
  id: "project-chat",
  sessionId: "project-session",
  projectPath: "D:/TestProject",
  items: [{ id: "request", type: "user", text: "Restore the saved checkpoint." }],
};
const approval = replay?.approval ?? {
  id: "restore-approval",
  status: "pending",
  projectRoot: chat.projectPath,
  targetTool: "vrcforge_restore_checkpoint",
  arguments: { checkpointId: "saved-checkpoint" },
  allowFutureEligible: false,
};
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React, { useState } from "react";
    import { createRoot } from "react-dom/client";
    import { ChatWorkspace } from "./src/components/chat/chat-workspace";
    import { PendingApprovalsStrip } from "./src/components/approvals/pending-approvals-strip";
    import { RightRuntimeSidebar } from "./src/components/runtime/runtime-sidebar";
    import { useApprovalExecution } from "./src/hooks/use-approval-execution";
    import { initializeI18n, setLocale } from "./src/i18n";
    const chat = ${JSON.stringify(chat)};
    const approval = ${JSON.stringify(approval)};
    const noop = () => {};
    async function mount() {
      await initializeI18n();
      await setLocale("en-US");
      createRoot(document.getElementById("root")).render(<Fixture />);
    }
    window.composerSubmitted = 0;
    function Fixture() {
      const [input, setInput] = useState("");
      const [approvals, setApprovals] = useState([]);
      const [approvalOpen, setApprovalOpen] = useState(false);
      const [history, setHistory] = useState([]);
      const [questions, setQuestions] = useState([]);
      const [questionPanelTarget, setQuestionPanelTarget] = useState(null);
      const [reviewQuestionReveal, setReviewQuestionReveal] = useState(null);
      const [pendingReplyQuestionId, setPendingReplyQuestionId] = useState("");
      window.showTestQuestion = (live) => {
        setQuestions([{ questionId: "question", status: "pending", sessionId: chat.sessionId,
          originClientTurnId: "question-turn", question: "Which side should we check?", options: [{ id: "front", label: "Front" }] }]);
        setHistory(live ? [{ id: "question-live", type: "streaming", clientTurnId: "question-turn", sessionId: chat.sessionId, text: "" }]
          : [{ id: "question-settled", type: "agent", response: { ok: true, status: "blocked", sessionId: chat.sessionId,
            clientTurnId: "question-turn", turnId: "question-backend-turn", plan: { summary: "", nextStep: "needs_user_action" } } }]);
      };
      window.setTestHistory = (hasHistory) => setHistory(hasHistory ? chat.items : []);
      window.showTestApproval = () => setApprovals([approval]);
      const execution = useApprovalExecution({
        endpoint: location.origin, activeRuntimeProjectPath: chat.projectPath,
        activeChatId: chat.id, activeView: "chat",
        pendingApprovalItems: approvals, externalApprovalItems: [],
        setRuntimeNotice: noop, setError: (error) => { window.approvalError = error; },
        appendToChat: noop, chatIdForSessionId: () => chat.id,
        refresh: async () => { setApprovals([]); setHistory(chat.items); }, refreshRuntimeRuns: async () => {},
        loadCheckpoints: async () => { window.checkpointsRefreshed = (window.checkpointsRefreshed || 0) + 1; },
        reloadChatStorageState: async () => false,
      });
      return <div className="flex min-h-0 flex-1"><ChatWorkspace
        input={input} setInput={setInput} inputSuggestion="Check the result" sending={false} queueAllowed={false}
        onSubmit={(event) => { event?.preventDefault(); window.composerSubmitted++; }}
        onSwitchMode={noop} commands={[]} actions={[]} attachments={[]} projects={[]}
        activeGoal={null} disabledReason="" conversation={history} queued={[]} runtimeRuns={[]}
        agentQuestions={questions} backgroundGoalDeliveries={[]} backgroundGoalProviderWarnings={[]}
        runtimeSessionId={chat.sessionId} questionPanelTarget={questionPanelTarget} reviewQuestionReveal={reviewQuestionReveal}
        pendingReplyQuestionId={pendingReplyQuestionId} onReplyInChat={setPendingReplyQuestionId}
        onCancelReply={() => setPendingReplyQuestionId("")} onDismissQuestion={() => setReviewQuestionReveal(null)}
        onAnswerQuestion={(id, option) => { window.answeredOption = option; setQuestions([]); }}
        scopedPendingApprovals={approvals} approvalActions={execution.approvalActions}
        pendingApprovalForResponse={execution.pendingApprovalForResponse}
        onApprove={execution.approveShell} onReject={execution.rejectShell}
        onModifyApproval={execution.modifyApprovalInComposer}
        sessionHandoffSourceChatId={chat.id} onConversationScroll={noop} onConversationMouseUp={noop}
      />
        <div style={{width:320,flexShrink:0}}><RightRuntimeSidebar runtimeConnected loadingUnityStatus={false} workspaceProjectLabel="Fixture"
          workspaceProjectType="general" projectWorkspace authorizationStatus={{source:"none",status:"unknown"}}
          agentProgress={[]} userAttachmentSources={[]} approvalsLoaded pendingApprovals={approvals.length}
          onQuestionPanelTarget={setQuestionPanelTarget}
          pendingQuestions={questions.length ? {count:questions.length,
            onOpen:() => setReviewQuestionReveal({questionId:questions[0].questionId})} : undefined}
          approvalPanel={approvalOpen ? <PendingApprovalsStrip approvals={approvals} actions={execution.approvalActions}
            loading={false} onApprove={execution.approveShell} onReject={execution.rejectShell} /> : undefined}
          workspaceSummary={null} activeDesktopActions={[]} refreshUnityStatus={noop} providerTestAttempted={false}
          onHideSidebar={noop} onOpenPendingApprovals={() => setApprovalOpen((value) => !value)}
          localizeHealthMessage={() => ""} /></div>
      </div>;
    }
    void mount();
  ` },
  bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
});
const css = (await postcss([tailwind("tailwind.config.ts")]).process(
  "@tailwind base; @tailwind utilities;", { from: "src/styles.css" },
)).css;
const browser = await chromium.launch({
  headless: true,
  channel: process.env.VRCFORGE_BROWSER_CHANNEL || (process.platform === "win32" ? "msedge" : undefined),
});
try {
  for (const hasHistory of [true, false]) {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const requests = [];
    let releaseApproval;
    const approvalResponse = new Promise((resolve) => { releaseApproval = resolve; });
    await page.route("http://approval-composer.test/**", async (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === "/") return route.fulfill({
        contentType: "text/html", body: '<main id="root" class="flex h-screen flex-col"></main>',
      });
      if (pathname !== `/api/app/agent/approvals/${encodeURIComponent(approval.id)}/approve`) return route.abort();
      requests.push(route.request().postDataJSON());
      await approvalResponse;
      await route.fulfill({ json: {
        ok: true, approval: { ...approval, status: "approved" },
        execution: { targetTool: approval.targetTool, status: "applied", result: { ok: true } },
      } });
    });
    await page.goto("http://approval-composer.test/");
    await page.addStyleTag({ content: css });
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    const input = page.locator("[data-chat-composer-dock] textarea");
    await input.waitFor();
    await page.evaluate((value) => window.setTestHistory(value), hasHistory);
    await page.locator(hasHistory ? "[data-chat-history-scroll]" : "[data-empty-chat-content]").waitFor({ state: "attached" });
    await page.evaluate(() => window.showTestApproval());
    const card = page.locator("[data-runtime-approval-panel]");
    const approvalEntry = page.locator('[data-vrcforge-status="approval"]');
    await approvalEntry.waitFor();
    assert.equal(await card.count(), 0, "settled approval stays collapsed until the right-side button is used");
    assert.equal(await page.locator("[data-chat-composer-dock] [data-scoped-pending-approval]").count(), 0);
    assert.equal(await page.locator("[data-composer-input-suggestion]").innerText(), "Check the result");
    assert.equal(await page.locator("[data-composer-send]").isDisabled(), true, "prediction does not permit sending past approval");
    await input.fill("Keep this draft");
    const isCompact = () => input.evaluate((node) => node.closest(".chat-composer-frame").classList.contains("px-2.5"));
    assert.equal(await isCompact(), hasHistory);
    await input.evaluate((node) => { window.originalInput = node; node.setSelectionRange(5, 9); });
    const inputState = () => page.evaluate(() => ({
      attached: window.originalInput.isConnected,
      focused: document.activeElement === window.originalInput,
      draft: window.originalInput.value,
      selection: [window.originalInput.selectionStart, window.originalInput.selectionEnd],
    }));
    await approvalEntry.evaluate((button) => button.click());
    const approve = card.getByRole("button", { name: "Allow once", exact: true });
    await approve.waitFor({ timeout: 4000 });
    const pendingState = await inputState();
    if (pendingState.attached) {
      assert.equal(await page.locator("[data-composer-send]").isDisabled(), true);
      await input.press("Enter");
      assert.equal(await page.evaluate(() => window.composerSubmitted), 0);
    }
    const request = page.waitForRequest((entry) => entry.url().endsWith("/approve"));
    await approve.evaluate((button) => { button.click(); button.click(); });
    await request;
    await card.waitFor({ state: "hidden" });
    const expected = { attached: true, focused: true, draft: "Keep this draft", selection: [5, 9] };
    assert.deepEqual(await inputState(), expected, "the composer remains mounted and focused during an unresolved rollback");
    assert.deepEqual(pendingState, expected, "revealing approval keeps the original input and selection");
    assert.deepEqual(requests, [{ expectedProjectRoot: approval.projectRoot, globalOnly: false }]);
    assert.equal(await page.locator("[data-composer-send]").isDisabled(), true);
    releaseApproval();
    await page.waitForFunction(() => window.checkpointsRefreshed === 1);
    await page.waitForFunction(() => !document.querySelector("[data-composer-send]").disabled);
    assert.deepEqual(await inputState(), expected, "the receipt preserves the active draft and focus");
    assert.equal(await isCompact(), true);
    await input.press("Enter");
    assert.equal(await page.evaluate(() => window.composerSubmitted), 1);
    assert.equal(requests.length, 1);
    assert.equal(await page.evaluate(() => window.approvalError), "");
    if (hasHistory) {
      await page.evaluate(() => window.showTestQuestion(true));
      await page.locator("[data-chat-composer-dock] [data-chat-question-dock]").waitFor();
      await page.evaluate(() => window.showTestQuestion(false));
      await page.locator('[data-conversation-item-id="question-settled"]').waitFor({ state: "attached" });
      assert.equal(await page.locator("[data-chat-question-dock]").count(), 0);
      assert.equal(await page.locator("[data-chat-answer-question]").count(), 0);
      await page.locator('[data-vrcforge-status="runtime-question"]').click();
      const questionCard = page.locator("[data-runtime-question-panel] [data-chat-question-dock]");
      await questionCard.waitFor();
      assert.equal(await page.locator("[data-chat-composer-dock] [data-chat-question-dock]").count(), 0);
      await questionCard.locator("[data-question-reply-in-chat]").click();
      await page.locator("[data-question-reply-mode]").waitFor();
      await page.locator("[data-question-reply-mode] button").click();
      await questionCard.getByRole("button", { name: /Front/ }).click();
      await questionCard.waitFor({ state: "detached" });
      assert.equal(await page.evaluate(() => window.answeredOption), "front");
    }
    assert.deepEqual(errors, []);
    await page.close();
  }
} finally {
  await browser.close();
}
console.log("settled decisions reopen in the sidebar; approval preserves the composer draft and question can be answered");
