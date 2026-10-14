import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { build } from "esbuild";

const { chromium } = createRequire(import.meta.url)(
  process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright",
);

const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
import React from "react";
import { createRoot } from "react-dom/client";
import { ConversationCard } from "./src/components/chat/conversation-card";

const event = (id, sequence, kind, payload) => ({
  id,
  sequence,
  timestamp: \`2026-09-22T00:00:0\${sequence}.000Z\`,
  kind,
  payload,
});
const timeline = [
  event("public-1", 0, "assistant", { summary: "public progress 1", status: "done", phase: "commentary", messageId: "message-commentary-1" }),
  event("call-1", 1, "tool_call", { actionId: "interleaved-1", tool: "vrcforge_list_directory", status: "started" }),
  event("result-1", 2, "tool_result", { actionId: "interleaved-1", tool: "vrcforge_list_directory", status: "ok", summary: "listed" }),
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
createRoot(document.getElementById("root")).render(
  <>
    <section id="live"><ConversationCard item={item} /></section>
    <section id="restored"><ConversationCard item={restoredItem} /></section>
    <section id="streaming"><ConversationCard item={streamingItem} /></section>
    <section id="failed"><ConversationCard item={failedItem} /></section>
    <section id="commentary-only"><ConversationCard item={commentaryOnlyItem} /></section>
  </>,
);
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
  res.end(`<div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
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
      : Array.from(row.querySelectorAll("button")).some((button) => button.classList.contains("group/run"))
        ? "tool"
        : "other"
  )));
  assert.deepEqual(expandedKinds, ["progress", "tool", "progress", "tool"], "expanded browser order is progress, tool, progress, tool");
  assert.ok(expandedText.indexOf("final summary") >= 0, "expanded process leaves the final summary outside the process fold");

  await restored.locator("[data-agent-turn-process-group] button").first().click();
  const restoredExpandedText = await restored.innerText();
  assert.ok(restoredExpandedText.includes("public progress 1") && restoredExpandedText.includes("public progress 2"), "serialized process fold expands with its progress rows");

  const firstInvocation = groups.nth(0).locator("button[aria-expanded='false']").first();
  await firstInvocation.click();
  await live.getByText("FIRST_FULL_RESULT", { exact: false }).waitFor();
  assert.equal(await live.locator("[data-agent-invocation-output-card]").count(), 1, "expanded tool result is rendered once");
  assert.deepEqual(errors, []);
  console.log("interleaved timeline browser: order, expansion, and serialized restore passed");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
