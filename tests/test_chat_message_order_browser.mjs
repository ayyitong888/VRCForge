import assert from "node:assert/strict";
import postcss from "postcss";
import tailwindcss from "tailwindcss";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { build } from "esbuild";

const root = process.cwd();
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const replayPath = process.env.VRCFORGE_TURN_REPLAY_PATH;
const replay = replayPath ? JSON.parse(await readFile(resolve(replayPath), "utf8")) : undefined;
const rawTimeline = replay?.response?.timeline ?? replay?.timeline?.events ?? replay?.timeline;
const sourceEvents = rawTimeline || [
  { id: "fixture-call", sequence: 10, timestamp: "2026-10-23T08:50:00.000+09:00", kind: "tool_call", payload: { status: "started" } },
  { id: "fixture-result", sequence: 11, timestamp: "2026-10-23T08:50:01.000+09:00", kind: "tool_result", payload: { status: "ok" } },
];
const toolCall = sourceEvents.find((event) => event.kind === "tool_call");
const toolResult = sourceEvents.find((event) => event.kind === "tool_result" && event.sequence > toolCall?.sequence);
assert.ok(toolCall && toolResult, "timeline fixture contains a real or generic tool call/result pair");
// Project only structural fields and generic labels; do not persist receipt text or tool payloads.
const clientTurnId = replay?.response?.clientTurnId || "message-order-fixture";
const createdAt = sourceEvents[0].timestamp;
const timeline = [toolCall, toolResult].map((event, index) => ({
  id: `fixture-${index}`,
  sequence: event.sequence,
  timestamp: event.timestamp,
  kind: event.kind,
  payload: { actionId: "fixture-action", tool: "fixture_tool", status: event.payload.status, summary: event.kind === "tool_result" ? "fixture result" : undefined },
}));
const user = { id: `user-${clientTurnId}`, type: "user", text: "fixture user message", clientTurnId, createdAt };
const streaming = { id: `stream-${clientTurnId}`, type: "streaming", clientTurnId, text: "", phase: "waiting_for_model", createdAt, providerLastActivityAt: createdAt, timeline };

