import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const baseline = process.argv.includes("--baseline");

const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
      import React from 'react';
      import { createRoot } from 'react-dom/client';
      import i18n from 'i18next';
      import { initReactI18next } from 'react-i18next';
      import { useChatSessions } from './src/hooks/use-chat-sessions';
      await i18n.use(initReactI18next).init({lng:'en',resources:{en:{translation:{}}}});
      const noop=()=>{};
      const paths=[];
      const chat={id:'chat',sessionId:'session',title:'historical',revision:0,
        projectPath:'C:/unmounted/project',projectType:'unity',
        createdAt:'2026-01-01T00:00:00Z',updatedAt:'2026-01-01T00:00:00Z',
        items:[{id:'u1',type:'user',text:'historical',createdAt:'2026-01-01T00:00:00Z'}]};
      function Fixture(){
        const sessions=useChatSessions({endpoint:location.origin,activeView:'chat',runtimeConnected:true,
          projectPrefsReady:true,projectPaths:paths,customProjectPaths:paths,activeProjectPath:'',
          setActiveProjectPath:noop,setActiveView:noop,setError:noop,expandProjectGroup:noop,
          initialChatState:{chats:[],activeChatId:''}});
        window.notify=sessions.notifyChatStorageChanged;
        return <pre id="state">{JSON.stringify({blocked:sessions.chatPersistenceBlocked,recoveries:sessions.chatRecoveries})}</pre>;
      }
      createRoot(document.getElementById('root')).render(<Fixture/>);
    `,
  },
  bundle: true,
  write: false,
  format: "esm",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"test"' },
  plugins: baseline ? [{
    name: "use-baseline-chat-sessions",
    setup(buildApi) {
      buildApi.onLoad({ filter: /use-chat-sessions\.ts$/ }, () => ({
        contents: execFileSync("git", ["show", "HEAD:src/hooks/use-chat-sessions.ts"], { encoding: "utf8" }),
        loader: "tsx",
      }));
    },
  }] : [],
});

let gets = 0;
const server = createServer((req, res) => {
  if (req.url.startsWith("/api/app/chats")) {
    gets += 1;
    res.setHeader("Content-Type", "application/json");
    const historicalUnavailable = gets === 2;
    const blocked = gets === 3;
    res.end(JSON.stringify({
      ok: true,
      chats: [{
        id: "chat", sessionId: "session", title: "historical", revision: 0,
        projectPath: "C:/unmounted/project", projectType: "unity",
        createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z",
        items: [{ id: "u1", type: "user", text: "historical", createdAt: "2026-01-01T00:00:00Z" }],
      }],
      count: 1,
      sources: historicalUnavailable ? [{
        storeId: "chat.unavailable.historical", scope: "project", exists: false,
        digest: "", status: "unsupported", reason: "project_unavailable",
        projectPath: "C:/unmounted/project", unavailable: true, indexed: false, count: 0,
      }] : [],
      recoveries: blocked ? [{
        storeId: "chat.project-damaged", scope: "project", status: "needs_repair",
        reason: "snapshot_changed", requiresApproval: false, invalidCount: 1,
      }] : [],
      writeBlocked: blocked,
    }));
    return;
  }
  if (req.url === "/fixture.js") {
    res.setHeader("Content-Type", "text/javascript");
    res.end(bundle.outputFiles[0].text);
    return;
  }
  res.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
});

await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  const origin = `http://127.0.0.1:${server.address().port}`;
  await page.route("**/*", (route) => route.request().url().startsWith(origin) ? route.continue() : route.abort());
  await page.goto(origin);
  await page.waitForFunction(() => document.querySelector("#state")?.textContent.includes('"blocked":false'));
  await page.evaluate(() => window.notify());
  await page.waitForFunction(() => document.querySelector("#state")?.textContent.includes('"recoveries":[]'));
  await page.waitForTimeout(250);
  const state = await page.evaluate(() => JSON.parse(document.querySelector("#state").textContent));
  assert.equal(state.blocked, false, "an unindexed unavailable historical project must not block healthy chat storage");
  assert.deepEqual(state.recoveries, []);
  assert.equal(gets, 2, "the first storage notification should perform one refresh");
  await page.evaluate(() => window.notify());
  await page.waitForFunction(() => document.querySelector("#state")?.textContent.includes('"blocked":true'));
  const blockedState = await page.evaluate(() => JSON.parse(document.querySelector("#state").textContent));
  assert.equal(blockedState.recoveries.length, 1, "a real backend recovery must block persistence");
  await page.evaluate(() => window.notify());
  await page.waitForFunction(() => document.querySelector("#state")?.textContent.includes('"blocked":false'));
  const recoveredState = await page.evaluate(() => JSON.parse(document.querySelector("#state").textContent));
  assert.equal(recoveredState.blocked, false, "a healthy backend payload must clear the old banner");
  assert.deepEqual(recoveredState.recoveries, []);
  assert.ok(gets >= 4, "each storage notification should perform a serialized refresh");
  console.log("PASS unavailable historical project stays non-blocking; real recovery blocks; healthy refresh clears banner");
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
