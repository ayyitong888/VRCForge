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
  async function onAnswerQuestion(questionId, optionId, value) {
    if (window.failNextAnswer) {
      window.failNextAnswer = false;
      throw new Error("Network error");
    }
    setAnswer({ questionId, optionId, value });
    setQuestion((current) => ({ ...current, status: "answered" }));
  }
  return React.createElement("main", null,
    React.createElement("output", { "data-answer": true }, answer ? JSON.stringify(answer) : ""),
    React.createElement("output", { "data-reply-question": true }, replyQuestionId),
    React.createElement(AgentQuestionCard, { questions: [question], onAnswerQuestion, onReplyInChat: setReplyQuestionId,
      capturePreviewsForQuestion: () => [{ resourceUri: "vrcforge://scene-capture-preview/8d886299-9be1-4baf-8e1f-e205f3a8e5fc?context=runtime%3Asession-fixture&revision=1&store=11111111111111111111111111111111", label: "front", sessionId: "session-fixture" }],
      resourceImageContextForQuestion: () => ({ endpoint: location.origin, chatId: "fixture", sessionId: "session-fixture", clientTurnId: "turn-fixture" }) }),
  );
}
createRoot(document.getElementById("root")).render(React.createElement(Fixture));
`;
await build({
  stdin: { contents: entry, resolveDir: root, sourcefile: "visual-checkpoint-entry.mjs" },
  outfile: bundle, bundle: true, format: "iife", platform: "browser",
  plugins: [{ name: "visual-checkpoint-mocks", setup(plugin) {
    plugin.onResolve({ filter: /^react-i18next$/ }, () => ({ path: "translation", namespace: "checkpoint" }));
    plugin.onLoad({ filter: /.*/, namespace: "checkpoint" }, () => ({
      contents: 'export const initReactI18next={type:"3rdParty",init(){}}; export const useTranslation=()=>({t:(key,fallback)=>({"questionCard.visualCheckpoint.options.pass":"Pass","questionCard.visualCheckpoint.options.fail":"Fail","questionCard.visualCheckpoint.options.uncertain":"Uncertain","questionCard.visualCheckpoint.options.later":"Later","questionCard.visualCheckpoint.notePlaceholder":"Optional visual-checkpoint note...","questionCard.customPlaceholder":"Type another answer...","chat.send":"Send","questionCard.skip":"Skip","questionCard.skipAnswer":"Skipped","questionCard.replyInChat":"Reply in chat"}[key]||(typeof fallback==="string"?fallback:key))});',
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
  await page.clock.install();
  await page.route("**/api/app/agent/resource-image", async (route) => {
    const { uri } = route.request().postDataJSON();
    await route.fulfill({ json: {
      uri, mimeType: "image/png", sha256: "a".repeat(64),
      pngBase64: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5foAAAAASUVORK5CYII=",
    } });
  });
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  assert.equal(await page.locator("[data-question-capture-previews]").count(), 1, "question card exposes the related capture preview");
  await page.locator("[data-question-capture-previews] button").click({ timeout: 2500 });
  const preview = page.getByRole("dialog");
  await preview.getByRole("img", { name: "front" }).waitFor();
  await page.clock.fastForward(31_000);
  assert.equal(await preview.count(), 1, "the question stays open while the user inspects an image");
  assert.equal(await page.locator("[data-answer]").textContent(), "", "previewing a capture never answers the question");
  await preview.getByRole("button", { name: "chat.closeImagePreview" }).click();
  await preview.waitFor({ state: "detached" });
  await page.getByRole("button", { name: "Reply in chat" }).click();
  assert.equal(await page.locator("[data-reply-question]").textContent(), "visual-1", "reply-in-chat keeps the question id");
  assert.equal(await page.getByText("Recommended", { exact: true }).count(), 0, "visual checkpoints do not recommend Pass");
  const input = page.getByPlaceholder("Type another answer...");
  assert.equal(await input.count(), 1, "visual checkpoints share the compact text reply row");
  assert.equal(await input.evaluate((element) => element.tagName), "INPUT");
  assert.equal(await input.getAttribute("type"), "text");
  assert.equal(await page.locator('input[type="file"]').count(), 0);
  assert.equal(await page.getByRole("button", { name: "Send", exact: true }).isDisabled(), true);
  await input.fill("Please inspect the shoe opening; I have not decided yet.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  assert.deepEqual(JSON.parse(await page.locator("[data-answer]").textContent()), {
    questionId: "visual-1", optionId: "custom", value: "Please inspect the shoe opening; I have not decided yet.",
  }, "free text must not fabricate a pass/fail decision");
  assert.equal(await page.getByRole("region", { name: "questionCard.label" }).count(), 0);
  for (const [label, optionId] of [["Fail", "fail"], ["Uncertain", "uncertain"], ["Later", "later"], ["Skip", "skip"], ["Pass", "pass"]]) {
    await page.reload();
    if (optionId === "fail") await input.fill("Shoulder is clear; feet still need review.");
    if (optionId === "later") await page.evaluate(() => { window.failNextAnswer = true; });
    await page.getByRole("button", { name: label, exact: false }).filter({ hasText: label }).first().click();
    if (optionId === "later") {
      await page.getByText("Network error", { exact: true }).waitFor();
      assert.equal(await page.locator("[data-answer]").textContent(), "");
      await page.getByRole("button", { name: "questionCard.retry" }).click();
    }
    const answer = JSON.parse(await page.locator("[data-answer]").textContent());
    assert.equal(answer.questionId, "visual-1");
    assert.equal(answer.optionId, optionId);
    if (optionId === "fail") assert.match(answer.value, /^Fail\n\nShoulder is clear/);
    assert.equal(await page.getByRole("region", { name: "questionCard.label" }).count(), 0, `${label} closes the original question`);
    assert.equal(await page.locator("[data-question-reopen='visual-1']").count(), 0);
  }
  console.log("visual checkpoint question UI: every choice closes the question, retry preserves answer, image preview passed");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
  await fs.rm(temp, { recursive: true, force: true });
}