const style = (await readFile("src/styles.css", "utf8")).replace('@import "./theme.css";', await readFile("src/theme.css", "utf8"));
const css = (await postcss([tailwindcss("./tailwind.config.ts")]).process(style, { from: "src/styles.css" })).css;
const bundle = await build({
  stdin: { resolveDir: root, loader: "tsx", contents: `
    import React, { useState } from "react";
    import { createRoot } from "react-dom/client";
    import { initializeI18n } from "./src/i18n";
    import { ChatWorkspace } from "./src/components/chat/chat-workspace";
    const noop = () => {};
    function Fixture() {
      const [conversation, setConversation] = useState([]);
      window.setConversation = setConversation;
      return <ChatWorkspace conversation={conversation} conversationEndRef={null}
        projectPromptTitle="" input="" setInput={noop} sending={true} queueAllowed={false} onSubmit={noop}
        onSwitchMode={noop} commands={[]} actions={[]} onAction={noop} disabledReason="" attachments={[]}
        onAttachFiles={noop} onRemoveAttachment={noop} providerLabel="" model="" activeGoal={null}
        projects={[]} queued={[]} agentQuestions={[]} backgroundGoalDeliveries={[]} backgroundGoalProviderWarnings={[]}
        onConversationScroll={noop} pendingApprovalForResponse={()=>null} scopedPendingApprovals={[]}
        approvalActions={{}} editingAttachments={[]} runtimeRuns={[]} sessionHandoffTargetChats={[]} />;
    }
    void initializeI18n().then(() => createRoot(document.getElementById("root")).render(<Fixture/>));
  ` },
  bundle: true, write: false, format: "iife", platform: "browser", jsx: "automatic",
});
const browser = await chromium.launch({ channel: "msedge", headless: true });
try {
  const page = await browser.newPage({ reducedMotion: "no-preference" });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.clock.setFixedTime(new Date(Date.parse(createdAt) + 22_000));
  await page.setContent('<div id="root"></div>');
  await page.addStyleTag({ content: css });
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  await page.waitForFunction(() => typeof window.setConversation === "function");
  const status = page.locator("[data-chat-current-activity]");
  const label = status.locator(".chat-current-activity__label");
  const clock = page.locator("[data-agent-turn-process-title], [data-chat-turn-elapsed]");
  const render = (items) => page.evaluate(items => window.setConversation(items), items);
  const before = async (first, second) => first.evaluate(
    (element, next) => Boolean(element.compareDocumentPosition(next) & Node.DOCUMENT_POSITION_FOLLOWING),
    await second.elementHandle(),
  );
  const answer = { id: "answer", type: "question_answer", questionId: "where", text: "check again", question: "What next?", updatedAt: createdAt };
  const previous = { id: "previous", type: "agent", completedAt: new Date(Date.parse(createdAt) - 3_600_000).toISOString(),
    response: { clientTurnId: "previous", plan: { reply: "Previous task completed.", nextStep: "done" } } };
  const standalone = { ...user, id: "external-user", clientTurnId: undefined, text: "external user message" };
  for (const input of [user, standalone, answer]) {
    const currentTurn = input === answer ? { ...streaming, clientTurnId: clientTurnId + ":question:where" } : streaming;
    await render([previous, input, { ...currentTurn, text: "progress after request" }]);
    await page.getByText(input.text, { exact: true }).waitFor();
    await label.waitFor();
    assert.equal(await status.count(), 1);
    const bubble = page.locator("[data-user-message-bubble]").last();
    const tool = page.locator("[data-agent-tool-call-group]").first();
    if (input !== answer) assert.ok(await before(page.locator("[data-chat-date-separator]"), bubble), "a new task after an hour starts with its date separator");
    assert.equal(await status.locator("time").count(), 0, "the live tail contains only the latest status");
    assert.equal(await clock.count(), 1, "the round has only one elapsed-time header");
    assert.match(await clock.innerText(), /22\s*s/i);
    assert.ok(await before(bubble, clock), "the user input precedes elapsed time");
    assert.ok(await before(clock, tool), "elapsed time precedes tool output");
    assert.ok(await before(page.getByText("progress after request", { exact: true }), status), "latest activity follows the assistant output");
    assert.ok(await before(tool, status));
  }
  const motion = () => label.evaluate(el => getComputedStyle(el).animationName);
  assert.notEqual(await motion(), "none", "active work animates the text");
  assert.equal(await status.locator(".rounded-full").count(), 0, "no activity dot");
  await page.emulateMedia({ reducedMotion: "reduce" });
  assert.equal(await motion(), "none", "honor reduced motion");
  assert.equal(await label.evaluate(element => {
    const reference = document.createElement("span");
    reference.className = "text-muted-foreground";
    document.body.append(reference);
    const same = getComputedStyle(element).color === getComputedStyle(reference).color;
    reference.remove();
    return same;
  }), true, "the static label uses the same gray as other secondary text");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await render([user, { ...streaming, phase: "waiting_for_approval" }]);
  await page.locator('[data-vrcforge-streaming-phase="waiting_for_approval"]').waitFor();
  assert.equal(await motion(), "none", "awaiting a human decision is static");
  await render([user, { ...streaming, timeline: [] }]);
  await page.locator("[data-chat-turn-elapsed]").waitFor();
  assert.equal(await clock.count(), 1, "an empty live timeline still has one elapsed time");
  await render([user, {
    id: "done", type: "agent", completedAt: new Date(Date.parse(createdAt) + 22_000).toISOString(),
    response: { clientTurnId, plan: { reply: "Done", nextStep: "done" }, timeline },
  }]);
  await status.waitFor({ state: "detached" });
  assert.deepEqual(errors, []);
  console.log("live turn: input, elapsed time, work, latest status, active motion and reduced motion passed");
} finally {
  await browser.close();
}
