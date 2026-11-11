import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const root = process.cwd();
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({
  stdin: { resolveDir: root, loader: "tsx", contents: `
    import React, { useEffect, useState } from "react";
    import { createRoot } from "react-dom/client";
    import i18n from "i18next";
    import { initReactI18next } from "react-i18next";
    import { useRuntimeWorkspace } from "./src/hooks/use-runtime-workspace";
    await i18n.use(initReactI18next).init({lng:"en",resources:{en:{translation:{questionCard:{label:"Question"}}}}});
    function Fixture() {
      const [runtimeConnected, setRuntimeConnected] = useState(false);
      const [sessionId, setSessionId] = useState("session-replay");
      const workspace = useRuntimeWorkspace({ endpoint: location.origin, runtimeConnected, sessionId, activeRuntimeProjectPath: "", activeProjectPath: "", sending: false, pendingApprovals: 0, setBootstrap: () => {}, setAgentApprovals: () => {}, setError: () => {}, chatIdForSessionId: () => "chat-replay" });
      useEffect(() => { window.testApi = workspace; }, [workspace]);
      window.connectRuntime = () => setRuntimeConnected(true);
      window.switchSession = setSessionId;
      return <output id="state" data-session={sessionId}>{JSON.stringify({ questions: workspace.agentQuestions.length })}</output>;
    }
    createRoot(document.getElementById("root")).render(<Fixture />);
  `}, bundle: true, write: false, format: "esm", jsx: "automatic", define: { "process.env.NODE_ENV": '"test"' },
  plugins: [{ name: "tauri-invoke-fixture", setup(plugin) {
    plugin.onResolve({ filter: /^@tauri-apps\/api\/core$/ }, () => ({ path: "tauri-core", namespace: "fixture" }));
    plugin.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({ contents: `export async function invoke(command) { window.__invokeCount = (window.__invokeCount || 0) + 1; if (window.__invokeCount === 1 || window.__rejectNextToast) { window.__rejectNextToast = false; throw new Error("Toast rejected"); } return null; }`, loader: "js" }));
  } }],
});

let snapshotQuestions = [];
const server = createServer((req, res) => {
  if (req.url === "/fixture.js") {
    res.setHeader("Content-Type", "text/javascript");
    res.end(bundle.outputFiles[0].text);
    return;
  }
  if (req.url?.startsWith("/api/app/")) {
    const url = new URL(req.url, "http://fixture");
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify(url.pathname === "/api/app/runtime/snapshot"
      ? { ok: true, questions: { questions: snapshotQuestions.filter((question) => question.sessionId === url.searchParams.get("sessionId")) } }
      : { ok: true, runs: [], actions: [] }));
    return;
  }
  res.end('<div id="root"></div><script>window.__TAURI_INTERNALS__={}; window.Audio=class { play(){ window.__audioAttempts=(window.__audioAttempts||0)+1; if(window.__audioFailures){ window.__audioFailures-=1; return Promise.reject(new Error("audio rejected")); } return Promise.resolve(); } pause(){} };</script><script type="module" src="/fixture.js"></script>');
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
try {
  const page = await browser.newPage();
  page.on("pageerror", (error) => console.error(`browser pageerror: ${error.stack || error}`));
  page.on("console", (message) => { if (message.type() === "error") console.error(`browser console: ${message.text()}`); });
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.waitForFunction(() => window.testApi, null, { timeout: 10000 });
  await page.evaluate(() => localStorage.setItem("vrcforge.agent-question.notification-sound.v1", JSON.stringify({ ordinary: { enabled: true, dataUrl: "data:audio/wav;base64,AAAA", name: "test.wav" }, blocking: { enabled: true, dataUrl: "data:audio/wav;base64,AAAA", name: "test.wav" } })));
  const question = { questionId: "question-browser-replay", sourceTool: "call_user", status: "pending", sessionId: "session-replay", question: "Continue?" };
  await page.evaluate((value) => window.testApi.notifyCallUserQuestions([value]), question);
  await page.waitForFunction(() => window.__invokeCount === 1, null, { timeout: 5000 });
  await page.evaluate((value) => window.testApi.notifyCallUserQuestions([value]), question);
  await page.waitForFunction(() => window.__invokeCount === 2, null, { timeout: 5000 });
  await page.evaluate((value) => window.testApi.notifyCallUserQuestions([value]), question);
  await new Promise((resolve) => setTimeout(resolve, 80));
  assert.equal(await page.evaluate(() => window.__invokeCount), 2, "third snapshot must not re-toast after successful delivery");
  assert.equal(await page.evaluate(() => window.__audioAttempts), 1, "native Toast rejection must not replay custom sound");

  const audioFailureQuestion = { ...question, questionId: "question-browser-audio-retry" };
  await page.evaluate(() => { window.__audioFailures = 1; window.__rejectNextToast = true; });
  await page.evaluate((value) => window.testApi.notifyCallUserQuestions([value]), audioFailureQuestion);
  await page.waitForFunction(() => window.__invokeCount === 3, null, { timeout: 5000 });
  await page.evaluate((value) => window.testApi.notifyCallUserQuestions([value]), audioFailureQuestion);
  await page.waitForFunction(() => window.__invokeCount === 4, null, { timeout: 5000 });
  await page.evaluate((value) => window.testApi.notifyCallUserQuestions([value]), audioFailureQuestion);
  await new Promise((resolve) => setTimeout(resolve, 80));
  assert.equal(await page.evaluate(() => window.__invokeCount), 4, "audio failure retry must suppress only after native success");
  assert.equal(await page.evaluate(() => window.__audioAttempts), 3, "audio failure must retry on the next snapshot");
  const history = Array.from({ length: 10 }, (_, index) => ({ questionId: `history-${index}`, sessionId: "session-replay", status: "answered" }));
  const pending = Array.from({ length: 7 }, (_, index) => ({ questionId: `pending-${index}`, sessionId: "session-replay", status: "pending", blocking: false }));
  const continuations = ["queued", "claimed", "running", "cancelling"].map((status, index) => ({
    questionId: `continuation-${status}`, sessionId: "session-replay", status: "answered",
    ...(index < 2 ? { runtimeContinuationStatus: status } : { runtimeContinuation: { status } }),
  }));
  snapshotQuestions = [...history, ...pending, ...continuations];
  await page.evaluate(() => {
    delete window.__TAURI_INTERNALS__;
    window.connectRuntime();
  });
  await page.waitForFunction(() => window.testApi.agentQuestions.some((question) => question.questionId === "history-0"));
  const questionIds = () => page.evaluate(() => window.testApi.agentQuestions.map((question) => question.questionId));
  assert.deepEqual(await questionIds(), [...history.slice(0, 8), ...pending, ...continuations].map((question) => question.questionId));
  await page.evaluate(() => window.switchSession("another-session"));
  await page.locator('#state[data-session="another-session"]').waitFor();
  await page.waitForFunction(() => !window.testApi.agentQuestions.some((question) => question.questionId === "history-0"));
  assert.deepEqual(await questionIds(), [...pending, ...continuations].map((question) => question.questionId));
  console.log("PASS real useRuntimeWorkspace replay: native reject retries silently, successful toast suppresses, audio failure retries once");
} finally {
  await browser.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
