import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { basename, join } from "node:path";
import { build } from "esbuild";
import { chromium } from "playwright";

const original = process.env.VRCFORGE_APPROVAL_REPLAY_DIR;
const read = async (name) => JSON.parse(await readFile(join(original, name), "utf8"));
const approval = {
  id: "approval-restore", status: "pending", targetTool: "vrcforge_restore_checkpoint",
  arguments: { checkpointId: "checkpoint-fixture" },
};
const receiptText = "Diagnostic supplied by the pending approval tool";
const receipt = { status: "pending", approval, message: receiptText };
const model = "provider-model-fixture";
const event = (sequence, kind, payload) => ({
  sequence, id: `event-${sequence}`, timestamp: `2026-11-19T08:00:${sequence}Z`, kind, payload,
});
const fixture = {
  id: "parent",
  type: "agent",
  response: {
    model, approvalId: approval.id, result: receipt,
    clientTurnId: "parent-client", sessionId: "fixture-session", turnId: "parent",
    plan: { summary: receiptText, reply: receiptText, nextStep: "needs_user_action", completionGate: { reason: "approval_pending" } },
    steps: [{ kind: "write", tool: approval.targetTool, actionId: "restore", status: "approval_pending", result: receipt }],
    timeline: [
      event(0, "assistant", { phase: "commentary", status: "progress", summary: "先检查当前场景。", messageId: "progress" }),
      event(1, "tool_call", { actionId: "restore", tool: approval.targetTool, status: "prepared" }),
      event(2, "tool_result", { actionId: "restore", tool: approval.targetTool, status: "needs_user_action", summary: receiptText }),
      event(3, "assistant", { phase: "final_answer", status: "needs_user_action", summary: receiptText }),
    ],
  },
};
const wait = original
  ? (await read("chat.json")).items.find(item => item.response?.plan.completionGate?.reason === "approval_pending")
  : fixture;
const pending = original ? wait.response.result.approval : approval;
const runs = original ? (await read("runs.json")).runs
  : [{ approvalId: approval.id, event: "approval_applied", status: "applied", completionStatus: "ok" }];
const continued = original ? (await read("continued-timeline.json")).events : [
  event(5, "assistant", { phase: "commentary", status: "progress", summary: "已完成回退。", messageId: "resume" }),
];
const continuationText = continued.find(item => item.kind === "assistant").payload.summary;
const progress = wait.response.timeline.filter(item => item.kind === "assistant"
  && item.payload.phase === "commentary").map(item => item.payload.summary);
const genuineText = "请核对申请里的目标。";
const genuine = structuredClone(wait);
genuine.response.plan.reply = genuineText;
genuine.response.plan.summary = genuineText;
// Historical providers can have model words without a native message ID.
genuine.response.timeline.find(item => item.kind === "assistant"
  && item.payload.status === "needs_user_action").payload.summary = genuineText;

