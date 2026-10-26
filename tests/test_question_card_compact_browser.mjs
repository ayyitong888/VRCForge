import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const root = process.cwd();
const temp = await fs.mkdtemp(path.join(root, "tests", ".question-card-compact-"));
const bundle = path.join(temp, "bundle.mjs");
const entry = `
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { AgentQuestionCard } from ${JSON.stringify(path.join(root, "src/components/chat/agent-question-card.tsx"))};

const initialQuestions = [
  { questionId: "q-one", header: "First question", status: "pending", question: "Choose or write the first answer", options: [
    { id: "yes", label: "Yes", value: "yes" }, { id: "no", label: "No", value: "no" },
  ] },
  { questionId: "q-two", header: "Second question", status: "pending", question: "Choose or write the second answer", options: [
    { id: "later", label: "Later", value: "later" }, { id: "skip-choice", label: "Skip choice", value: "skip-choice" },
  ] },
];

function Fixture() {
  const [questions, setQuestions] = useState(initialQuestions);
  const [lastAction, setLastAction] = useState(null);
  const [replyQuestionId, setReplyQuestionId] = useState("");
  const [failedOnce, setFailedOnce] = useState(false);

  async function onAnswerQuestion(questionId, optionId, value) {
    setLastAction({ questionId, optionId, value });
    if (questionId === "q-one" && optionId === "custom" && !failedOnce) {
      setFailedOnce(true);
      throw new Error("simulated send failure");
    }
    setQuestions((current) => current.map((question) => question.questionId === questionId ? { ...question, status: "answered" } : question));
  }

  return React.createElement("main", null,
    React.createElement("output", { "data-action": true }, lastAction ? JSON.stringify(lastAction) : ""),
    React.createElement("output", { "data-reply-question": true }, replyQuestionId),
    React.createElement(AgentQuestionCard, {
      questions,
      onAnswerQuestion,
      onReplyInChat: setReplyQuestionId,
    }),
  );
}

createRoot(document.getElementById("root")).render(React.createElement(Fixture));
`;

await build({
  stdin: { contents: entry, resolveDir: root, sourcefile: "question-card-compact-entry.mjs" },
  outfile: bundle, bundle: true, format: "iife", platform: "browser",
  plugins: [{ name: "question-card-mocks", setup(plugin) {
    plugin.onResolve({ filter: /^react-i18next$/ }, () => ({ path: "translation", namespace: "question-card" }));
    plugin.onResolve({ filter: /^lucide-react$/ }, () => ({ path: "icons", namespace: "question-card" }));
    plugin.onLoad({ filter: /.*/, namespace: "question-card" }, (args) => ({
      contents: args.path === "translation"
        ? 'export const useTranslation=()=>({t:(key,fallback)=>({"questionCard.label":"Agent question","questionCard.title":"Question","questionCard.previous":"Previous","questionCard.next":"Next","questionCard.position":"{{current}} of {{total}}","questionCard.customPlaceholder":"Type another answer...","chat.send":"Send","questionCard.skip":"Skip","questionCard.skipAnswer":"Skipped","questionCard.retry":"Retry","questionCard.replyInChat":"Reply in chat"}[key]||fallback||key)});'
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
  await page.clock.install();
  page.setDefaultTimeout(5000);
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.clock.runFor(30_000);
  assert.equal(await page.getByPlaceholder("Type another answer...").count(), 0, "idle question prompt collapses after 30 seconds");
  await page.locator("[data-question-reopen='q-one']").click();
  const custom = page.getByPlaceholder("Type another answer...");
  const answerButton = page.getByRole("button", { name: "Send", exact: true });

  assert.equal(await page.getByText("Recommended", { exact: true }).count(), 0, "ordinary options do not imply a recommendation");
  assert.equal(await page.locator('input[type="file"]').count(), 0, "ask questions stay text-only");
  assert.equal(await page.getByRole("button", { name: "Reply in chat", exact: true }).count(), 1, "shared composer handoff remains available");

  await custom.fill("draft one");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  assert.equal(await page.getByPlaceholder("Type another answer...").inputValue(), "", "the second question starts with its own draft");
  await page.getByPlaceholder("Type another answer...").fill("draft two");
  await page.getByRole("button", { name: "Previous", exact: true }).click();
  assert.equal(await page.getByPlaceholder("Type another answer...").inputValue(), "draft one", "first draft survives pagination");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  assert.equal(await page.getByPlaceholder("Type another answer...").inputValue(), "draft two", "second draft survives pagination");

  await page.getByRole("button", { name: "Previous", exact: true }).click();
  await answerButton.click();
  await page.getByRole("alert").waitFor();
  assert.equal(await custom.inputValue(), "draft one", "failed submission keeps the draft for retry");
  assert.match(await page.getByRole("alert").textContent(), /simulated send failure/);
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  const retried = JSON.parse(await page.locator("[data-action]").textContent());
  assert.deepEqual(retried, { questionId: "q-one", optionId: "custom", value: "draft one" }, "retry resends the preserved draft");

  await page.getByRole("button", { name: "Skip", exact: true }).click();
  const skipped = JSON.parse(await page.locator("[data-action]").textContent());
  assert.deepEqual(skipped, { questionId: "q-two", optionId: "skip", value: "Skipped" }, "Skip answers the remaining question");
  assert.equal(await page.getByRole("button", { name: "Skip", exact: true }).count(), 0, "answered questions leave the pending card");
  console.log("compact question card browser regression: text draft isolation, failed retry, skip, neutral options, and text-only input passed");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
  const cleanupTarget = await fs.realpath(temp);
  assert.ok(cleanupTarget.startsWith(path.join(await fs.realpath(path.join(root, "tests")), ".question-card-compact-")));
  await fs.rm(cleanupTarget, { recursive: true, force: true });
}
