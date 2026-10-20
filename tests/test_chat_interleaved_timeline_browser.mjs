import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { build } from "esbuild";
import { readFile, writeFile } from "node:fs/promises";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const { chromium } = createRequire(import.meta.url)(
  process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright",
);
const originalChat = process.env.VRCFORGE_TURN_REPLAY_PATH
  ? JSON.parse(await readFile(process.env.VRCFORGE_TURN_REPLAY_PATH, "utf8")) : null;
const css = (await postcss([tailwindcss("./tailwind.config.ts")]).process(
  (await readFile("src/theme.css", "utf8")) + "\n@tailwind base; @tailwind components; @tailwind utilities;", { from: undefined },
)).css;

const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
import React from "react";
import { createRoot } from "react-dom/client";
import { ConversationCard } from "./src/components/chat/conversation-card";
import { initializeI18n } from "./src/i18n";
import { projectConversationSteeringMessages } from "./src/lib/chat-steering-projection";
import { mergeConversationTimelineItems } from "./src/lib/chat-timeline-presentation";
import i18n from "./src/i18n";
window.turnActions = [];
const originalChat = ${JSON.stringify(originalChat)};
const originalProjection = projectConversationSteeringMessages(mergeConversationTimelineItems(
  (originalChat?.items || []).filter((item) => !item.composerQueued)));

