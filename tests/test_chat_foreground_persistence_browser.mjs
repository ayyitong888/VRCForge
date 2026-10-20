import assert from "node:assert/strict";
import { createServer } from "node:http";
import { build } from "esbuild";
import { createRequire } from "node:module";

const { chromium } = createRequire(import.meta.url)(
  process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright",
);

const apiStub = `
export const cancelAgentRunFollowup = async () => ({ ok: true });
export const listAgentRunFollowups = async () => ({ ok: true, items: [] });
export const fetchAgentQuestions = async () => ({ ok: true, questions: [] });
export const issueComputerUseTurnGrant = async () => null;
export const requestAgentRunCancel = async () => ({ ok: true });
export const requestChatTitle = async () => ({ title: "Fixture" });
export const sendAgentMessage = (_endpoint, _message, _session, _history, _agent, options) => {
  window.__providerRequests.push({ clientTurnId: options?.clientTurnId });
  return Promise.resolve({
    ok: true, status: "completed", sessionId: "session-fixture", turnId: options?.clientTurnId,
    plan: { reply: "fixture reply", summary: "fixture", nextStep: "done", planner: "fixture", shellNeeded: false },
    timeline: [],
  });
};
export const recordAgentRunQueued = async () => ({ ok: true, accepted: true, mode: "followup", queueId: "queue-fixture", sequence: 1 });
`;

const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
import React from "react";
import { createRoot } from "react-dom/client";
import { useState } from "react";
import { useChatRunController } from "./src/hooks/use-chat-run-controller";

const chat = { id: "chat-fixture", sessionId: "session-fixture", items: [], title: "Existing chat" };
let persistCalls = 0;
let releasePersist;
let failNextPersist = false;
const persist = () => {
  persistCalls += 1;
  window.__events.push("persist_started");
  if (failNextPersist) {
    failNextPersist = false;
    return Promise.reject(new Error("fixture chat snapshot save failed"));
  }
  if (persistCalls === 1) return new Promise((resolve) => { releasePersist = resolve; });
  return Promise.resolve();
};
function Harness() {
  const [, setChatState] = useState([chat]);
  const controller = useChatRunController({
    endpoint: "http://127.0.0.1:8757", runtimeConnected: true, sessionId: "session-fixture",
    activeRuntimeProjectPath: "", getChatById: (id) => id === chat.id ? chat : undefined,
    ensureActiveChat: () => chat.id,
    updateChat: (id, update) => { if (id === chat.id) { Object.assign(chat, update(chat)); setChatState([{ ...chat }]); } },
    appendToChat: (id, item) => { if (id === chat.id) chat.items = [...chat.items, item]; },
    touchChat: (value) => value,
    startRuntime: async () => "http://127.0.0.1:8757", refresh: async () => {},
    refreshRuntimeRuns: async () => {}, refreshBackgroundGoals: () => {},
    handleRuntimeSessionFailure: () => {}, setError: (message) => window.__errors.push(message),
    persistChatsNow: persist, chats: [chat],
  });
  window.__controller = controller;
  return <div id="hook-mounted" />;
}
window.__events = [];
window.__errors = [];
window.__providerRequests = [];
window.__releasePersist = () => releasePersist?.();
window.__failNextPersist = () => { failNextPersist = true; };
window.__submit = (id, text) => window.__controller.submitTurn({
  id, text, attachments: [], provider: "fixture", providerLabel: "Fixture", model: "fixture-model",
  sessionId: "session-fixture", planMode: false,
});
createRoot(document.getElementById("root")).render(<Harness />);
`,
  },
  bundle: true,
  write: false,
  format: "iife",
  jsx: "automatic",
  platform: "browser",
  plugins: [{
    name: "foreground-persistence-test-stubs",
    setup(builder) {
      builder.onResolve({ filter: /^react-i18next$/ }, () => ({ path: "react-i18next", namespace: "foreground-stub" }));
      builder.onResolve({ filter: /^\.\.\/lib\/api$/ }, (args) => args.importer.endsWith("use-chat-run-controller.ts")
        ? ({ path: "app-api", namespace: "foreground-stub" }) : undefined);
      builder.onResolve({ filter: /^\.\.\/lib\/api\/agent-runtime$/ }, (args) => args.importer.endsWith("use-chat-run-controller.ts")
        ? ({ path: "agent-runtime-api", namespace: "foreground-stub" }) : undefined);
      builder.onLoad({ filter: /.*/, namespace: "foreground-stub" }, (args) => ({
        contents: args.path === "react-i18next"
          ? "export const useTranslation = () => ({ t: (key) => key });"
          : args.path === "agent-runtime-api"
            ? "export const DEFAULT_BACKGROUND_MAX_AGENTIC_TURNS = 12;"
            : apiStub,
        loader: "js",
      }));
    },
  }],
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
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.locator("#hook-mounted").waitFor({ state: "attached" });

  const firstTurn = page.evaluate(() => window.__submit("foreground-first", "first foreground message"));
  await page.waitForFunction(() => window.__events.includes("persist_started"));
  await page.waitForTimeout(40);
  assert.deepEqual(await page.evaluate(() => window.__providerRequests), [], "foreground runtime admission waits for the chat snapshot");
  await page.evaluate(() => window.__releasePersist());
  await page.waitForFunction(() => window.__providerRequests.length === 1);
  await firstTurn;
  assert.deepEqual(await page.evaluate(() => window.__providerRequests[0]), { clientTurnId: "foreground-first" });

  await page.evaluate(() => window.__failNextPersist());
  await page.evaluate(() => window.__submit("foreground-failed", "must remain local"));
  await page.waitForFunction(() => window.__errors.length > 0);
  assert.equal(await page.evaluate(() => window.__providerRequests.length), 1, "a failed foreground save prevents runtime admission");
  assert.equal(await page.evaluate(() => window.__events.filter((event) => event === "persist_started").length), 2);
  assert.equal(pageErrors.length, 0, pageErrors.join("\n"));
  console.log("foreground chat persistence before runtime admission: ok");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