const baselineFiles = {
  "conversation-card.tsx": "src/components/chat/conversation-card.tsx",
  "conversation-timeline.tsx": "src/components/chat/conversation-timeline.tsx",
  "chat-timeline-presentation.ts": "src/lib/chat-timeline-presentation.ts",
  "conversation-utils.ts": "src/lib/conversation-utils.ts",
};
const baselineSource = process.env.VRCFORGE_APPROVAL_BASELINE;
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React from "react";
    import { createRoot } from "react-dom/client";
    import { ConversationCard } from "./src/components/chat/conversation-card";
    import { agentReplyPreviewText, copyableAgentDialogueText } from "./src/lib/conversation-utils";
    import { initializeI18n, setLocale } from "./src/i18n";
    const waiting = ${JSON.stringify(wait)};
    const approval = ${JSON.stringify(pending)};
    const runs = ${JSON.stringify(runs)};
    const resolved = { first: true, last: false, failures: 0, open: true, live: false };
    const child = { id: "continued", type: "streaming", text: "", phase: "running_tool", clientTurnId: "continued-client", timeline: ${JSON.stringify(continued)} };
    void initializeI18n().then(() => setLocale("zh-CN")).then(() => {
      window.spoken = [agentReplyPreviewText(waiting.response), copyableAgentDialogueText(waiting.response)];
      createRoot(document.getElementById("resolved")).render(
        <><ConversationCard item={waiting} sharedProcess={resolved} approvalRuns={runs} />
        <ConversationCard item={child} sharedProcess={{...resolved, first: false, last: true}} runtimeActive /></>);
      createRoot(document.getElementById("pending")).render(<ConversationCard item={waiting} sharedProcess={resolved} approval={approval} />);
      createRoot(document.getElementById("historic")).render(<ConversationCard item={waiting} sharedProcess={resolved} />);
      createRoot(document.getElementById("genuine")).render(<ConversationCard item={${JSON.stringify(genuine)}} sharedProcess={resolved} />);
      createRoot(document.getElementById("approved")).render(<ConversationCard item={waiting} sharedProcess={resolved} approvalRuns={[
        {approvalId: approval.id, event: "approval_applied", status: "needs_user_action", completionStatus: "needs_user_action"},
        {approvalId: approval.id, event: "approval_approved", status: "approved"},
      ]} />);
    });
  ` },
  plugins: baselineSource ? [{
    name: "approval-baseline",
    setup(builder) {
      builder.onLoad({ filter: /conversation-card\.tsx$|conversation-timeline\.tsx$|chat-timeline-presentation\.ts$|conversation-utils\.ts$/ }, ({ path }) => ({
        contents: execFileSync("git", ["show", `${baselineSource}:${baselineFiles[basename(path)]}`], { encoding: "utf8" }),
        resolveDir: join(path, ".."),
        loader: path.endsWith(".tsx") ? "tsx" : "ts",
      }));
    },
  }] : [],
  bundle: true, write: false, jsx: "automatic", format: "iife", platform: "browser",
});

const server = createServer((_request, response) => {
  response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  response.end(`<!DOCTYPE html><meta charset="utf-8">
    <section id="resolved"></section><section id="pending"></section><section id="historic"></section>
    <section id="genuine"></section><section id="approved"></section>
    <script>${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
});
let browser;
try {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ headless: true, channel: "msedge" });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  const owner = page.locator("#resolved > [data-vrcforge-agent-client-turn]").first();
  const raw = wait.response.plan.reply;
  await owner.waitFor();
  const resolvedText = await owner.innerText();
  assert.equal(resolvedText.includes(raw), false, "The tool's approval diagnostic cannot become an assistant reply");
  assert.equal(resolvedText.includes(wait.response.model), false, "The waiting turn has no final reply to attribute");
  assert.ok(resolvedText.includes("恢复项目检查点"));
  assert.ok(resolvedText.includes("已执行"));
  for (const message of progress) assert.ok(resolvedText.includes(message.replaceAll("**", "").slice(0, 12)));
  assert.ok((await page.locator("#resolved").innerText()).includes(continuationText.slice(0, 12)));
  assert.match(await page.locator("#pending").innerText(), /需要审批/);
  const historic = await page.locator("#historic").innerText();
  assert.match(historic, /审批请求/);
  assert.doesNotMatch(historic, /需要审批|正在等待审批|已执行/);
  assert.match(await page.locator("#approved").innerText(), /已批准/);
  assert.doesNotMatch(await page.locator("#approved").innerText(), /已执行/);
  const genuineReply = await page.locator("#genuine").innerText();
  assert.ok(genuineReply.includes(genuineText) && genuineReply.includes(wait.response.model));
  for (const text of await page.evaluate(() => window.spoken)) {
    assert.equal(text.includes(raw), false);
  }
  assert.deepEqual(errors, []);
  if (original) {
    await page.locator("#resolved").screenshot({ path: join(original, "replay.png") });
    await writeFile(join(original, "validation.json"), JSON.stringify({
      rawApprovalFinalHidden: true, unattachedModelMetadataHidden: true, receiptApplied: true,
      providerCommentaryPreserved: true, continuationCommentaryPreserved: true, historyWithoutOutcomeNeutral: true,
      approvalWithoutVerifiedCompletionShownAsApproval: true, genuineFinalModelAttributionPreserved: true,
    }, null, 2));
  }
  console.log("Approval boundary transcript replay passed");
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