const event = (id, sequence, kind, payload) => ({
  id,
  sequence,
  timestamp: new Date(Date.UTC(2026, 8, 22) + sequence * 1000).toISOString(),
  kind,
  payload,
});
const timeline = [
  event("public-1", 0, "assistant", { summary: "public progress 1", status: "done", phase: "commentary", messageId: "message-commentary-1" }),
  event("call-1", 1, "tool_call", { actionId: "interleaved-1", tool: "vrcforge_list_directory", status: "started" }),
  event("result-1", 2, "tool_result", { actionId: "interleaved-1", tool: "vrcforge_list_directory", status: "ok", summary: "listed" }),
  event("call-extra", 2.1, "tool_call", { actionId: "interleaved-extra", tool: "vrcforge_execute_shell", status: "started" }),
  event("result-extra", 2.2, "tool_result", { actionId: "interleaved-extra", tool: "vrcforge_execute_shell", status: "ok", summary: "shell result" }),
  event("public-2", 3, "assistant", { summary: "public progress 2", status: "done", phase: "commentary", messageId: "message-commentary-2" }),
  event("call-2", 4, "tool_call", { actionId: "interleaved-2", tool: "vrcforge_find_files", status: "started" }),
  event("result-2", 5, "tool_result", { actionId: "interleaved-2", tool: "vrcforge_find_files", status: "ok", summary: "found" }),
  event("public-final", 6, "assistant", { summary: "final summary", status: "progress", phase: "final_answer", messageId: "message-final" }),
];
const item = {
  id: "interleaved-agent",
  type: "agent",
  response: {
    clientTurnId: "interleaved-browser-turn",
    plan: { reply: "final summary", summary: "final summary", nextStep: "done", planner: "fixture", shellNeeded: false },
    timeline,
    steps: [
      { actionId: "interleaved-1", result: { text: "FIRST_FULL_RESULT" } },
      { actionId: "interleaved-extra", result: { text: "EXTRA_FULL_RESULT" } },
      { actionId: "interleaved-2", result: { text: "SECOND_FULL_RESULT" } },
    ],
  },
};
const restoredItem = JSON.parse(JSON.stringify(item));
const streamingItem = {
  id: "interleaved-streaming",
  type: "streaming",
  clientTurnId: "interleaved-streaming-turn",
  createdAt: "2026-09-22T00:00:00.000Z",
  text: "final summary",
  timeline,
};
const activeStreamingItem = {
  id: "active-streaming",
  type: "streaming",
  clientTurnId: "active-streaming-turn",
  createdAt: "2026-09-22T00:00:00.000Z",
  phase: "running_tool",
  text: "The model's original progress text stays visible.",
  timeline: [event("active-call", 1, "tool_call", { actionId: "active-action", tool: "vrcforge_list_directory", status: "started" })],
};
const resumedStreamingItem = {
  ...activeStreamingItem,
  id: "resumed-streaming",
  clientTurnId: "resumed-streaming-turn",
  phase: "waiting_for_model",
};
const failedItem = {
  ...item,
  id: "interleaved-failed",
  response: {
    ...item.response,
    plan: { ...item.response.plan, plannerFailure: { code: "provider_timeout" } },
  },
};
const commentaryOnlyItem = {
  id: "interleaved-commentary-only",
  type: "agent",
  response: {
    clientTurnId: "interleaved-commentary-only-turn",
    plan: { reply: "", summary: "The turn ended without a final answer.", reason: "native_final_answer_missing", nextStep: "needs_user_action", planner: "fixture", shellNeeded: false },
    timeline: [
      event("commentary-only", 0, "assistant", { summary: "same public commentary", status: "done", phase: "commentary", messageId: "message-commentary-only" }),
    ],
  },
};
const steeringUser = {
  id: "user-interleaved-steering",
  type: "user",
  text: "Please check the menu too.",
  clientTurnId: "interleaved-steering-client-turn",
  createdAt: new Date(Date.UTC(2026, 8, 22) + 2500).toISOString(),
};
const userTrigger = {
  id: "user-interleaved-browser-turn",
  type: "user",
  text: "Inspect the outfit.",
  clientTurnId: "interleaved-browser-turn",
  createdAt: new Date(Date.UTC(2026, 8, 22)).toISOString(),
};
const steeringProjection = projectConversationSteeringMessages([userTrigger, steeringUser, item]);
const steeringAgent = steeringProjection.items.find((candidate) => candidate.type === "agent");
const interleavedSteeringMessages = steeringProjection.messagesByTurnId.get("interleaved-browser-turn") || [];
function InitialUser() {
  const [editing, setEditing] = React.useState(false);
  const [text, setText] = React.useState(userTrigger.text);
  return <div data-turn-initial-user><ConversationCard item={{...userTrigger,
    attachments:[{id:"turn-attachment",name:"original.txt",type:"text/plain",size:4,text:"note"}]}}
    canRetry canEdit editing={editing} editingText={text} onEditTextChange={setText}
    onCopyItem={(entry) => window.turnActions.push(["copy", entry.id])}
    onRetryItem={(id) => window.turnActions.push(["retry", id])}
    onEditItem={(id) => {window.turnActions.push(["edit", id]);setEditing(true);}}
    onEditItemSave={() => {window.turnActions.push(["save",text]);setEditing(false);}}
    onEditItemCancel={() => setEditing(false)} /></div>;
}
window.turnLabels = () => ({copy:i18n.t("chat.copyMessage"),retry:i18n.t("chat.retryMessage"),edit:i18n.t("chat.editMessage"),save:i18n.t("chat.saveEdit")});
void initializeI18n().then(() => createRoot(document.getElementById("root")).render(
  <>
    <section id="live"><ConversationCard item={item} /></section>
    <section id="restored"><ConversationCard item={restoredItem} /></section>
    <section id="streaming"><ConversationCard item={streamingItem} /></section>
    <section id="active-streaming"><ConversationCard item={activeStreamingItem}
      initialUserContent={<div data-turn-initial-user><ConversationCard item={userTrigger} /></div>} /></section>
    <section id="resumed-streaming"><ConversationCard item={resumedStreamingItem} /></section>
    <section id="failed"><ConversationCard item={failedItem} /></section>
    <section id="commentary-only"><ConversationCard item={commentaryOnlyItem} /></section>
    <section id="steering-expanded" data-top-level-user-count={steeringProjection.items.filter((entry) => entry.type === "user").length}>
      <ConversationCard item={steeringAgent} interleavedUserMessages={interleavedSteeringMessages}
        initialUserContent={<InitialUser />} />
    </section>
    <section id="original-replay">{originalProjection.items.map((entry) => {
      const turnId = entry.type === "agent" ? entry.response.clientTurnId : entry.clientTurnId;
      const initial = originalProjection.initialUsersByTurnId.get(turnId);
      return <div key={entry.id} data-original-turn><ConversationCard item={entry}
        interleavedUserMessages={originalProjection.messagesByTurnId.get(turnId)}
        initialUserContent={initial ? <div data-turn-initial-user><ConversationCard item={initial} /></div> : undefined} /></div>;
    })}</section>
  </>,
));
`,
  },
  bundle: true,
  write: false,
  format: "iife",
  jsx: "automatic",
  platform: "browser",
});

const server = createServer((_req, res) => {
  res.setHeader("Content-Type", "text/html");
  res.end(`<style>${css}</style><div id="root" style="width:768px;margin:24px auto"></div><script>${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
});

