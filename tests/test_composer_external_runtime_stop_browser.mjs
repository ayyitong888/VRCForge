import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { build } from "esbuild";

const root = process.cwd();
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundled = await build({
  stdin: {
    resolveDir: root,
    loader: "tsx",
    contents: `
      import React, { useState } from 'react';
      import { createRoot } from 'react-dom/client';
      import { initReactI18next } from 'react-i18next';
      import i18n from './src/i18n';
      import messages from './src/locales/en-US.json';
      import { Composer } from './src/components/chat/composer';
      import { ConversationCard, StreamingPhaseStatus } from './src/components/chat/conversation-card';
      import { applyRuntimeActivityDelta, projectRuntimeTurnActivity, runtimeActivityCancelTarget } from './src/hooks/use-runtime-turn-activity';
      import { runtimeTurnActivityStatus } from './src/lib/runtime-run-status';
      import { chatSidebarActivity } from './src/lib/sidebar-view';
      i18n.use(initReactI18next).init({ lng: 'en-US', resources: { 'en-US': { translation: messages } }, interpolation: { escapeValue: false } });
      const activeChat = { id: 'owner', sessionId: 'external-session', projectPath: '', title: 'Visible chat',
        items: [{ id: 'user-external-cid', type: 'user', text: 'Keep this accepted user message', clientTurnId: 'external-cid' }] };
      const currentRun = { sessionId: 'external-session', clientTurnId: 'external-cid', turnId: 'backend-turn', status: 'running' };
      const externalStream = { id: 'runtime-activity:external-cid', type: 'streaming', clientTurnId: 'external-cid', text: '',
        phase: 'verifying', timeline: [{ id: 'accepted-progress', sequence: 1, timestamp: '2026-11-04T19:05:00Z', kind: 'assistant',
          payload: { phase: 'commentary', summary: 'Accepted progress remains visible' } }] };
      window.cancelRequests = [];
      window.mockRequestAgentRunCancel = (endpoint, payload) => window.cancelRequests.push({ endpoint, payload });
      function Fixture() {
        const [runs, setRuns] = useState([currentRun]);
        const [chat, setChat] = useState(activeChat);
        const cancelTarget = runtimeActivityCancelTarget([chat], chat, runs);
        const runtimeActive = runtimeTurnActivityStatus(runs, activeChat.sessionId, 'external-cid');
        const transcript = applyRuntimeActivityDelta(
          projectRuntimeTurnActivity({ ...chat, items: [...chat.items, externalStream] }, runs[0]),
          { ...currentRun, textDelta: runs[0].status === 'cancel_requested' ? 'late text must be dropped' : '' },
        );
        const stream = transcript.items.find(item => item.id === externalStream.id);
        window.setRunStatus = status => setRuns([{ ...currentRun, status }]);
        window.setLegacyPlaceholder = () => setChat({ ...activeChat, items: [...activeChat.items, {
          id: 'legacy-placeholder', type: 'agent', response: { sessionId: activeChat.sessionId, clientTurnId: 'external-cid',
            turnId: 'external-cid', status: 'interrupted', continuationSource: 'foreground_interrupted' },
        }] });
        window.clearLegacyPlaceholder = () => setChat(activeChat);
        window.setQuestionRun = () => setRuns([{ ...currentRun, clientTurnId: 'external-cid:question:question-1' }]);
        window.setAmbiguousRuns = () => setRuns([
          currentRun,
          { sessionId: 'external-session', clientTurnId: 'other-cid', turnId: 'other-turn', status: 'running' },
        ]);
        return <>
          <div data-testid='user-transcript'>{chat.items.filter(item => item.type === 'user').map(item => item.text).join('\\n')}</div>
          <div data-testid='agent-replies'>{chat.items.filter(item => item.type === 'agent').length}</div>
          <div data-testid='sidebar-activity'>{chatSidebarActivity(transcript, '', runs) || 'none'}</div>
          <ConversationCard item={stream} runtimeActive={runtimeActive} initialUserContent={<div>Keep this accepted user message</div>} />
          <StreamingPhaseStatus item={stream} nowMs={Date.now()} showElapsed={false} runtimeActive={runtimeActive} />
          <Composer
            input='' setInput={() => {}} sending={Boolean(cancelTarget)} queueAllowed={false} stopDisabled={stream.stopRequested}
            onSubmit={event => event?.preventDefault()} onStop={() => {
              if (cancelTarget) window.mockRequestAgentRunCancel('http://fixture', { ...cancelTarget, reason: 'user_stop' });
            }}
            onSwitchMode={() => {}} actions={[]} commands={[]}
          />
        </>;
      }
      createRoot(document.getElementById('root')).render(<Fixture />);
    `,
  },
  bundle: true,
  write: false,
  format: "iife",
  platform: "browser",
  jsx: "automatic",
  plugins: [{
    name: "external-runtime-stop-stubs",
    setup(plugin) {
      plugin.onResolve({ filter: /^@tauri-apps\/api\/(webview|core)$/ }, ({ path }) => ({ path, namespace: "tauri-stub" }));
      plugin.onLoad({ filter: /.*/, namespace: "tauri-stub" }, ({ path }) => ({
        loader: "js",
        contents: path.endsWith("/webview")
          ? "export const getCurrentWebview = () => ({ onDragDropEvent: async () => () => {} });"
          : "export const convertFileSrc = (path) => path; export const invoke = async () => undefined;",
      }));
      plugin.onResolve({ filter: /^\.\.\/lib\/api\/agent-runtime$/ }, args => args.importer.endsWith("use-runtime-turn-activity.ts")
        ? { path: "agent-runtime", namespace: "agent-runtime-stub" } : undefined);
      plugin.onLoad({ filter: /.*/, namespace: "agent-runtime-stub" }, () => ({
        loader: "js",
        contents: "export const fetchAgentRunTimeline = async () => ({ok:true,run:null,events:[],nextSequence:0,hasMore:false}); export const fetchAgentTurnResponse = async () => ({ok:true,status:'missing'});",
      }));
    },
  }],
});

