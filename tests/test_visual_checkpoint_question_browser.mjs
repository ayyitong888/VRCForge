import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const root = process.cwd();
const temp = await fs.mkdtemp(path.join(root, "tests", ".visual-checkpoint-question-"));
const bundle = path.join(temp, "bundle.mjs");
const entry = `
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { AgentQuestionCard } from ${JSON.stringify(path.join(root, "src/components/chat/agent-question-card.tsx"))};
function Fixture() {
  const [question, setQuestion] = useState({ questionId: "visual-1", kind: "visual_checkpoint", status: "pending", question: "Inspect the preview", options: [
    { id: "pass", label: "Pass" }, { id: "fail", label: "Fail" }, { id: "uncertain", label: "Uncertain" }, { id: "later", label: "Later" },
  ] });
  const [answer, setAnswer] = useState(null);
  const [replyQuestionId, setReplyQuestionId] = useState("");
  async function onAnswerQuestion(questionId, optionId, value) { setAnswer({ questionId, optionId, value }); setQuestion((current) => ({ ...current, status: answer ? "answered" : "pending" })); }
  return React.createElement("main", null,
    React.createElement("output", { "data-answer": true }, answer ? JSON.stringify(answer) : ""),
    React.createElement("output", { "data-reply-question": true }, replyQuestionId),
    React.createElement(AgentQuestionCard, { questions: [question], onAnswerQuestion, onReplyInChat: setReplyQuestionId }),
  );
}
createRoot(document.getElementById("root")).render(React.createElement(Fixture));
`;
await build({
  stdin: { contents: entry, resolveDir: root, sourcefile: "visual-checkpoint-entry.mjs" },
  outfile: bundle, bundle: true, format: "iife", platform: "browser",
  plugins: [{ name: "visual-checkpoint-mocks", setup(plugin) {
    plugin.onResolve({ filter: /^react-i18next$/ }, () => ({ path: "translation", namespace: "checkpoint" }));
    plugin.onResolve({ filter: /^lucide-react$/ }, () => ({ path: "icons", namespace: "checkpoint" }));
    plugin.onLoad({ filter: /.*/, namespace: "checkpoint" }, (args) => ({
      contents: args.path === "translation"
        ? 'export const useTranslation=()=>({t:(key,fallback)=>({"questionCard.visualCheckpoint.options.pass":"Pass","questionCard.visualCheckpoint.options.fail":"Fail","questionCard.visualCheckpoint.options.uncertain":"Uncertain","questionCard.visualCheckpoint.options.later":"Later","questionCard.visualCheckpoint.notePlaceholder":"Optional visual-checkpoint note...","questionCard.replyInChat":"Reply in chat"}[key]||fallback||key)});'
        : 'export const ChevronLeft=()=>null; export const ChevronRight=()=>null; export const HelpCircle=()=>null; export const Pencil=()=>null; export const X=()=>null;',
      loader: "js",
    }));
  } }],
});
const script = await fs.readFile(bundle, "utf8");
const server = createServer((_request, response) => { response.writeHead(200, { "content-type": "text/html" }); response.end(`<div id="root"></div><script>${script}</script>`); });
server.listen(0, "127.0.0.1");
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  assert.equal(await page.getByText("Recommended", { exact: true }).count(), 0, "visual checkpoints do not recommend Pass");
  assert.equal(await page.getByPlaceholder("Type another answer...").count(), 0, "visual checkpoints use one note field instead of a duplicate custom field");
  await page.getByRole("button", { name: "Reply in chat" }).click();
  assert.equal(await page.locator("[data-reply-question]").textContent(), "visual-1", "reply-in-chat keeps the question id");
  await page.getByPlaceholder("Optional visual-checkpoint note...").fill("Shoulder is clear; feet still need review.");
  await page.getByRole("button", { name: "Fail" }).click();
  const answer = JSON.parse(await page.locator("[data-answer]").textContent());
  assert.equal(answer.questionId, "visual-1");
  assert.equal(answer.optionId, "fail");
  assert.match(answer.value, /^Fail\n\nShoulder is clear/);
  await page.getByRole("button", { name: "Uncertain" }).click();
  const deferredAnswer = JSON.parse(await page.locator("[data-answer]").textContent());
  assert.equal(deferredAnswer.optionId, "uncertain", "a deferred pending checkpoint can be answered again");
  console.log("visual checkpoint question UI: translated fixed choices, no default recommendation, question-id reply handoff, explicit decision/note, deferred retry passed");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
  await fs.rm(temp, { recursive: true, force: true });
}
