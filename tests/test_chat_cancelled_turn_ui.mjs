import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const cancelled = (item) => item?.type === "agent" && (
  item.response?.status === "cancelled" || item.response?.plan?.nextStep === "cancelled"
);
// Optional read-only replay of a real transcript store. Private records stay out
// of the test fixture and are never written back or sent to a provider.
const replayStore = process.env.VRCFORGE_CANCEL_REPLAY_PATH
  ? JSON.parse(await readFile(process.env.VRCFORGE_CANCEL_REPLAY_PATH, "utf8")) : null;
const replayItems = replayStore
  ? (replayStore.chats || [replayStore]).flatMap((chat) => chat.items || []).filter(cancelled) : [];
if (replayStore) assert.ok(replayItems.length, "the original store must contain cancelled turns to replay");
const event = (id, sequence, kind, payload) => ({
  id, sequence, kind, payload, timestamp: new Date(Date.UTC(2026, 9, 23) + sequence * 1000).toISOString(),
});
const base = {
  id: "cancelled-empty", type: "agent",
  response: {
    clientTurnId: "cancelled-empty", status: "cancelled",
    plan: { reply: "Request cancelled.", summary: "Runtime turn was cancelled by the user.",
      nextStep: "cancelled", planner: "runtime", shellNeeded: false },
  },
};
const marker = event("marker", 9, "assistant", { summary: "Request cancelled.", status: "cancelled" });
const tool = [
  event("tool-call", 1, "tool_call", { actionId: "read-action", tool: "vrcforge_inspect_avatar", status: "started" }),
  event("tool-result", 2, "tool_result", { actionId: "read-action", tool: "vrcforge_inspect_avatar", status: "ok", summary: "read completed" }),
];
const commentary = event("commentary", 0, "assistant", {
  summary: "I have checked the original file.", phase: "commentary", status: "done",
});
const variant = (id, response) => ({ ...base, id, response: { ...base.response, clientTurnId: id, ...response } });
const fixtures = [
  { name: "empty", item: base },
  { name: "steps-only", item: variant("steps-only", { steps: [{
    kind: "skill", actionId: "read-action", tool: "vrcforge_inspect_avatar", status: "ok",
    result: { text: "READ_RESULT_RETAINED" },
  }] }), tools: true },
  { name: "commentary", item: variant("commentary", {
    timeline: [commentary, ...tool, marker],
    steps: [{ actionId: "read-action", result: { text: "READ_RESULT_RETAINED" } }],
  }), tools: true, spoken: commentary.payload.summary, expectedInvocationStatusKey: "step.done", expectedInvocationSuccess: true },
  { name: "accepted-final", item: variant("accepted-final", {
    timeline: [commentary, ...tool,
      event("accepted", 3, "assistant", { summary: "The original file is valid.", phase: "final_answer", status: "done" }), marker],
  }), tools: true, final: "The original file is valid." },
  { name: "partial-reply", item: variant("partial-reply", {
    plan: { ...base.response.plan, reply: "I checked the first renderer before stopping." },
  }), final: "I checked the first renderer before stopping." },
  { name: "gateway-partial-reply", item: variant("gateway-partial-reply", {
    plan: { ...base.response.plan, reply: "I checked the first renderer before stopping." },
    timeline: [event("gateway-cancelled-final", 1, "assistant", {
      summary: "I checked the first renderer before stopping.", phase: "final_answer", status: "cancelled",
    })],
  }), final: "I checked the first renderer before stopping." },
  { name: "unaccepted-final", item: variant("unaccepted-final", {
    plan: { ...base.response.plan, reply: "UNACCEPTED_FINAL_MUST_NOT_APPEAR" },
    timeline: [commentary, ...tool,
      event("unaccepted", 3, "assistant", { summary: "UNACCEPTED_FINAL_MUST_NOT_APPEAR", phase: "final_answer", status: "completion_unverified" }), marker],
  }), tools: true, spoken: commentary.payload.summary },
  { name: "pending-tool", item: variant("pending-tool", {
    timeline: [event("pending-call", 1, "tool_call", {
      actionId: "pending-action", tool: "vrcforge_inspect_avatar", status: "started",
    }), marker],
  }), tools: true, expectedInvocationStatusKey: "subagent.statusInterrupted" },
  ...["cancelled", "rejected", "interrupted"].map((status) => ({
    name: `terminal-tool-${status}`, item: variant(`terminal-tool-${status}`, {
      timeline: [tool[0], event(`tool-result-${status}`, 2, "tool_result", {
        actionId: "read-action", tool: "vrcforge_inspect_avatar", status,
        summary: "The tool did not complete successfully.",
      }), marker],
    }), tools: true, expectedInvocationStatusKey: status === "cancelled" ? "subagent.statusCancelled"
      : status === "rejected" ? "agent.rejected" : "subagent.statusInterrupted",
  })),
  ...replayItems.map((item, index) => ({ name: `original-${index}`, item, original: true })),
];
const css = (await postcss([tailwindcss("./tailwind.config.ts")]).process(
  (await readFile("src/theme.css", "utf8")) + "\n@tailwind base; @tailwind components; @tailwind utilities;", { from: undefined },
)).css;
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
import React from "react";
import { createRoot } from "react-dom/client";
import { ConversationCard } from "./src/components/chat/conversation-card";
import { initializeI18n } from "./src/i18n";
import { buildChatHistory, copyableAgentDialogueText } from "./src/lib/conversation-utils";
import i18n from "./src/i18n";
const fixtures = ${JSON.stringify(fixtures)};
window.cancelledLabel = () => i18n.t("chat.cancelled");
window.invocationStatusLabel = (key) => i18n.t(key);
window.fixtureText = (name) => {
  const item = fixtures.find((fixture) => fixture.name === name).item;
  return { copy: copyableAgentDialogueText(item.response), history: buildChatHistory([item], i18n.t).map((entry) => entry.text).join("\\n\\n") };
};
window.fixtureUnchanged = () => JSON.stringify(fixtures) === ${JSON.stringify(JSON.stringify(fixtures))};
void initializeI18n().then(() => createRoot(document.getElementById("root")).render(
  <>{fixtures.map(({ name, item }) => <section key={name} id={name} className="mb-8"><ConversationCard item={item} /></section>)}</>
));
` },
  bundle: true, write: false, format: "iife", jsx: "automatic", platform: "browser",
});
const server = createServer((_req, res) => {
  res.setHeader("Content-Type", "text/html");
  res.end(`<style>${css}</style><div id="root" style="width:768px;margin:24px auto"></div><script>${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
});
let browser;
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage({ viewport: { width: 1000, height: 1000 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.locator("#empty [data-vrcforge-agent-client-turn]").waitFor();
  const cancelledLabel = await page.evaluate(() => window.cancelledLabel());
  const failures = [];
  for (const fixture of fixtures) {
    try {
      const card = page.locator(`#${fixture.name}`);
      assert.doesNotMatch(await card.innerText(), /Runtime turn was cancelled by the user\.|Completed or in flight before cancellation:|Request cancelled\.|\bplanned\b/i,
        `${fixture.name}: cancellation is a local status, not generated assistant prose or a planned action`);
      const status = card.locator("[data-agent-turn-cancelled]");
      assert.equal(await status.count(), 1, `${fixture.name}: one terminal cancellation status`);
      assert.equal((await status.innerText()).trim(), cancelledLabel, `${fixture.name}: uses the localized cancellation label`);
      assert.equal(await status.locator("[data-testid='chat-markdown-agent']").count(), 0, "status must be outside model Markdown");
      assert.doesNotMatch(await status.getAttribute("class") || "", /destructive|amber|red-/, "user cancellation has neutral styling");
      const text = await page.evaluate((name) => window.fixtureText(name), fixture.name);
      assert.doesNotMatch(text.copy, /Runtime turn was cancelled by the user\.|Completed or in flight before cancellation:|Request cancelled\.|UNACCEPTED_FINAL_MUST_NOT_APPEAR|READ_RESULT_RETAINED/,
        `${fixture.name}: copy includes model dialogue without app status or tool output`);
      if (["empty", "steps-only"].includes(fixture.name)) assert.equal(text.copy, "", `${fixture.name}: status-only turns have no fabricated reply to copy`);
      if (fixture.final) assert.equal(text.copy.split(fixture.final).length - 1, 1, `${fixture.name}: copy retains the model answer exactly once`);
      if (fixture.spoken || fixture.name === "accepted-final") assert.ok(text.copy.includes(commentary.payload.summary), `${fixture.name}: copy retains original commentary`);
      if (["steps-only", "commentary", "unaccepted-final"].includes(fixture.name)) {
        assert.match(text.history, /Runtime turn was cancelled by the user\./, `${fixture.name}: model history still records the stop`);
        assert.match(text.history, /Completed or in flight before cancellation:/, `${fixture.name}: model history still records completed or in-flight work`);
        assert.match(text.history, /inspect_avatar/, `${fixture.name}: tool history is retained for the next turn`);
      }
      assert.doesNotMatch(text.history, /UNACCEPTED_FINAL_MUST_NOT_APPEAR/, `${fixture.name}: an unaccepted final is not injected into model history`);
      if (fixture.final) assert.equal((await card.innerText()).split(fixture.final).length - 1, 1, `${fixture.name}: real model answer survives once`);
      const group = card.locator("[data-agent-turn-process-group]");
      if (fixture.tools) assert.equal(await group.count(), 1, `${fixture.name}: tool history remains in one process fold`);
      if (await group.count()) {
        const toggle = group.locator("button").first();
        assert.equal(await toggle.getAttribute("aria-expanded"), "false", `${fixture.name}: cancelled process is collapsed by default`);
        await toggle.click();
        if (fixture.spoken) assert.equal((await card.innerText()).split(fixture.spoken).length - 1, 1, `${fixture.name}: original commentary survives once`);
        assert.doesNotMatch(await card.innerText(), /UNACCEPTED_FINAL_MUST_NOT_APPEAR|Completed or in flight before cancellation:|Request cancelled\./,
          `${fixture.name}: expansion does not resurrect cancellation prose or an unaccepted final`);
        for (const calls of await group.locator("[data-agent-tool-call-group]").all()) {
          await calls.locator("button").first().click();
        }
        if (fixture.tools) assert.match(await group.innerText(), /inspect_avatar/, `${fixture.name}: original tools remain inspectable`);
        if (fixture.name === "commentary") {
          await group.locator("button.group\\/run").first().click();
          assert.match(await group.innerText(), /READ_RESULT_RETAINED/, "expanding the tool keeps its full original result");
        }
        if (fixture.expectedInvocationStatusKey) {
          if (fixture.name !== "commentary") await group.locator("button.group\\/run").first().click();
          const detail = group.locator("[data-agent-invocation-output-card]").first();
          const footer = detail.locator(":scope > div").last();
          const label = await page.evaluate((key) => window.invocationStatusLabel(key), fixture.expectedInvocationStatusKey);
          assert.equal((await footer.innerText()).trim(), label,
            `${fixture.name}: terminal tool detail shows its actual outcome instead of started or done`);
          assert.equal(await footer.locator("svg.lucide-check").count(), fixture.expectedInvocationSuccess ? 1 : 0,
            `${fixture.name}: success check is reserved for explicitly completed tools`);
          assert.doesNotMatch(await footer.getAttribute("class") || "", /amber|yellow/,
            `${fixture.name}: settled tool must not remain in a running visual state`);
        }
        assert.equal(await status.count(), 1, `${fixture.name}: expansion never duplicates the status`);
        await toggle.click();
      }
    } catch (error) {
      failures.push(`${fixture.name}: ${error.message}`);
    }
  }
  assert.deepEqual(failures, [], "all cancelled-turn rendering routes must pass");
  assert.equal(await page.evaluate(() => window.fixtureUnchanged()), true, "rendering must not mutate original durable transcript records");
  assert.deepEqual(errors, []);
  if (process.env.VRCFORGE_CANCEL_EVIDENCE_DIR) {
    await mkdir(process.env.VRCFORGE_CANCEL_EVIDENCE_DIR, { recursive: true });
    await page.screenshot({ path: `${process.env.VRCFORGE_CANCEL_EVIDENCE_DIR}/cancelled-turns.png`, fullPage: true });
    await writeFile(`${process.env.VRCFORGE_CANCEL_EVIDENCE_DIR}/browser-validation.json`, JSON.stringify({
      fixtures: fixtures.map(({ name }) => name), originalCancelledRecords: replayItems.length,
      neutralStatus: true, collapsedProcess: true, modelProsePreserved: true, toolResultPreserved: true, originalsUnchanged: true,
      modelOnlyCopy: true, cancellationAndWorkRetainedInHistory: true,
    }, null, 2));
  }
  console.log(`cancelled turn UI: ${fixtures.length} cases passed (${replayItems.length} original records replayed)`);
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
