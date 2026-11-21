import assert from "node:assert/strict";
import { mkdirSync, readFileSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const root = process.cwd();
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
import { buildChatHistory } from "./src/lib/conversation-utils";
const noop = () => {};
function Fixture() {
  const [scenario, setScenario] = useState("cold");
  const [draft, setDraft] = useState("");
  const [reminder, setReminder] = useState({});
  const conversationEndRef = useRef(null);
  window.setChatScenario = setScenario;
  window.setReminderFixture = setReminder;
  const conversation = scenario === "warm" || scenario === "ready"
    ? [{ id: "warm-user", type: "user", text: "Original cached conversation body" }]
    : [];
  window.getModelHistory = () => buildChatHistory(conversation, (key) => key);
  return <ChatWorkspace
    projectPromptTitle="New task welcome"
    conversationPending={scenario === "cold" || scenario === "warm"}
    conversationPendingMessage="Loading selected conversation…"
    input={draft} setInput={setDraft} sending={false} queueAllowed={false}
    onSubmit={noop} onSwitchMode={noop} commands={[]} actions={[]} onAction={noop} disabledReason=""
    attachments={[]} onAttachFiles={noop} onRemoveAttachment={noop}
    providerLabel="" model="" activeGoal={null} onGoalChanged={noop} goalEndpoint=""
    projects={[]} onBindProject={noop} conversation={conversation} queued={[]}
    compaction={reminder.compaction} resourceChatId={reminder.chatId} readOnlyHistory={reminder.preview}
    sessionReadOnly={reminder.sessionReadOnly}
    agentQuestions={[]} backgroundGoalDeliveries={[]} backgroundGoalProviderWarnings={[]}
    onBackgroundGoalCatchUpRendered={noop} onBackgroundGoalProviderWarningsRendered={noop}
    onBackgroundGoalCatchUpDismiss={noop} onAnswerQuestion={noop}
    conversationEndRef={conversationEndRef} onConversationMouseUp={noop} onConversationScroll={noop}
    showScrollToBottom={false} onScrollToBottom={noop} pendingApprovalForResponse={() => null}
    approvalActions={{}} latestRetryableItemId="warm-user" latestEditableUserItemId="warm-user"
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
  const page = await browser.newPage({ locale: "zh-CN", viewport: { width: 1100, height: 800 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  await page.goto(`http://127.0.0.1:${server.address().port}`);

  const loading = page.locator("[data-chat-transcript-loading]");
  const welcome = page.getByRole("heading", { name: "New task welcome", exact: true });
  const composer = page.locator("[data-vrcforge-composer-dropzone] textarea");
  await loading.waitFor();
  assert.equal(await loading.innerText(), "Loading selected conversation…", "cold selected conversation shows its pending status");
  assert.equal(await welcome.count(), 0, "cold existing chat does not show the new-task welcome title");
  await composer.fill("unsent draft survives transcript changes");

  await page.evaluate(() => window.setChatScenario("warm"));
  const warmMessage = page.locator('[data-chat-history-scroll] [data-conversation-item-id="warm-user"] [data-user-message-bubble]');
  await warmMessage.waitFor();
  assert.match(await warmMessage.innerText(), /Original cached conversation body/, "warm cached transcript renders its original message while authority is pending");
  assert.equal(await loading.count(), 0, "warm cached transcript does not show the cold loading skeleton");
  assert.equal(await composer.inputValue(), "unsent draft survives transcript changes", "draft survives rendering the cached transcript");
  const initialModelHistory = await page.evaluate(() => window.getModelHistory());

  await page.evaluate(() => window.setChatScenario("new"));
  await welcome.waitFor();
  assert.equal(await loading.count(), 0, "a genuinely empty new conversation has no transcript loading state");
  assert.equal(await composer.inputValue(), "unsent draft survives transcript changes", "draft survives returning to the empty new conversation");

  const newChatReminder = page.getByRole("dialog", { name: "这段对话已经很长" });
  const reminderState = ({ chatId = "older-chat", generation = "first", trigger = "auto", status = "applied",
    afterTokens = 51, contextLimit = 100, preview = false, sessionReadOnly = false } = {}) => page.evaluate(
    async ({ chatId, generation, trigger, status, afterTokens, contextLimit, preview, sessionReadOnly }) => {
      window.setChatScenario("ready");
      window.setReminderFixture({ chatId, preview, sessionReadOnly, compaction: { generation, status, trigger, afterTokens, contextLimit } });
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }, { chatId, generation, trigger, status, afterTokens, contextLimit, preview, sessionReadOnly },
  );
  for (const state of [{ afterTokens: 49 }, { afterTokens: 50 }, { afterTokens: null }, { contextLimit: null },
    { contextLimit: 0 }, { status: "compacting" }, { status: "failed", afterTokens: 85 }, { status: "cancelled" }, { preview: true }]) {
    await reminderState(state);
    assert.equal(await newChatReminder.count(), 0, `no suggestion for ${JSON.stringify(state)}`);
  }
  await reminderState();
  await newChatReminder.waitFor({ timeout: 4000 });
  assert.equal(await newChatReminder.count(), 1, "applied compaction above half the window shows one dialog");
  assert.equal(await newChatReminder.evaluate((node) => Boolean(node.closest("form, [data-chat-history-scroll]"))), false);
  assert.equal(await newChatReminder.getByRole("button").count(), 1, "the only action acknowledges the reminder");
  assert.deepEqual(await page.evaluate(() => window.getModelHistory()), initialModelHistory);
  assert.equal(await composer.inputValue(), "unsent draft survives transcript changes");

  await newChatReminder.getByRole("button", { name: "知道了", exact: true }).click();
  await newChatReminder.waitFor({ state: "detached" });
  assert.equal(await warmMessage.count(), 1, "acknowledgment keeps the selected conversation");
  await reminderState({ chatId: "another-chat", afterTokens: 23 });
  await reminderState();
  assert.equal(await newChatReminder.count(), 0, "returning to the same generation does not reopen the dialog");
  await page.reload();
  await loading.waitFor();
  await reminderState();
  assert.equal(await newChatReminder.count(), 0, "the acknowledgment survives a reload for the same generation");
  await reminderState({ generation: "second", afterTokens: 50 });
  assert.equal(await newChatReminder.count(), 0, "even a new generation at exactly half stays quiet");
  await reminderState({ generation: "third", trigger: "manual", afterTokens: 84 });
  await newChatReminder.waitFor();
  assert.equal(await composer.count(), 1, "a successful compaction below the automatic threshold leaves the chat writable");
  await page.keyboard.press("Escape");
  await newChatReminder.waitFor({ state: "detached" });

  await reminderState({ generation: "still-at-auto-line", afterTokens: 85, sessionReadOnly: true });
  assert.equal(await composer.count(), 0, "a read-only session cannot send or edit drafts");
  const retiredReminder = page.getByRole("dialog", { name: "此会话已转为只读" });
  await retiredReminder.waitFor();
  assert.equal(await retiredReminder.getByRole("button").count(), 1);
  assert.equal(await newChatReminder.count(), 0, "the ordinary reminder is replaced by the read-only notice");
  await retiredReminder.getByRole("button", { name: "知道了", exact: true }).click();
  await retiredReminder.waitFor({ state: "detached" });
  assert.equal(await page.locator("[data-chat-session-read-only]").count(), 1, "the reason remains visible after acknowledgment");
  assert.equal(await warmMessage.count(), 1, "the original conversation remains readable");
  const historyActions = page.locator('[data-conversation-item-id="warm-user"] [data-chat-message-actions] button');
  assert.equal(await historyActions.count(), 1, "history keeps copy and suppresses retry and edit");
  await reminderState({ chatId: "another-chat", afterTokens: 23 });
  assert.equal(await composer.count(), 1, "another conversation remains writable");
  await reminderState({ generation: "still-at-auto-line", afterTokens: 85, sessionReadOnly: true });
  assert.equal(await retiredReminder.count(), 0, "a confirmed notice stays dismissed on return");
  assert.equal(await composer.count(), 0, "returning to the retired chat keeps it read-only");

  const evidenceDirectory = process.env.VRCFORGE_CHAT_CACHE_EVIDENCE_DIR;
  if (evidenceDirectory) {
    const screenshotPath = resolve(evidenceDirectory, "loading-ui.png");
    mkdirSync(dirname(screenshotPath), { recursive: true });
    await page.screenshot({ path: screenshotPath, fullPage: true });
  }
  assert.deepEqual(errors, [], "real ChatWorkspace renders without browser errors");
  console.log("PASS ChatWorkspace transcript loading, draft retention, compaction reminder, and read-only session");
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolveClose) => server.close(resolveClose));
}
