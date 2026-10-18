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
import { initReactI18next } from "react-i18next";
import i18n from "./src/i18n";
import zhCN from "./src/locales/zh-CN.json";
import { ConversationCard } from "./src/components/chat/conversation-card";

const event = (id, sequence, kind, payload) => ({
  id,
  sequence,
  timestamp: new Date(Date.UTC(2026, 0, 1, 12, 0) + (sequence - 69) * 1000).toISOString(),
  kind,
  payload,
});
// Sanitized fixture derived from a real failed-read/recovery sequence. Runtime
// identities, timestamps and incidental text are replaced; operative statuses,
// tool names and error messages remain. Exact live replay is retained privately.
const timeline = [
  event("timeline-fixture-69", 69, "tool_call", { label: "vrcforge_get_gameobject", tool: "vrcforge_get_gameobject", status: "started", actionId: "action-fixture-1", summary: "Inspect the current scene state." }),
  event("timeline-fixture-70", 70, "tool_result", { label: "vrcforge_get_gameobject", tool: "vrcforge_get_gameobject", status: "failed", actionId: "action-fixture-1", summary: "Unity MCP identity lock rejected this reference: ExecutionTarget scene revision changed." }),
  event("timeline-fixture-71", 71, "file_edit", { label: "vrcforge_add_component", tool: "vrcforge_add_component", status: "started", actionId: "action-fixture-2", summary: "Prepared supervised execution for unity_add_component." }),
  event("timeline-fixture-72", 72, "tool_result", { label: "vrcforge_add_component", tool: "vrcforge_add_component", status: "ok", actionId: "action-fixture-2", summary: "Add a component of a given type to a scene GameObject through VRCForge." }),
  event("timeline-fixture-73", 73, "tool_call", { label: "vrcforge_bind_execution_target", tool: "vrcforge_bind_execution_target", status: "started", actionId: "action-fixture-3", summary: "Inspect the current scene state." }),
  event("timeline-fixture-74", 74, "tool_result", { label: "vrcforge_bind_execution_target", tool: "vrcforge_bind_execution_target", status: "ok", actionId: "action-fixture-3", summary: "When to use: Bind the current Avatar directly with {projectPath, scope: avatar}." }),
  event("timeline-fixture-75", 75, "tool_call", { label: "vrcforge_list_execution_targets", tool: "vrcforge_list_execution_targets", status: "started", actionId: "action-fixture-4", summary: "Inspect the current scene state." }),
  event("timeline-fixture-76", 76, "tool_result", { label: "vrcforge_list_execution_targets", tool: "vrcforge_list_execution_targets", status: "failed", actionId: "action-fixture-4", summary: "An exact component GlobalObjectId on the object is required." }),
  event("timeline-fixture-77", 77, "tool_call", { label: "vrcforge_get_asset_info", tool: "vrcforge_get_asset_info", status: "started", actionId: "action-fixture-5", summary: "Inspect the current scene state." }),
  event("timeline-fixture-78", 78, "tool_result", { label: "vrcforge_get_asset_info", tool: "vrcforge_get_asset_info", status: "ok", actionId: "action-fixture-5", summary: "Describe a project asset: path, GUID, type, importer, and prefab details." }),
  event("timeline-fixture-79", 79, "tool_call", { label: "vrcforge_get_gameobject", tool: "vrcforge_get_gameobject", status: "started", actionId: "action-fixture-6", summary: "Inspect the current scene state." }),
  event("timeline-fixture-80", 80, "tool_result", { label: "vrcforge_get_gameobject", tool: "vrcforge_get_gameobject", status: "ok", actionId: "action-fixture-6", summary: "Describe a scene GameObject: path, active state, tag/layer, parent, children, and components." }),
];
const streamingItem = {
  id: "raw-failure-continues",
  type: "streaming",
  clientTurnId: "fixture-running-turn",
  createdAt: new Date(Date.now() - 488_000).toISOString(),
  phase: "waiting_for_model",
  text: "still processing after recoverable tool errors",
  timeline,
};
const terminalFailureItem = {
  id: "terminal-failure-stays-visible",
  type: "agent",
  response: {
    clientTurnId: "terminal-failure-turn",
    plan: { reply: "The final operation failed.", summary: "The final operation failed.", nextStep: "done", planner: "fixture", shellNeeded: false, plannerFailure: { code: "provider_timeout" } },
    timeline: [event("terminal-final", 0, "assistant", { summary: "The final operation failed.", status: "done", phase: "final_answer", messageId: "terminal-final-message" })],
    steps: [],
  },
};
const root = createRoot(document.getElementById("root"));
void i18n.use(initReactI18next).init({
  lng: "zh-CN",
  fallbackLng: "zh-CN",
  interpolation: { escapeValue: false },
  resources: { "zh-CN": { translation: zhCN } },
}).then(() => root.render(
  <>
    <section id="running"><ConversationCard item={streamingItem} /></section>
    <section id="terminal-failure"><ConversationCard item={terminalFailureItem} /></section>
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
  res.end(`<div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);
});

let browser;
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const running = page.locator("#running");
  await running.locator("[data-agent-turn-process-group]").waitFor();

  const processGroup = running.locator("[data-agent-turn-process-group]");
  assert.equal(await running.locator("[data-conversation-streaming-turn]").count(), 1, "raw turn remains streaming after later successful tools");
  assert.equal(await running.locator('[data-vrcforge-streaming-phase="waiting_for_model"]').count(), 1);
  assert.equal(await processGroup.locator("button").first().getAttribute("aria-expanded"), "true", "live process fold is open");
  const collapsedSummary = await processGroup.locator("button").first().innerText();
  assert.doesNotMatch(collapsedSummary, /失败/, "running process summary does not inherit intermediate tool failures");
  const toolGroups = running.locator("[data-agent-tool-call-group]");
  assert.equal(await toolGroups.count(), 1, "raw adjacent tool operations stay in one group despite intermediate errors");
  for (const group of await toolGroups.all()) {
    assert.doesNotMatch(await group.locator("button").first().innerText(), /失败/, "collapsed tool-group summary hides aggregate failure status");
  }

  await toolGroups.nth(0).locator("button").first().click();
  const firstFailure = toolGroups.nth(0).locator("button.group\\/run").first();
  const firstFailureText = await firstFailure.innerText();
  assert.match(firstFailureText, /unity_get_gameobject/, "expanded failure row keeps its tool identity");
  assert.match(firstFailureText, /失败$/, "expanded failure row uses the short localized status");
  assert.doesNotMatch(firstFailureText, /执行失败/, "expanded failure row avoids a redundant long label");
  await firstFailure.click();
  await running.getByText("Unity MCP identity lock rejected this reference: ExecutionTarget scene revision changed.", { exact: true }).waitFor();

  const toolRows = toolGroups.nth(0).locator("button.group\\/run");
  const listFailure = toolRows.filter({ hasText: "unity_list_execution_targets" });
  assert.equal(await listFailure.count(), 1);
  assert.match(await listFailure.innerText(), /失败$/, "second failure is also visible as a short row status");
  assert.doesNotMatch(await listFailure.innerText(), /执行失败/);
  const getObjectRows = toolRows.filter({ hasText: "unity_get_gameobject" });
  assert.equal(await getObjectRows.count(), 2, "the same real trace contains both failed and successful reads");
  assert.match(await getObjectRows.nth(0).innerText(), /失败$/, "the first read remains failed");
  const continuedRead = getObjectRows.nth(1);
  assert.doesNotMatch(await continuedRead.innerText(), /失败/, "later success is not tainted by earlier errors");
  await continuedRead.click();
  await running.getByText("Describe a scene GameObject: path, active state, tag/layer, parent, children, and components.", { exact: true }).waitFor();

  const terminalStatus = page.locator("#terminal-failure [data-vrcforge-terminal-status]");
  assert.equal(await terminalStatus.count(), 1, "actual terminal failure remains visible");
  assert.ok((await terminalStatus.innerText()).trim(), "the terminal failure reply keeps its user-facing explanation");
  assert.deepEqual(errors, []);
  console.log("live failed-tool continuation UI: passed");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