let browser;
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({
    headless: true,
    channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge",
  });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.locator("#live [data-vrcforge-agent-client-turn]").waitFor();

  const live = page.locator("#live");
  const restored = page.locator("#restored");
  const streaming = page.locator("#streaming");
  const activeStreaming = page.locator("#active-streaming");
  const resumedStreaming = page.locator("#resumed-streaming");
  assert.equal(await live.locator("[data-agent-turn-process-group]").count(), 1, "completed turn has one collapsed process fold");
  assert.equal(await restored.locator("[data-agent-turn-process-group]").count(), 1, "serialized timeline keeps one process fold");
  assert.equal(await live.locator("[data-agent-turn-process-group] button").first().getAttribute("aria-expanded"), "false", "completed process fold is collapsed by default");
  const liveText = await live.innerText();
  const restoredText = await restored.innerText();
  assert.deepEqual(restoredText, liveText, "serialized durable projection restores the same visible order");
  assert.equal((liveText.match(/final summary/g) || []).length, 1, "completed summary remains visible");
  assert.equal((liveText.match(/public progress [12]/g) || []).length, 0, "collapsed completed process hides progress rows");
  assert.equal((liveText.match(/vrcforge_(list_directory|find_files)/g) || []).length, 0, "collapsed completed process hides tool rows");
  const streamingText = await streaming.innerText();
  assert.equal(await streaming.locator("[data-agent-turn-process-group]").count(), 1, "streaming turn has one process fold");
  assert.equal(await streaming.locator("[data-agent-turn-process-group] button").first().getAttribute("aria-expanded"), "true", "streaming process fold is open");
  assert.equal((streamingText.match(/public progress 1/g) || []).length, 1, "streaming keeps the first public update");
  assert.equal((streamingText.match(/public progress 2/g) || []).length, 1, "streaming keeps the second public update");
  assert.equal((streamingText.match(/final summary/g) || []).length, 1, "streaming suppresses only the duplicate trailing assistant text");
  assert.equal(await page.locator("#failed [data-vrcforge-terminal-status]").count(), 1, "terminal failure styling belongs only to the final assistant row");
  const commentaryOnly = page.locator("#commentary-only");
  assert.equal(await commentaryOnly.locator("[data-agent-turn-process-group]").count(), 1, "commentary-only turns retain an expandable process fold");
  assert.equal((await commentaryOnly.innerText()).includes("The turn ended without a final answer."), false, "commentary-only plan summary is not promoted to a final answer");
  assert.equal(await commentaryOnly.locator("[data-vrcforge-missing-final-answer]").count(), 1, "commentary-only turns show the local missing-final status");
  assert.ok((await commentaryOnly.locator("[data-vrcforge-missing-final-answer]").innerText()).trim(), "missing-final status is visible without fabricating a model answer");
  const steeringExpanded = page.locator("#steering-expanded");
  assert.equal(await steeringExpanded.getAttribute("data-top-level-user-count"), "0", "the initial user belongs to its owning turn");
  assert.equal(await steeringExpanded.locator("[data-turn-initial-user]").count(), 1, "initial user remains visible once while collapsed");
  assert.equal(await steeringExpanded.locator("[data-chat-steering-message]").count(), 1, "sent steering message is projected into its owning turn");
  assert.equal((await steeringExpanded.innerText()).includes("Please check the menu too."), true, "collapsed turn retains the steering message");
  assert.equal((await steeringExpanded.innerText()).includes("public progress"), false, "collapsed turn hides commentary while retaining steering");
  const steeringGroup = steeringExpanded.locator("[data-agent-turn-process-group]");
  const assertTurnLayout = async (turn) => {
    const header = await turn.locator("[data-agent-turn-process-group] button").first().boundingBox();
    const initial = await turn.locator("[data-turn-initial-user] [data-user-message-bubble]").boundingBox();
    const steers = await turn.locator("[data-chat-steering-message] [data-user-message-bubble]").all();
    assert.ok(header.y + header.height <= initial.y, "fold header precedes initial user");
    for (const steer of steers) {
      const rect = await steer.boundingBox();
      assert.ok(Math.abs(rect.x + rect.width - initial.x - initial.width) < 1, "initial and steering bubbles share the right edge");
      assert.ok(rect.y > initial.y, "steering follows initial user");
    }
  };
  await assertTurnLayout(steeringExpanded);
  await steeringGroup.locator("button").first().click();
  const steeringRows = await steeringGroup.locator("[data-agent-process-content] > div").evaluateAll((rows) => rows.map((row) => (
    row.hasAttribute("data-chat-steering-message")
      ? "steer"
      : row.querySelector("[data-testid='chat-markdown-agent']")
      ? "progress"
      : row.hasAttribute("data-agent-tool-call-group")
        ? "tool"
        : "other"
  )));
  assert.deepEqual(steeringRows, ["progress", "tool", "steer", "progress", "tool"], "expanded turn inserts steering between runtime events by timestamp");
  await assertTurnLayout(steeringExpanded);
  await steeringGroup.locator("button").first().click();
  await assertTurnLayout(steeringExpanded);
  const initialUser = steeringExpanded.locator("[data-turn-initial-user]");
  const labels = await page.evaluate(() => window.turnLabels());
  assert.ok((await initialUser.innerText()).includes("original.txt"), "initial attachment survives turn projection");
  await initialUser.getByRole("button",{name:labels.copy,exact:true}).click();
  await initialUser.getByRole("button",{name:labels.retry,exact:true}).click();
  await initialUser.getByRole("button",{name:labels.edit,exact:true}).click();
  await initialUser.locator("textarea").fill("Updated initial user");
  await initialUser.getByRole("button",{name:labels.save,exact:true}).click();
  assert.deepEqual(await page.evaluate(() => window.turnActions),[
    ["copy","user-interleaved-browser-turn"],["retry","user-interleaved-browser-turn"],
    ["edit","user-interleaved-browser-turn"],["save","Updated initial user"],
  ], "initial message copy/retry/edit/save keep their original item identity");
  assert.equal(await activeStreaming.locator("[data-turn-initial-user]").count(),1,"streaming initial user survives when no process fold exists");
  if (originalChat) {
    const original = page.locator("#original-replay");
    assert.equal(await original.locator("[data-turn-initial-user]").count(), 2, "both original turn triggers render once");
    assert.equal(await original.locator("[data-chat-steering-message]").count(), 1, "original sent steer stays in its turn; cancelled queue is excluded");
    const originalTurn = original.locator("[data-original-turn]").first();
    await assertTurnLayout(originalTurn);
    const originalText = await original.innerText();
    for (const item of originalChat.items.filter((entry) => entry.type === "agent")) {
      assert.ok(originalText.includes(item.response.plan.reply.slice(-60).replaceAll("`", "")), "original final answer remains visible outside fold");
    }
    await originalTurn.locator("[data-agent-turn-process-group] button").first().click();
    await assertTurnLayout(originalTurn);
    if (process.env.VRCFORGE_TURN_EVIDENCE_DIR) {
      await original.screenshot({path: process.env.VRCFORGE_TURN_EVIDENCE_DIR + "/original-expanded.png"});
      await originalTurn.locator("[data-agent-turn-process-group] button").first().click();
      await original.screenshot({path: process.env.VRCFORGE_TURN_EVIDENCE_DIR + "/original-collapsed.png"});
      await writeFile(process.env.VRCFORGE_TURN_EVIDENCE_DIR + "/browser-validation.json", JSON.stringify({originalChatId:originalChat.id, initialUsers:2, steeringUsers:1, headerBeforeAllUsers:true,rightEdgeEqual:true,finalAnswersVisible:true,collapsedAndExpanded:true},null,2));
    }
  }
  await commentaryOnly.locator("[data-agent-turn-process-group] button").first().click();
  assert.equal((await commentaryOnly.innerText()).match(/same public commentary/g)?.length, 1, "commentary-only text remains visible after expansion");

  const groups = live.locator("[data-agent-turn-process-group]");
  assert.equal(await groups.nth(0).locator("button svg").count(), 1, "process header has one always-visible chevron");
  assert.doesNotMatch(await groups.nth(0).locator("button svg").first().getAttribute("class"), /opacity-0/, "process chevron is not hover-only");
  await groups.nth(0).locator("button").first().click();
  const expandedText = await live.innerText();
  const expandedKinds = await groups.nth(0).locator("div.mt-3 > div").evaluateAll((rows) => rows.map((row) => (
    row.querySelector("[data-testid='chat-markdown-agent']")
      ? "progress"
      : row.hasAttribute("data-agent-tool-call-group")
        ? "tool"
        : "other"
  )));
  assert.deepEqual(expandedKinds, ["progress", "tool", "progress", "tool"], "expanded browser order is progress, tool, progress, tool");
  assert.ok(expandedText.indexOf("final summary") >= 0, "expanded process leaves the final summary outside the process fold");

  await restored.locator("[data-agent-turn-process-group] button").first().click();
  const restoredExpandedText = await restored.innerText();
  assert.ok(restoredExpandedText.includes("public progress 1") && restoredExpandedText.includes("public progress 2"), "serialized process fold expands with its progress rows");

  const toolGroups = groups.nth(0).locator("[data-agent-tool-call-group]");
  assert.equal(await toolGroups.count(), 2, "assistant prose splits consecutive tool groups");
  assert.equal(await toolGroups.nth(0).locator("button").first().getAttribute("aria-expanded"), "false", "consecutive tools start collapsed");
  assert.equal(await toolGroups.nth(0).locator("button.group\\/run").count(), 0, "collapsed group hides individual calls");
  assert.equal(await streaming.locator("[data-agent-tool-call-group]").count(), 2, "streaming uses the same groups");
  assert.equal(await streaming.locator("[data-agent-tool-call-group] button").first().getAttribute("aria-expanded"), "false", "streaming does not force tool group open");
  const activeStatus = activeStreaming.locator("[data-chat-current-activity]");
  assert.equal(await activeStatus.count(), 1, "one current status is shown for the active turn");
  assert.equal(await activeStreaming.locator("[data-conversation-streaming-turn] > :last-child > [data-chat-current-activity]").count(), 1, "the current status sits after timeline and model text");
  assert.match(await activeStatus.innerText(), /vrcforge_list_directory/, "running tool status comes from the live tool event");
  assert.match(await activeStreaming.innerText(), /The model's original progress text stays visible\./, "the status does not replace model progress text");
  assert.equal(await resumedStreaming.locator("[data-chat-current-activity]").getAttribute("data-vrcforge-streaming-phase"), "waiting_for_model", "the current status follows the latest runtime phase");
  assert.doesNotMatch(await resumedStreaming.locator("[data-chat-current-activity]").innerText(), /vrcforge_list_directory/, "a completed prior tool does not remain as the current status");
  assert.equal(await live.locator("[data-chat-current-activity]").count(), 0, "completed turns collapse the live status");
  await page.emulateMedia({ reducedMotion: "reduce" });
  assert.equal(await activeStatus.locator(".chat-current-activity__label").evaluate((element) => getComputedStyle(element).animationName), "none", "reduced motion disables the shimmer");
  await toolGroups.nth(0).locator("button").first().click();
  const calls = toolGroups.nth(0).locator("button.group\\/run");
  assert.equal(await calls.count(), 2, "mixed tool kinds share one consecutive group");
  const firstInvocation = calls.first();
  await firstInvocation.click();
  await live.getByText("FIRST_FULL_RESULT", { exact: false }).waitFor();
  assert.equal(await live.locator("[data-agent-invocation-output-card]").count(), 1, "expanded tool result is rendered once");
  await calls.nth(1).click();
  await live.getByText("EXTRA_FULL_RESULT", { exact: false }).waitFor();
  assert.equal(await live.locator("[data-agent-invocation-output-card]").count(), 2, "all grouped results remain inspectable");
  assert.deepEqual(errors, []);
  console.log("interleaved timeline browser: order, expansion, and serialized restore passed");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
