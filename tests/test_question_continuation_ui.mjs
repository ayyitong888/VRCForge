import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { projectAgentQuestionContinuation, questionContinuationCancelTarget, questionContinuationDisplay, questionContinuationStatus } from "../src/hooks/use-runtime-turn-continuation.ts";

assert.equal(questionContinuationDisplay({ status: "answered", runtimeContinuation: { status: "queued" } }), "active");
assert.deepEqual(
  questionContinuationCancelTarget({ sessionId: "session", runtimeContinuation: { turnId: "turn", clientTurnId: "client" } }),
  { sessionId: "session", turnId: "turn", clientTurnId: "client" },
);
assert.equal(questionContinuationCancelTarget({ sessionId: "session", runtimeContinuation: { status: "queued" } }), null);
assert.equal(questionContinuationStatus("delivered", "needs_user_action"), "blocked");
assert.equal(questionContinuationStatus("delivered", "cancel_requested"), "cancelling");
assert.equal(questionContinuationStatus("delivered"), "delivered");
const projected = projectAgentQuestionContinuation(
  { questionId: "q-run", sessionId: "s", status: "answered", runtimeContinuationStatus: "claimed" },
  [{ sessionId: "s", turnId: "t", clientTurnId: "c:question:q-run", status: "running" }],
);
assert.equal(projected.runtimeContinuation?.status, "running");
assert.equal(projected.runtimeContinuation?.clientTurnId, "c:question:q-run");
for (const [runStatus, nextStep, expected] of [
  ["cancel_requested", "", "cancelling"],
  ["blocked", "pending_approval", "blocked"],
  ["completed", "done", "completed"],
  ["cancelled", "cancelled", "cancelled"],
]) {
  const actual = projectAgentQuestionContinuation(
    { questionId: "q-run", sessionId: "s", status: "answered", runtimeContinuationStatus: "delivered" },
    [{ sessionId: "s", clientTurnId: "c:question:q-run", status: runStatus, nextStep }],
  );
  assert.equal(actual.runtimeContinuation?.status, expected, `wrong projection for ${runStatus}`);
}

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const temp = path.join(root, "tests/.tmp-question-ui");
await fs.rm(temp, { recursive: true, force: true });
await fs.mkdir(temp, { recursive: true });
const mockTranslation = path.join(temp, "react-i18next.mjs");
const mockIcons = path.join(temp, "lucide-react.mjs");
const entry = path.join(temp, "entry.mjs");
const output = path.join(temp, "rendered.mjs");
await fs.writeFile(mockTranslation, 'export function useTranslation(){return {t:(_key,fallback)=>fallback||_key};}\n');
await fs.writeFile(mockIcons, 'export const ChevronLeft=()=>null; export const ChevronRight=()=>null;\n');
await fs.writeFile(entry, `
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AgentQuestionCard } from ${JSON.stringify(path.join(root, "src/components/chat/agent-question-card.tsx"))};
import { projectAgentQuestionContinuation } from ${JSON.stringify(path.join(root, "src/hooks/use-runtime-turn-continuation.ts"))};
const base = { questionId: "q-1", status: "answered", question: "Approve repair?", options: [] };
const active = renderToStaticMarkup(React.createElement(AgentQuestionCard, {
  questions: [{ ...base, runtimeContinuation: { questionId: "q-1", status: "queued", sessionId: "s", clientTurnId: "c" } }],
  onAnswerQuestion: async () => {},
  onStopContinuation: () => {},
}));
if (!active.includes("Continuing this task") || !active.includes(">Stop<")) throw new Error("running continuation was not rendered with Stop");
const terminal = renderToStaticMarkup(React.createElement(AgentQuestionCard, {
  questions: [{ ...base, runtimeContinuation: { questionId: "q-1", status: "failed", error: "planner failed" } }],
  onAnswerQuestion: async () => {},
}));
if (!terminal.includes("planner failed")) throw new Error("terminal continuation failure was not rendered");
console.log(JSON.stringify({ activeVisible: true, stopVisible: true, terminalFailureVisible: true }));
for (const [runStatus, nextStep, expected] of [["cancel_requested", "", "Stopping…"], ["blocked", "pending_approval", "Waiting for the next action"]]) {
  const question = projectAgentQuestionContinuation({ ...base, sessionId: "s", runtimeContinuationStatus: "delivered" },
    [{ sessionId: "s", turnId: "t", clientTurnId: "c:question:q-1", status: runStatus, nextStep }]);
  const html = renderToStaticMarkup(React.createElement(AgentQuestionCard, { questions: [question], onAnswerQuestion: () => {}, onStopContinuation: () => {} }));
  if (!html.includes(expected) || html.includes("Continuation stopped") || html.includes("Continuation completed")) throw new Error("Raw snapshot state was projected incorrectly: " + runStatus);
}
`);
try {
  await build({
    entryPoints: [entry], outfile: output, bundle: true, format: "esm", platform: "node",
    external: ["react", "react-dom/server"],
    plugins: [{ name: "question-ui-mocks", setup(plugin) {
      plugin.onResolve({ filter: /^react-i18next$/ }, () => ({ path: mockTranslation }));
      plugin.onResolve({ filter: /^lucide-react$/ }, () => ({ path: mockIcons }));
    } }],
  });
  await import(`${pathToFileURL(output).href}?case=question-continuation`);
} finally {
  await fs.rm(temp, { recursive: true, force: true });
}
