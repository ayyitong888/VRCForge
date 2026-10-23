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
const noop = () => {};
function Fixture() {
  const [scenario, setScenario] = useState("cold");
  const [draft, setDraft] = useState("");
  const conversationEndRef = useRef(null);
  window.setChatScenario = setScenario;
  const conversation = scenario === "warm"
    ? [{ id: "warm-user", type: "user", text: "Original cached conversation body" }]
    : [];
  return <ChatWorkspace
    projectPromptTitle="New task welcome"
    conversationPending={scenario !== "new"}
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

  await page.evaluate(() => window.setChatScenario("new"));
  await welcome.waitFor();
  assert.equal(await loading.count(), 0, "a genuinely empty new conversation has no transcript loading state");
  assert.equal(await composer.inputValue(), "unsent draft survives transcript changes", "draft survives returning to the empty new conversation");

  const evidenceDirectory = process.env.VRCFORGE_CHAT_CACHE_EVIDENCE_DIR;
  if (evidenceDirectory) {
    const screenshotPath = resolve(evidenceDirectory, "loading-ui.png");
    mkdirSync(dirname(screenshotPath), { recursive: true });
    await page.screenshot({ path: screenshotPath, fullPage: true });
  }
  assert.deepEqual(errors, [], "real ChatWorkspace renders without browser errors");
  console.log("PASS ChatWorkspace cold transcript loading, warm cached body, new-chat welcome, and draft retention");
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolveClose) => server.close(resolveClose));
}
