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
assert.match(appSource, /questionAnswerItem\(payload\.question/);
assert.match(appSource, /persistQuestionAnswer\(answerChatId, answerItem\)/);
const answerHelperSource = await fs.readFile(path.join(root, "src", "lib", "question-answer.ts"), "utf8");
assert.match(answerHelperSource, /question\.header\?\.trim\(\)/);
assert.doesNotMatch(answerHelperSource, /question\.question \|\| question\.questionId/, "the visible answer label never duplicates the full question");
assert.match(appSource, /questionAnswerItem\(payload\.question, payload\.question\.answer \|\| composerMessage, attachments\)/);
const answerHandler = appSource.slice(appSource.indexOf("const answerRuntimeQuestion"), appSource.indexOf("const replyToAgentQuestionInComposer"));
assert.doesNotMatch(answerHandler, /type: "user"/, "question answers are not persisted as ordinary user turns");
assert.ok(!workspaceSource.includes("data-question-continuation-status"), "successful answers must not leave a question-specific continuation dock");
const temp = await fs.mkdtemp(path.join(root, "tests", ".question-answer-browser-"));
const helperOutput = path.join(temp, "question-answer.mjs");
await build({ entryPoints: [path.join(root, "src/lib/question-answer.ts")], outfile: helperOutput, bundle: true, format: "esm", platform: "node" });
const { questionAnswerItem } = await import(pathToFileURL(helperOutput).href);
const answer = questionAnswerItem({ questionId: "q-browser", header: "Repair avatar", question: "Choose a repair" }, "Safe repair", [
  { id: "attachment-1", name: "review.png", size: 42, type: "image/png", dataUrl: "data:image/png;base64,AA==" },
]);
assert.deepEqual(answer, {
  id: "question-answer-q-browser-0", type: "question_answer", questionId: "q-browser", header: "Repair avatar", question: "Choose a repair", text: "Safe repair", revision: 0,
  attachments: [{ id: "attachment-1", name: "review.png", size: 42, type: "image/png", dataUrl: "data:image/png;base64,AA==" }],
});
const bundle = path.join(temp, "bundle.mjs");
const entry = `
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { AgentQuestionCard } from ${JSON.stringify(path.join(root, "src/components/chat/agent-question-card.tsx"))};
function Fixture() {
  const [question, setQuestion] = useState({ questionId: "q-browser", status: "pending", header: "Repair avatar", question: "Choose a repair", options: [{ id: "safe", label: "Safe repair", value: "Safe repair" }] });
  const [answer, setAnswer] = useState("");
  const [calls, setCalls] = useState(0);
  const [fail, setFail] = useState(true);
  async function answerQuestion(_id, _optionId, value) {
    setCalls((count) => count + 1);
    await new Promise((resolve) => setTimeout(resolve, 40));
    if (fail) { setFail(false); throw new Error("temporary answer failure"); }
    setAnswer(value); setQuestion((current) => ({ ...current, status: "answered", runtimeContinuation: { status: "running", turnId: "turn-browser" } }));
  }
  return React.createElement("main", null,
    React.createElement("output", { "data-answer-calls": calls }, String(calls)),
    React.createElement("div", { "data-answer-item": true }, answer ? "Repair avatar\\n\\n" + answer : ""),
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
        : 'export const ChevronLeft=()=>null; export const ChevronRight=()=>null; export const HelpCircle=()=>null; export const Pencil=()=>null; export const X=()=>null;',
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
  assert.equal(await page.locator("[data-question-continuation-status]").count(), 0, "successful answer must not leave a question-specific running frame");
  assert.equal(await page.locator("[data-answer-calls]").textContent(), "2", "retry must be the only second answer call");
  console.log("question answer browser: failure retains Retry; success stores answer and removes card without a continuation frame");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
  const cleanupTarget = await fs.realpath(temp);
  const testRoot = await fs.realpath(path.join(root, "tests"));
  assert.ok(cleanupTarget.startsWith(testRoot + path.sep), "cleanup must stay in the test workspace");
  await fs.rm(cleanupTarget, { recursive: true, force: true });
}
