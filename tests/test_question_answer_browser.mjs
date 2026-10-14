// Real browser regression for question-card settlement, retry, and duplicate-answer protection.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";

const root = process.cwd();
const appSource = await fs.readFile(path.join(root, "src", "App.tsx"), "utf8");
const workspaceSource = await fs.readFile(path.join(root, "src", "components", "chat", "chat-workspace.tsx"), "utf8");
assert.match(appSource, /const answerItemId = `question-answer-\$\{questionId\}`/);
assert.match(appSource, /appendToChat\(answerChatId,\s*\{/);
assert.match(appSource, /answeredQuestion\?\.question \|\| questionId/);
assert.match(workspaceSource, /data-question-continuation-status/);
assert.match(workspaceSource, /data-question-continuation-stop/);
const temp = await fs.mkdtemp(path.join(root, "tests", ".question-answer-browser-"));
const bundle = path.join(temp, "bundle.mjs");
const entry = `
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { AgentQuestionCard } from ${JSON.stringify(path.join(root, "src/components/chat/agent-question-card.tsx"))};
function Fixture() {
  const [question, setQuestion] = useState({ questionId: "q-browser", status: "pending", header: "Repair avatar", question: "Choose a repair", options: [{ id: "safe", label: "Safe repair", value: "Safe repair" }] });
  const [answer, setAnswer] = useState("");
  const [continuing, setContinuing] = useState(false);
  const [calls, setCalls] = useState(0);
  const [fail, setFail] = useState(true);
  async function answerQuestion(_id, _optionId, value) {
    setCalls((count) => count + 1);
    await new Promise((resolve) => setTimeout(resolve, 40));
    if (fail) { setFail(false); throw new Error("temporary answer failure"); }
    setAnswer(value); setQuestion((current) => ({ ...current, status: "answered", runtimeContinuation: { status: "running", turnId: "turn-browser" } })); setContinuing(true);
  }
  return React.createElement("main", null,
    React.createElement("output", { "data-answer-calls": calls }, String(calls)),
    React.createElement("div", { "data-answer-item": true }, answer ? "Repair avatar\\n\\n" + answer : ""),
    continuing ? React.createElement("div", { role: "status", "data-question-continuation-status": true }, "Continuing this task…", React.createElement("button", { "data-question-continuation-stop": true, onClick: () => setContinuing(false) }, "Stop")) : null,
    React.createElement(AgentQuestionCard, { questions: [question], onAnswerQuestion: answerQuestion })
  );
}
createRoot(document.getElementById("root")).render(React.createElement(Fixture));
`;
await build({
  stdin: { contents: entry, resolveDir: root, sourcefile: "question-answer-browser-entry.mjs" },
  outfile: bundle, bundle: true, format: "iife", platform: "browser",
  plugins: [{ name: "question-answer-mocks", setup(plugin) {
    plugin.onResolve({ filter: /^react-i18next$/ }, () => ({ path: "translation", namespace: "qa" }));
    plugin.onResolve({ filter: /^lucide-react$/ }, () => ({ path: "icons", namespace: "qa" }));
    plugin.onLoad({ filter: /.*/, namespace: "qa" }, (args) => ({
      contents: args.path === "translation"
        ? 'export const useTranslation=()=>({t:(_key,fallback)=>fallback||_key});'
        : 'export const ChevronLeft=()=>null; export const ChevronRight=()=>null;',
      loader: "js",
    }));
  } }],
});
const script = await fs.readFile(bundle, "utf8");
const server = createServer((_request, response) => {
  response.writeHead(200, { "content-type": "text/html" });
  response.end(`<div id="root"></div><script>${script}</script>`);
});
server.listen(0, "127.0.0.1");
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const answer = page.getByRole("button", { name: "Safe repair" });
  await answer.click();
  await page.getByRole("alert").waitFor();
  assert.equal(await page.getByRole("region", { name: "questionCard.label" }).count(), 1, "failed answer must retain retryable card");
  await page.getByRole("button", { name: "Retry" }).click();
  await page.locator("[data-answer-item]").filter({ hasText: "Repair avatar" }).waitFor();
  assert.equal(await page.getByRole("region", { name: "questionCard.label" }).count(), 0, "successful answer must remove card");
  assert.equal(await page.locator("[data-question-continuation-status]").count(), 1, "running continuation must remain visible outside question card");
  await page.locator("[data-question-continuation-stop]").click();
  assert.equal(await page.locator("[data-question-continuation-status]").count(), 0, "Stop must remain available on unified continuation status");
  assert.equal(await page.locator("[data-answer-calls]").textContent(), "2", "retry must be the only second answer call");
  console.log("question answer browser: failure retains Retry; success stores answer and removes card");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
  await fs.rm(temp, { recursive: true, force: true });
}
