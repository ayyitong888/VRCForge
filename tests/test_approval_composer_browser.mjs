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
      const [history, setHistory] = useState([]);
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
      return <ChatWorkspace
        input={input} setInput={setInput} sending={false} queueAllowed={false}
        onSubmit={(event) => { event?.preventDefault(); window.composerSubmitted++; }}
        onSwitchMode={noop} commands={[]} actions={[]} attachments={[]} projects={[]}
        activeGoal={null} disabledReason="" conversation={history} queued={[]} runtimeRuns={[]}
        agentQuestions={[]} backgroundGoalDeliveries={[]} backgroundGoalProviderWarnings={[]}
        scopedPendingApprovals={approvals} approvalActions={execution.approvalActions}
        pendingApprovalForResponse={execution.pendingApprovalForResponse}
        onApprove={execution.approveShell} onReject={execution.rejectShell}
        onModifyApproval={execution.modifyApprovalInComposer}
        sessionHandoffSourceChatId={chat.id} onConversationScroll={noop} onConversationMouseUp={noop}
      />;
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
    await page.evaluate(() => window.showTestApproval());
    const card = page.locator("[data-scoped-pending-approval]");
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
    assert.deepEqual(errors, []);
    await page.close();
  }
} finally {
  await browser.close();
}
console.log("rollback approval preserves the composer, draft, selection and focus in both chat layouts");