const browser = await chromium.launch({ channel: "msedge", headless: true });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setContent('<div id="root"></div>');
  await page.addScriptTag({ content: bundled.outputFiles[0].text });
  const primary = page.locator("[data-composer-primary-action]");
  await primary.waitFor();

  assert.equal(await primary.getAttribute("data-composer-primary-action"), "stop",
    "a uniquely owned active external runtime turn exposes Stop");
  assert.equal(await page.locator("[data-chat-current-activity]").textContent(), "Verifying result", "an active external turn keeps its real phase label");
  assert.equal(await page.locator("[data-testid=sidebar-activity]").textContent(), "running", "an active external turn keeps the sidebar running marker");
  await primary.click();
  assert.deepEqual(await page.evaluate(() => window.cancelRequests), [{ endpoint: "http://fixture", payload: {
    sessionId: "external-session", clientTurnId: "external-cid", turnId: "backend-turn", reason: "user_stop",
  } }], "Stop targets the exact session, client turn, and backend turn");

  await page.evaluate(() => window.setLegacyPlaceholder());
  await page.waitForFunction(() => document.querySelector("[data-composer-primary-action]")?.getAttribute("data-composer-primary-action") === "stop");
  await primary.click();
  assert.deepEqual(await page.evaluate(() => window.cancelRequests[1]), { endpoint: "http://fixture", payload: {
    sessionId: "external-session", clientTurnId: "external-cid", turnId: "backend-turn", reason: "user_stop",
  } }, "a legacy placeholder receipt cannot hide cancellation for the active backend turn");
  await page.evaluate(() => window.clearLegacyPlaceholder());

  await page.evaluate(() => window.setRunStatus("cancel_requested"));
  const stopping = page.locator('[data-vrcforge-streaming-phase="stopping"]');
  await stopping.waitFor();
  assert.equal(await stopping.textContent(), "Stopping");
  assert.equal(await primary.isDisabled(), true);
  assert.doesNotMatch(await page.locator("#root").innerText(), /late text must be dropped/);

  await page.evaluate(() => window.setRunStatus("blocked"));
  await page.waitForFunction(() => document.querySelector("[data-composer-primary-action]")?.getAttribute("data-composer-primary-action") === "send");
  assert.equal(await page.locator("[data-composer-stop]").count(), 0, "a terminal blocked run is not shown as active");
  assert.equal(await page.locator("[data-chat-current-activity]").count(), 0, "the terminal external stream no longer claims it is verifying");
  assert.equal(await page.locator("[data-testid=sidebar-activity]").textContent(), "none", "the terminal external stream no longer lights the sidebar as running");
  assert.ok((await page.locator("[data-vrcforge-live-runtime-timeline]").textContent()).includes("Accepted progress remains visible"), "the accepted stream timeline remains visible after terminal projection");
  assert.equal(await page.locator("[data-testid=user-transcript]").textContent(), "Keep this accepted user message");
  assert.equal(await page.locator("[data-testid=agent-replies]").textContent(), "0", "no reply is fabricated from the ledger status");

  await page.evaluate(() => window.setQuestionRun());
  await page.waitForFunction(() => document.querySelector("[data-composer-primary-action]")?.getAttribute("data-composer-primary-action") === "send");
  assert.equal(await page.locator("[data-composer-stop]").count(), 0, "question continuation retains its dedicated cancellation owner");

  await page.evaluate(() => window.setAmbiguousRuns());
  await page.waitForFunction(() => document.querySelector("[data-composer-primary-action]")?.getAttribute("data-composer-primary-action") === "send");
  assert.equal(await page.locator("[data-composer-stop]").count(), 0, "the composer does not guess between multiple active turns");
  assert.deepEqual(errors, []);
  console.log("external runtime Stop browser test: exact cancellation target, terminal blocked state, transcript retention, question and ambiguity ownership passed");
} finally {
  await browser.close();
}
