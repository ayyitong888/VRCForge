// Real UsageSummary component in a browser; no provider or app fixture is used.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const mockPlugin = {
  name: "usage-summary-browser-mocks",
  setup(api) {
    api.onResolve({ filter: /(?:^|[\\/])(?:react-i18next|utils)$/ }, (args) => ({ path: args.path.split(/[\\/]/).pop(), namespace: "usage-mock" }));
    api.onLoad({ filter: /.*/, namespace: "usage-mock" }, (args) => ({
      loader: "tsx",
      contents: args.path === "react-i18next"
        ? `export const useTranslation = () => ({ t: (key, values = {}) => ({\n          "chat.usageSummaryTitle":"Usage", "chat.usageSummaryRecorded":"recorded this turn", "chat.usageSummaryTask":"task total",\n          "chat.usageSummaryInput":"Input", "chat.usageSummaryOutput":"Output", "chat.usageSummaryCache":"Cache read",\n          "chat.usageSummaryLastRequest":"Last request input", "chat.usageSummaryIncluded":"included in input", "chat.usageSummaryCacheIncomplete":"incomplete", "chat.usageSummaryCost":"Cost",\n          "chat.usageSummaryPricingNotConfigured":"pricing not configured", "chat.usageSummaryUnknown":"unknown",
          "chat.usageSummaryCompaction":"Compaction usage total", "chat.usageSummaryTotal":"Total", "chat.usageSummaryCacheWrite":"Cache write", "chat.usageSummaryPartial":"Partial", "chat.usageSummaryRequests":"Recorded requests"\n        }[key] || key).replace(/\\{\\{(\\w+)\\}\\}/g, (_m, name) => String(values[name] ?? name)) });` 
        : `export const formatCount = (value) => String(value); export const cn = (...values) => values.filter(Boolean).join(" ");`,
      resolveDir: process.cwd(),
    }));
  },
};
const entry = `
import React from "react";
import { createRoot } from "react-dom/client";
import { UsageSummary } from "./src/components/chat/usage-summary";
const call = { inputTokens: 1200, outputTokens: 300, lastInputTokens: 700, cacheReadTokens: 500, cacheUsageComplete: true };
const compact = { exact: true, inputTokens: 100, outputTokens: 10, totalTokens: 110, cacheReadTokens: 40, cacheWriteTokens: 5, requestCount: 1, cacheUsageComplete: true };
const compactUnknown = { exact: false, unavailableReason: "provider_usage_missing" };
const task = { scope: "task_total_context_usage", taskTotalAvailable: true, inputTokens: 4200, cumulativeInputTokens: 4200, cumulativeOutputTokens: 900, cacheReadTokens: 1800, cacheUsageComplete: true };
const scopedFallback = { scope: "task_total_context_usage", taskTotalAvailable: true, inputTokens: 77, outputTokens: 11 };
const incomplete = { inputTokens: 1200, cacheReadTokens: 500, cacheUsageComplete: false };
function Fixture() { return <main><UsageSummary usage={call} compactionUsage={compact}/><UsageSummary usage={task}/><UsageSummary usage={scopedFallback}/><UsageSummary usage={incomplete}/><UsageSummary/><UsageSummary compactionUsage={compactUnknown}/></main>; }
createRoot(document.getElementById("root")).render(<Fixture/>);
`;
const conversationCardSource = readFileSync("src/components/chat/conversation-card.tsx", "utf8");
const chatWorkspaceSource = readFileSync("src/components/chat/chat-workspace.tsx", "utf8");
assert.doesNotMatch(conversationCardSource, /UsageSummary/, "per-reply cards no longer own the usage summary");
assert.doesNotMatch(chatWorkspaceSource, /UsageSummary|data-chat-usage-panel|latestAgentItem\.response\.contextUsage/, "chat workspace does not render a per-reply usage panel");
const bundle = await build({ stdin: { resolveDir: process.cwd(), loader: "tsx", contents: entry }, bundle: true, write: false, format: "iife", jsx: "automatic", plugins: [mockPlugin] });
const server = createServer((_request, response) => { response.writeHead(200, { "content-type": "text/html" }); response.end(`<div id="root"></div><script>${bundle.outputFiles[0].text}</script>`); });
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const summaries = page.locator("[data-usage-summary]");
  assert.equal(await summaries.count(), 6);
  for (let index = 0; index < 6; index += 1) await summaries.nth(index).locator("summary").click();
  assert.match(await summaries.nth(0).innerText(), /recorded this turn/);
  assert.match(await summaries.nth(0).innerText(), /Input\s*1200/);
  assert.match(await summaries.nth(0).innerText(), /Cache read\s*500/);
  assert.match(await summaries.nth(0).innerText(), /42%/);
  assert.match(await summaries.nth(0).innerText(), /Last request input\s*700/);
  assert.match(await summaries.nth(0).innerText(), /pricing not configured/);
  assert.match(await summaries.nth(0).innerText(), /Compaction usage total/);
  assert.match(await summaries.nth(0).innerText(), /Input\s*100/);
  assert.match(await summaries.nth(0).innerText(), /Output\s*10/);
  assert.match(await summaries.nth(0).innerText(), /Cache read\s*40/);
  assert.match(await summaries.nth(0).innerText(), /Cache write\s*5/);
  assert.match(await summaries.nth(1).innerText(), /task total/);
  assert.match(await summaries.nth(1).innerText(), /Input\s*4200/);
  assert.match(await summaries.nth(1).innerText(), /Cache read\s*1800/);
  assert.match(await summaries.nth(2).innerText(), /Input\s*77/);
  assert.match(await summaries.nth(2).innerText(), /Output\s*11/);
  assert.match(await summaries.nth(3).innerText(), /incomplete/);
  assert.match(await summaries.nth(4).innerText(), /unknown/);
  assert.match(await summaries.nth(5).innerText(), /unknown/);
  assert.match(await summaries.nth(5).locator("[data-compaction-usage-summary]").innerText(), /unknown/);
  assert.match(await summaries.nth(5).locator("[data-compaction-usage-summary]").innerText(), /Partial/);
  console.log("usage summary browser: real component call/task/unknown and pricing boundary passed");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
