import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createServer as createHttpServer } from "node:http";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { extname, resolve, sep } from "node:path";
import { chromium } from "playwright";
import { build } from "vite";

const root = resolve(import.meta.dirname, "..");
const baseline = process.env.VRCFORGE_SURFACE_BASELINE;
const issue = process.argv[2] || "all";
const snapshots = [];
const pending = [];
const posts = [];
const merges = [];
const handoffs = [];
const date = "2026-10-28T10:00:00Z";
const chatA = { id: "surface-A", sessionId: "surface-session-A", title: "Surface A", projectPath: "", projectType: "general", createdAt: date, updatedAt: date, revision: 0, items: [] };
const chatB = { ...chatA, id: "surface-B", sessionId: "surface-session-B", title: "Surface B" };
const chatC = { ...chatB, id: "surface-C", sessionId: "surface-session-C", title: "Surface C", projectPath: "E:/fixtures/other-project", projectType: "unity" };
if (issue === "composer-notification") {
  chatA.items = [
    { id: "copy-source", type: "user", text: "Review the draft before sending.", createdAt: date },
    { id: "copy-other", type: "user", text: "Keep this other message separate.", createdAt: date },
  ];
}
if (issue === "app-tooltips") {
  chatA.items = [
    { id: "tooltip-user", type: "user", text: "Check this draft.", createdAt: date },
    { id: "tooltip-agent", type: "agent", createdAt: date, response: {
      ok: true, sessionId: chatA.sessionId, turnId: "tooltip-turn", status: "completed",
      plan: { reply: "The draft is ready to send." },
    } },
  ];
}
if (issue === "sidebar") {
  chatA.projectPath = "E:/fixtures/sidebar-workbench";
  chatA.items = [{ id: "parent-message", type: "user", text: "Original parent transcript", createdAt: date }];
}
const userLink = "vrcforge://chat/surface-B";
const composerTargetLink = "vrcforge://chat/cross-project-fixture-20260101-1234567890";
const composerTargetTitle = "另一个工程中的测试对话";
const externalChatLink = "codex://threads/019f052c-5b66-7ad3-829b-825371654321";
if (issue === "parent-activity") {
  chatA.items = [{ id: "parent-delegation", type: "agent", createdAt: date, response: {
    sessionId: chatA.sessionId, turnId: "delegating-turn", clientTurnId: "delegating-request", ok: true,
    plan: { reply: "Checking the materials.", nextStep: "waiting_for_tool", reason: "sub_agent_pending" },
  } }];
}
if (issue === "external-chat-link") {
  chatA.items = [{ id: "codex-link", type: "user", createdAt: date, text: [
    `详见：${externalChatLink}。`,
    `[原始会话](${externalChatLink} "另一个应用")`,
    `[查询参数](${externalChatLink}?prompt=write)`,
    `[片段](${externalChatLink}#run)`,
    "[新会话](codex://threads/new)",
    "[自动化](codex://automations)",
    `[图片](https://example.com/image.png)`,
  ].join("\n\n") }];
}
if (issue === "chat-composer-link") {
  chatB.id = composerTargetLink.split("/").at(-1);
  chatB.title = composerTargetTitle;
  chatB.projectPath = chatC.projectPath;
  chatB.projectType = chatC.projectType;
  chatB.items = [{ id: "original-cross-project-transcript", type: "user", createdAt: date, text: "Original project transcript" }];
}
if (issue === "chat-links") {
  const tool = "vrcforge_send_message";
  const source = { id: "link-target", type: "user", text: "Other project transcript", createdAt: date,
    forwardedByAgent: true, forwardedFrom: { sourceChatId: chatA.id, sourceClientTurnId: "source-turn", sourceActionId: "source-action" } };
  chatC.items = [source];
  chatA.items = [{ id: "link-user", type: "user", text: `详见：${userLink}。 无效链接：${userLink}?command=write`, createdAt: date }];
  chatB.items = [{ id: "link-agent", type: "agent", createdAt: date, response: {
    ok: true, sessionId: chatB.sessionId, turnId: "link-agent-turn",
    plan: { reply: "[另一个项目](vrcforge://chat/surface-C \"中文标题\") · [Missing chat](vrcforge://chat/unknown)" },
    steps: [{ actionId: "lookup-action", result: { chatId: chatC.id } }],
    timeline: [
      { id: "lookup-call", kind: "tool_call", timestamp: date, sequence: 1, payload: { actionId: "lookup-action", tool, status: "started" } },
      { id: "lookup-receipt", kind: "tool_result", timestamp: date, sequence: 2, payload: { actionId: "lookup-action", tool, status: "ok" } },
    ],
  } }];
}
const handoffTargets = Array.from({ length: 35 }, (_, index) => ({
  ...chatA, id: `handoff-${index + 1}`, sessionId: `handoff-session-${index + 1}`, title: `Handoff target ${index + 1}`,
}));
if (issue === "handoff-catalog") {
  for (const chat of [chatA, ...handoffTargets]) {
    chat.projectPath = chatC.projectPath;
    chat.projectType = chatC.projectType;
  }
  chatC.projectPath = "E:/fixtures/foreign-project";
}
const fixtureChats = issue === "handoff-catalog" ? [chatA, chatB, chatC, ...handoffTargets]
  : issue === "chat-links" ? [chatA, chatB, chatC] : [chatA, chatB];
const catalog = fixtureChats.map((chat) => ({ ...chat, sourceProjectPath: chat.projectPath, sourceStoreId: "app" }));
const chatDigests = Object.fromEntries(fixtureChats.map((chat, index) => [chat.id, String(index + 1).repeat(64)]));
const chatMetadataShapes = Object.fromEntries(fixtureChats.map((chat) => [
  chat.id, Object.fromEntries(Object.keys(chat).map((field) => [field, true])),
]));
const taskA = { id: "surface-task-A", displayName: "Surface worker A", task: "Review the task", role: "llm_readonly", status: "running", parentChatId: chatA.id, parentSessionId: chatA.sessionId, revision: 2, createdAt: date };
const done = { ...taskA, status: "completed", revision: 3, summary: "Durable result v3", result: { summaryText: "Durable result v3" } };
const taskB = { ...done, id: "surface-task-B", parentChatId: chatB.id, parentSessionId: chatB.sessionId, displayName: "Surface worker B" };
const bootstrap = {
  ok: true, app: { name: "VRCForge" },
  health: { ok: true, version: "fixture", components: {} },
  apiConfig: { provider: issue === "composer-notification" ? "chatgpt" : "custom", configured: true, base_url: "", model: "fixture", api_type: "auto" },
  permission: { pending: [] }, approvals: [],
};
let tasks = [taskA];
let detail = taskA;
let holdNextList = false;
let holdNextParent = "";
let holdPost = false;
let postFailure = true;
let listFailure = false;
let missingDetail = false;
let catalogOnly = false;
const backend = createHttpServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  const respond = (body, status = 200) => {
    res.writeHead(status, { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" });
    res.end(JSON.stringify(body));
  };
  if (url.pathname === "/api/app/bootstrap") return respond(bootstrap);
  if (url.pathname === "/api/app/chats") {
    if (req.method === "GET") {
      if (url.searchParams.get("catalog")) return respond({ ok: true, entries: catalog, sources: [], recoveries: [],
        pendingChatIds: catalogOnly ? [] : fixtureChats.map((chat) => chat.id) });
      const selected = url.searchParams.get("chatId");
      return respond({ ok: true, chats: selected ? fixtureChats.filter((chat) => chat.id === selected) : fixtureChats,
        sources: [], recoveries: [], pendingChatIds: [], chatDigests, chatMetadataShapes });
    }
    return respond({ ok: true, count: 2, path: "fixture.json", chatDigests, chatMetadataShapes });
  }
  if (url.pathname === "/api/app/chats/single") return respond({ ok: true, count: 1, path: "fixture.json", chatDigests, chatMetadataShapes });
  if (issue === "app-action-errors" && req.method === "POST") {
    if (url.pathname === "/api/app/agent/goals") return respond({ detail: "Fixture goal storage failure" }, 500);
    if (url.pathname === "/api/app/chat-attachments/uploads") return respond({ detail: "Fixture attachment format rejected" }, 415);
  }
  if (url.pathname === "/api/app/session-handoff/send" && req.method === "POST") {
    const handoff = JSON.parse(await readBody(req));
    handoffs.push(handoff);
    return respond({ ok: true, handoff: { id: "fixture-handoff", status: "delivered", source_chat_id: handoff.sourceChatId, target_chat_id: handoff.targetChatId } });
  }
  if (url.pathname === "/api/app/sub-agents" && req.method === "GET") {
    if (listFailure) return respond({ detail: "Fixture failed listing workers" }, 500);
    const parent = url.searchParams.get("parentChatId");
    const rows = parent ? tasks.filter((task) => task.parentChatId === parent) : tasks.slice(0, 50);
    snapshots.push({ parent, revisions: rows.map((row) => row.revision) });
    const body = { ok: true, schema: "fixture", tasks: rows, count: rows.length, roles: [] };
    if (holdNextList || (holdNextParent && holdNextParent === parent)) {
      holdNextList = false;
      holdNextParent = "";
      pending.push(() => respond(body));
      return;
    }
    return respond(body);
  }
  if (url.pathname === "/api/app/sub-agents" && req.method === "POST") {
    const body = JSON.parse(await readBody(req));
    posts.push(body);
    const send = () => postFailure ? respond({ detail: "Fixture rejected delegation" }, 400) : respond({ ok: true, task: detail });
    if (holdPost) {
      holdPost = false;
      pending.push(send);
      return;
    }
    return send();
  }
  if (url.pathname.endsWith("/merge")) {
    merges.push(JSON.parse(await readBody(req)));
    return respond({ detail: "Fixture revision conflict" }, 409);
  }
  if (url.pathname.startsWith("/api/app/sub-agents/")) {
    if (missingDetail) return respond({ detail: "Fixture missing worker detail" }, 404);
    return respond({ ok: true, task: detail });
  }
  return respond({ ok: true, tasks: [], actions: [], questions: [], runs: [], items: [], sources: [], settings: {}, approvals: [] });
});

async function readBody(req) {
  let value = "";
  for await (const chunk of req) value += chunk;
  return value;
}

async function waitFor(predicate, label) {
  const end = Date.now() + 13000;
  while (Date.now() < end) {
    if (predicate()) return;
    await new Promise((pause) => setTimeout(pause, 30));
  }
  assert.fail(label);
}

const baselineModules = new Set([
  "src/App.tsx", "src/components/subagents/sub-agent-sidebar.tsx", "src/hooks/use-composer-draft.ts",
  "src/lib/api/sub-agents.ts", "src/lib/subagent-delegate.ts", "src/lib/subagent-state.ts",
  ...(["chat-links", "external-chat-link"].includes(issue) ? ["src/components/chat/chat-markdown.tsx", "src/components/sidebar/sidebar-menus.tsx", "src/hooks/use-chat-deep-links.ts"] : []),
  ...(issue === "composer-notification" ? ["src/components/chat/conversation-card.tsx", "src/components/ui/transient-failure-toast.tsx"] : []),
]);
const scratch = await mkdtemp(resolve(tmpdir(), "vrcforge-subagent-surface-"));
const frontend = resolve(scratch, "frontend");
await build({
  root, logLevel: "error", build: { outDir: frontend, minify: false },
  plugins: baseline ? [{
    name: "read-baseline-app",
    enforce: "pre",
    load(id) {
      const relative = id.slice(root.length + 1).replaceAll("\\", "/");
      if (baselineModules.has(relative)) return execFileSync("git", ["show", `${baseline}:${relative}`], { cwd: root, encoding: "utf8" });
    },
  }] : [],
});
const contentTypes = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2" };
const ui = createHttpServer(async (req, res) => {
  const path = resolve(frontend, "." + new URL(req.url, "http://localhost").pathname);
  if (path !== frontend && !path.startsWith(frontend + sep)) { res.writeHead(403); return res.end(); }
  try {
    const file = path === frontend ? resolve(frontend, "index.html") : path;
    const body = await readFile(file);
    res.writeHead(200, { "Content-Type": contentTypes[extname(file)] || "application/octet-stream" });
    res.end(body);
  } catch {
    res.writeHead(404); res.end();
  }
});
let browser, currentPage;
try {
  await new Promise((ready) => backend.listen(0, "127.0.0.1", ready));
  const base = `http://127.0.0.1:${backend.address().port}`;
  await new Promise((ready) => ui.listen(0, "127.0.0.1", ready));
  const origin = `http://127.0.0.1:${ui.address().port}`;
  browser = await chromium.launch({ channel: process.platform === "win32" ? "msedge" : undefined, headless: true });

  const open = async (desktop = false, startupUrl = "", options = {}) => {
    const page = await browser.newPage({ viewport: options.viewport || { width: 1600, height: 1000 } });
    currentPage = page;
    page.setDefaultTimeout(8000);
    page.on("pageerror", (error) => console.error("App fixture:", error.message));
    page.on("console", (message) => {
      if (message.type() === "error" && !message.text().startsWith("Failed to load resource")) console.error(message.text());
    });
    await page.addInitScript(({ desktop, base, startupUrl, linkProjectPath, composerLinkFixture, noticeFixture, theme }) => {
      localStorage.setItem("vrcforge_onboarded", "true");
      localStorage.setItem("vrcforge-locale", "en-US");
      localStorage.setItem("vrcforge_startup_animation", "false");
      if (theme) localStorage.setItem("vrcforge_theme", theme);
      if (!desktop) return;
      const callbacks = new Map();
      const listeners = new Map();
      window.codexOpenUrls = [];
      window.sentChatMessages = [];
      let nextId = 0;
      window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener: (_event, id) => listeners.delete(id) };
      window.subAgentEvent = (event, payload) => {
        for (const [id, listener] of listeners) {
          if (event === listener.event) callbacks.get(listener.handler)({ id, event, payload });
        }
      };
      window.subAgentHasListener = (event) => [...listeners.values()].some((listener) => listener.event === event);
      window.__TAURI_INTERNALS__ = {
        metadata: { currentWindow: { label: "main" }, currentWebview: { label: "main" } },
        transformCallback(callback) { callbacks.set(++nextId, callback); return nextId; },
        async invoke(command, args) {
          if (command === "plugin:event|listen") { listeners.set(++nextId, args); return nextId; }
          if (command === "start_backend") return { mode: "attached", started: true, endpoint: base, message: "fixture" };
          if (command === "take_pending_vsk_paths") return [];
          if (command === "plugin:deep-link|get_current") return startupUrl ? [startupUrl] : [];
          if (noticeFixture && command === "chatgpt_oauth_status") return {
            accounts: [{ id: "fixture-account", connected: true }], activeAccountId: "fixture-account", activeModel: "fixture",
          };
          if (noticeFixture && command === "chatgpt_oauth_models") return {
            models: [{ id: "fixture", supportedReasoningLevels: [{ effort: "low" }, { effort: "high" }] }],
          };
          if (command === "open_codex_chat_url") { window.codexOpenUrls.push(args.url); return; }
          if (command === "fetch_project_prefs" && linkProjectPath) return {
            ok: true, customPaths: [linkProjectPath], customProjects: [{ path: linkProjectPath, projectType: "unity" }],
          };
          const request = args?.request || {};
          if (composerLinkFixture && command === "save_chat_record") {
            const { chatId } = request.body;
            return { ok: true, chatDigests: { [chatId]: "0".repeat(64) }, chatMetadataShapes: { [chatId]: { id: true } } };
          }
          if (composerLinkFixture && command === "send_agent_message") {
            window.sentChatMessages.push(request.message);
            return { ok: true, status: "completed", sessionId: request.sessionId, turnId: request.clientTurnId, clientTurnId: request.clientTurnId,
              plan: { reply: `Assistant mention: ${request.message.split(" ").find(word => word.startsWith("vrcforge://"))}`, nextStep: "done" } };
          }
          if (command === "select_unity_project") return { ok: true, selectedProjectPath: request.projectPath };
          if (command === "refresh_unity_readiness") return { ok: true, health: { ok: true, components: {} } };
          let path;
          if (command === "fetch_app_bootstrap") path = "/api/app/bootstrap";
          else if (command === "fetch_chats") {
            const query = new URLSearchParams();
            if (request.catalog) query.set("catalog", "1");
            if (request.chatId) query.set("chatId", request.chatId);
            path = "/api/app/chats?" + query;
          }
          else if (command === "fetch_sub_agents") {
            const query = new URLSearchParams();
            if (request.parentChatId) query.set("parentChatId", request.parentChatId);
            path = "/api/app/sub-agents?" + query;
          } else if (command === "fetch_sub_agent") path = "/api/app/sub-agents/" + request.id;
          else if (command === "session_handoff_action" && request.action === "send") {
            return (await fetch(base + "/api/app/session-handoff/send", {
              method: "POST", body: JSON.stringify(request.body),
            })).json();
          }
          else return { ok: true, items: [], questions: [], runs: [], actions: [], settings: {} };
          return (await fetch(base + path)).json();
        },
      };
    }, { desktop, base, startupUrl, composerLinkFixture: issue === "chat-composer-link", noticeFixture: issue === "composer-notification",
      linkProjectPath: ["chat-links", "chat-composer-link", "app-tooltips"].includes(issue) ? chatC.projectPath : ["handoff-catalog", "sidebar"].includes(issue) ? chatA.projectPath : "",
      theme: options.theme });
    await page.route("**/*", async (route) => {
      const url = new URL(route.request().url());
      if (url.origin === origin || url.origin === base) return route.continue();
      if (url.origin !== "http://127.0.0.1:8757") return route.abort();
      const response = await route.fetch({ url: base + url.pathname + url.search });
      return route.fulfill({ response });
    });
    await page.goto(origin, { waitUntil: "domcontentloaded" });
    if (!startupUrl) await page.getByText("Surface A", { exact: true }).first().click();
    await page.locator("[data-chat-composer-dock] textarea:not([disabled])").first().waitFor();
    return page;
  };

  const openList = async (page) => {
    const list = page.locator("[data-vrcforge-subagent-sidebar-list]");
    if (!await list.isVisible()) await page.locator("[data-vrcforge-open-subagent-surface]").click();
    await list.waitFor();
    return list;
  };
  const openThread = async (page) => {
    const list = await openList(page);
    await list.locator(`[data-vrcforge-open-subagent-thread="${taskA.id}"]`).click();
    return page.locator("[data-vrcforge-subagent-workspace-surface]");
  };

  const tooltipText = (page, label) => page.getByRole("tooltip").filter({ hasText: label });
  const assertTooltip = async (page, target, label) => {
    assert.equal(await target.getAttribute("title"), null, `${label} must not use a native title`);
    await target.hover();
    const tooltip = tooltipText(page, label);
    await tooltip.waitFor();
    assert.equal(await tooltip.innerText(), label);
    return tooltip;
  };

  if (issue === "composer-notification") {
    async function assertCopyFeedback(button, copied) {
      await button.locator(copied ? "svg.lucide-check" : "svg.lucide-copy").waitFor();
      const label = copied ? "Copied" : "Copy";
      assert.equal(await button.getAttribute("title"), null);
      assert.equal(await button.getAttribute("aria-label"), label);
    }

    for (const [width, height, theme] of [[1600, 1000, "light"], [900, 640, "dark"]]) {
      const page = await open(true, "", { viewport: { width, height }, theme });
      await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
      const dock = page.locator("[data-chat-composer-dock]");
      const composer = dock.locator("textarea");
      const permission = dock.getByRole("button", { name: "Restricted", exact: true });
      const reasoning = dock.getByRole("button", { name: "Reasoning effort" });
      const copy = page.locator('[data-conversation-item-id="copy-source"]').getByRole("button", { name: /^(Copy|Copied)$/ });
      const otherCopy = page.locator('[data-conversation-item-id="copy-other"]').getByRole("button", { name: /^(Copy|Copied)$/ });
      const notice = page.locator(".transient-failure-toast");
      const dismiss = notice.getByRole("button", { name: "Dismiss notification" });
      await reasoning.waitFor();

      async function assertComposerClear() {
        const noticeBox = await notice.boundingBox();
        const dockBox = await dock.boundingBox();
        const overlaps = noticeBox.x < dockBox.x + dockBox.width && noticeBox.x + noticeBox.width > dockBox.x
          && noticeBox.y < dockBox.y + dockBox.height && noticeBox.y + noticeBox.height > dockBox.y;
        assert.equal(overlaps, false, `notification overlaps the composer (${JSON.stringify({ noticeBox, dockBox })})`);
        assert.ok(noticeBox.x >= 0 && noticeBox.x + noticeBox.width <= width
          && noticeBox.y >= 0 && noticeBox.y + noticeBox.height <= height, "notification fits the viewport");
        for (const control of [composer, permission, reasoning, dock.getByRole("button", { name: "Send", exact: true })]) {
          assert.equal(await control.evaluate(el => {
            const { x, y, width, height } = el.getBoundingClientRect();
            return el.contains(document.elementFromPoint(x + width / 2, y + height / 2));
          }), true, "composer controls receive pointer events while the notice is visible");
        }
      }

      await composer.fill("Draft preserved while copying");
      await page.clock.install();
      await page.evaluate(() => {
        const writeText = navigator.clipboard.writeText.bind(navigator.clipboard);
        window.completedClipboardWrites = 0;
        navigator.clipboard.writeText = async (text) => {
          await writeText(text);
          window.completedClipboardWrites += 1;
        };
      });
      await assertTooltip(page, copy, "Copy");
      await copy.click();
      await assertCopyFeedback(copy, true);
      await tooltipText(page, "Copied").waitFor();
      assert.equal(await notice.count(), 0, "copy success must not create a workspace notification");
      await assertCopyFeedback(otherCopy, false);
      assert.equal(await page.evaluate(() => navigator.clipboard.readText()), chatA.items[0].text);
      await page.clock.fastForward(1100);
      await copy.click();
      await page.waitForFunction(() => window.completedClipboardWrites === 2);
      await page.clock.fastForward(1000);
      await assertCopyFeedback(copy, true);

      await otherCopy.click();
      await assertCopyFeedback(otherCopy, true);
      await assertCopyFeedback(copy, true);
      assert.equal(await page.evaluate(() => navigator.clipboard.readText()), chatA.items[1].text);
      await page.clock.fastForward(2000);
      await assertCopyFeedback(copy, false);
      await assertCopyFeedback(otherCopy, false);
      await page.getByRole("tooltip").filter({ hasText: /^Copy$/ }).waitFor();

      await page.evaluate(() => {
        navigator.clipboard.writeText = async () => { throw new Error("Fixture clipboard rejected"); };
      });
      await copy.click();
      await notice.getByRole("alert").getByText("Copy failed", { exact: true }).waitFor();
      await notice.getByRole("alert").getByText("The clipboard rejected the copy request.", { exact: true }).waitFor();
      await assertComposerClear();
      await assertCopyFeedback(copy, false);
      await permission.click();
      const autoApprove = dock.getByRole("button", { name: /Auto Approve/ });
      await autoApprove.waitFor();
      await dock.getByRole("button", { name: /Restricted Approve writes/ }).click();
      await autoApprove.waitFor({ state: "hidden" });
      await reasoning.click();
      await page.getByRole("menu", { name: "Reasoning effort" }).waitFor();
      await page.keyboard.press("Escape");
      await composer.click();
      await composer.press("End");
      await page.keyboard.type("!");
      assert.equal(await composer.inputValue(), "Draft preserved while copying!");
      await dismiss.click();
      await notice.waitFor({ state: "hidden" });
      await page.close();
    }

    tasks = [done];
    detail = done;
    const page = await open(true);
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
    const panel = await openThread(page);
    const headerCopy = panel.locator("[data-subagent-copy-reply]");
    const childCopy = panel.locator("[data-subagent-user-message]").getByRole("button", { name: /^(Copy|Copied)$/ });
    await headerCopy.click();
    await assertCopyFeedback(headerCopy, true);
    await assertCopyFeedback(childCopy, false);
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), done.result.summaryText);
    await childCopy.click();
    await assertCopyFeedback(childCopy, true);
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), done.task);
    assert.equal(await page.locator(".transient-failure-toast").count(), 0);
    await page.close();
  }

  if (issue === "app-tooltips") {
    const page = await open(true, "", { viewport: { width: 900, height: 620 }, theme: "dark" });
    const left = page.locator('[data-layout-splitter="left"]');
    const right = page.locator('[data-layout-splitter="right"]');
    const hideSidebar = page.getByRole("button", { name: "Hide runtime sidebar" });
    await assertTooltip(page, left, "Resize left pane");
    await assertTooltip(page, right, "Resize right pane");
    const tooltip = await assertTooltip(page, hideSidebar, "Hide runtime sidebar");
    const bounds = await tooltip.boundingBox();
    assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= 900 && bounds.y + bounds.height <= 620,
      "tooltip must fit the viewport next to the rightmost control");
    assert.equal(await tooltip.evaluate(el => getComputedStyle(el).pointerEvents), "none");
    assert.ok((await hideSidebar.getAttribute("aria-describedby")).split(" ").includes(await tooltip.getAttribute("id")));
    await page.keyboard.press("Escape");
    await tooltip.waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "New Project", exact: true }).hover();
    await page.keyboard.press("Tab");
    await hideSidebar.focus();
    await tooltip.waitFor();
    await page.keyboard.press("Escape");
    await tooltip.waitFor({ state: "hidden" });
    assert.equal(await hideSidebar.evaluate(button => button === document.activeElement), true);

    const userCopy = page.locator('[data-conversation-item-id="tooltip-user"]').getByRole("button", { name: "Copy", exact: true });
    const agentCopy = page.locator('[data-conversation-item-id="tooltip-agent"]').getByRole("button", { name: "Copy", exact: true }).last();
    const agentTip = await assertTooltip(page, agentCopy, "Copy");
    const agentBox = await agentCopy.boundingBox();
    const agentTipBox = await agentTip.boundingBox();
    const history = await page.locator("[data-chat-history-scroll]").boundingBox();
    assert.ok(agentTipBox.y >= agentBox.y + agentBox.height, "assistant actions place the tooltip below to preserve the reply text");
    assert.ok(agentTipBox.y + agentTipBox.height <= history.y + history.height, "tooltip does not cover the composer");
    const userTip = await assertTooltip(page, userCopy, "Copy");
    const userBox = await userCopy.boundingBox();
    const userTipBox = await userTip.boundingBox();
    assert.ok(userTipBox.y + userTipBox.height <= userBox.y, "user message actions retain the above placement");

    await page.getByRole("button", { name: "New Project", exact: true }).click();
    await page.getByRole("heading", { name: "Select Project" }).waitFor();
    const editPath = page.getByRole("button", { name: "Edit recorded path" });
    await assertTooltip(page, editPath, "Edit recorded path");
    await page.close();
  }

  if (issue === "sidebar") {
    const startedAt = new Date(Date.now() - 70_000).toISOString();
    tasks = [
      { ...taskA, summary: "Read the project configuration\nChecking the last referenced asset", startedAt },
      { ...taskA, id: "surface-task-C", displayName: "Package reviewer", startedAt },
      { ...taskA, id: "surface-task-D", displayName: "Dependency reviewer", status: "queued" },
      { ...done, id: "surface-task-E", displayName: "Build report reviewer", startedAt: date, stoppedAt: new Date(Date.parse(date) + 65_000).toISOString() },
      { ...done, id: "surface-task-F", displayName: "Documentation reviewer", status: "cancelled" },
    ];
    detail = tasks[0];
    const artifactDir = resolve(root, ".tmp", "subagent-sidebar-replay");
    await mkdir(artifactDir, { recursive: true });
    for (const [theme, width] of [["light", 1480], ["dark", 1000]]) {
      const page = await open(true, "vrcforge://chat/surface-A", { theme, viewport: { width, height: 860 } });
      const workbench = page.locator("[data-vrcforge-project-workbench]");
      const entrance = workbench.locator("[data-vrcforge-project-sub-agents]");
      const composer = page.locator("[data-chat-composer-dock] textarea:not([disabled])");
      await entrance.getByText("3 running", { exact: true }).waitFor();
      assert.equal(await page.locator("html").evaluate(node => node.classList.contains("dark")), theme === "dark");
      await composer.fill("Draft survives right sidebar navigation");
      assert.equal(await entrance.getByRole("heading", { name: "Sub agents" }).count(), 1);
      assert.equal(await entrance.getByText("3 running", { exact: true }).count(), 1);
      assert.equal(await entrance.getByRole("button").count(), 1);
      assert.equal(await entrance.locator("svg.lucide-bot").count(), 3);
      await workbench.getByRole("button", { name: "Desktop actions" }).click();
      await workbench.locator("[data-vrcforge-project-desktop-actions]").waitFor();
      if (theme === "light") await page.screenshot({ path: resolve(artifactDir, "entry-light.png") });
      await entrance.getByRole("button").focus();
      await page.keyboard.press("Enter");
      const list = page.locator("[data-vrcforge-subagent-sidebar-list]");
      await list.waitFor();
      const sidebar = page.locator("[data-vrcforge-subagent-sidebar]");
      assert.equal(await sidebar.getByRole("heading", { name: "Sub agents" }).count(), 1);
      assert.equal(await sidebar.getByRole("heading", { name: "Sub agents" }).evaluate(node => document.activeElement === node), true);
      assert.equal(await list.locator('[data-vrcforge-subagent-section="active"] [data-vrcforge-subagent-thread-row]').count(), 3);
      assert.equal(await list.locator('[data-vrcforge-subagent-section="done"] [data-vrcforge-subagent-thread-row]').count(), 2);
      await list.getByText("Checking the last referenced asset", { exact: true }).waitFor();
      await list.getByText("1m 5s", { exact: true }).waitFor();
      await page.getByText("Original parent transcript", { exact: true }).waitFor();
      assert.equal(await composer.inputValue(), "Draft survives right sidebar navigation");
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
      await page.screenshot({ path: resolve(artifactDir, `list-${theme}.png`) });
      const detailPanel = await openThread(page);
      await detailPanel.waitFor();
      await detailPanel.getByText("Surface worker A", { exact: true }).first().waitFor();
      assert.equal(await sidebar.getByRole("heading", { name: "Sub agents" }).count(), 0);
      assert.equal(await list.isVisible(), false);
      await page.getByText("Original parent transcript", { exact: true }).waitFor();
      assert.equal(await composer.inputValue(), "Draft survives right sidebar navigation");
      if (theme === "light") await page.screenshot({ path: resolve(artifactDir, "detail-light.png") });
      await detailPanel.locator("[data-vrcforge-subagent-back]").click();
      await list.waitFor();
      await sidebar.locator("[data-vrcforge-close-subagent-sidebar]").click();
      await entrance.getByRole("button").waitFor();
      assert.equal(await composer.inputValue(), "Draft survives right sidebar navigation");
      await page.close();
    }
  }

  if (issue === "parent-activity") {
    detail = { ...done, parentTurnId: "delegating-turn", parentClientTurnId: "delegating-request" };
    tasks = [detail, { ...taskA, id: "other-turn", parentClientTurnId: "another-request" }];
    const page = await open();
    const composer = page.locator("[data-chat-composer-dock] textarea:not([disabled])").first();
    await composer.fill("Draft kept in the parent chat");
    const process = page.locator("[data-agent-turn-process-group]");
    await process.waitFor();
    assert.equal(await page.locator("[data-subagent-activity]").count(), 0);
    await process.getByRole("button").click();
    const row = page.locator('[data-subagent-status="completed"]');
    await row.getByText("Completed", { exact: true }).waitFor();
    assert.equal(await row.count(), 1);
    assert.match(await row.innerText(), /Surface worker A/);
    assert.equal(await page.locator("[data-agent-waiting-for-subagent]").count(), 0);
    await row.getByRole("button").click();
    const panel = page.locator("[data-vrcforge-subagent-workspace-surface]");
    await panel.getByText("Surface worker A", { exact: true }).first().waitFor();
    assert.equal(await composer.inputValue(), "Draft kept in the parent chat");
    await page.close();
  }

  if (issue === "handoff-catalog") {
    const page = await open(true, "vrcforge://chat/surface-A");
    await page.getByText("Surface A", { exact: true }).first().waitFor();
    const composer = page.locator("[data-chat-composer-dock] textarea:not([disabled])").first();
    await composer.fill("/handoff");
    await composer.press("Enter");
    await composer.press("Enter");
    const form = page.locator("[data-session-handoff-send][open] form");
    const target = form.locator("select");
    await target.waitFor();
    const values = await target.locator("option").evaluateAll(options => options.map(option => option.value));
    assert.ok(values.includes("handoff-1"), "existing first target is available");
    for (const id of ["surface-A", "surface-B", "surface-C"]) assert.ok(!values.includes(id), `out-of-scope or source chat ${id} is absent`);
    assert.ok(values.includes("handoff-33"), `33rd same-project target is missing from ${values.length - 1} choices`);
    assert.deepEqual(values.slice(1).sort(), handoffTargets.map(chat => chat.id).sort());
    await target.selectOption("handoff-33");
    await form.locator("textarea").fill("Continue the isolated handoff");
    await form.getByRole("button", { name: "Send", exact: true }).click();
    await form.getByText("Handoff sent.").waitFor();
    assert.deepEqual(handoffs.map(({ sourceChatId, targetChatId, payload }) => [sourceChatId, targetChatId, payload.goal]),
      [["surface-A", "handoff-33", "Continue the isolated handoff"]]);
    console.log("PASS handoff: all same-project targets selectable and one exact submission acknowledged");
  }

  if (issue === "external-chat-link") {
    const page = await open(true, "vrcforge://chat/surface-A");
    await page.getByText("Expand", { exact: true }).click();
    const bareLink = page.getByRole("link", { name: externalChatLink, exact: true });
    await bareLink.waitFor();
    const markdownLink = page.getByRole("link", { name: "原始会话", exact: true });
    assert.equal(await markdownLink.getAttribute("href"), externalChatLink);
    await assertTooltip(page, markdownLink, "另一个应用");
    assert.equal(await page.getByRole("link", { name: /查询参数|片段|新会话|自动化/ }).count(), 0);
    assert.equal(await page.getByRole("link", { name: "图片" }).getAttribute("href"), "https://example.com/image.png");
    await page.evaluate(url => window.subAgentEvent("deep-link://new-url", [url]), externalChatLink);
    assert.deepEqual(await page.evaluate(() => window.codexOpenUrls), []);
    await bareLink.click();
    await markdownLink.click();
    assert.deepEqual(await page.evaluate(() => window.codexOpenUrls), [externalChatLink, externalChatLink]);
    await page.locator('[data-vrcforge-chat-id="surface-A"][data-vrcforge-chat-active="true"]').waitFor();
    assert.deepEqual(posts, []);
    assert.deepEqual(handoffs, []);
    console.log("PASS external Codex chat links: no auto-open, only existing-chat UUID URLs reach the desktop opener on click");
  }

  if (issue === "chat-composer-link") {
    const page = await open(true, "vrcforge://chat/surface-A");
    const composer = page.locator("[data-chat-composer-dock] textarea").first();
    const raw = `Original draft: ${composerTargetLink} and more text.`;
    const rendered = `Original draft: ${composerTargetTitle} and more text.`;
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
    await composer.fill("Original draft: ");
    await composer.press("End");
    await page.evaluate(link => navigator.clipboard.writeText(link), composerTargetLink);
    await composer.press("Control+v");
    const draftLink = page.locator("[data-chat-composer-dock]").getByRole("link", { name: composerTargetTitle, exact: true });
    await draftLink.waitFor({ timeout: 3000 });
    assert.equal(await draftLink.getAttribute("href"), composerTargetLink);
    assert.equal(await composer.evaluate(node => node === document.activeElement), true);
    await composer.press("End");
    await page.keyboard.insertText(" and more text.");
    assert.equal(await composer.inputValue(), rendered);
    await composer.press("Control+a");
    await composer.press("Control+c");
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), raw);
    await composer.press("End");
    await draftLink.click();
    await page.getByText("Original project transcript", { exact: true }).waitFor();
    await page.locator('[data-vrcforge-chat-id="surface-A"]').click();
    await draftLink.waitFor();
    assert.equal(await composer.inputValue(), rendered);
    await composer.press("Enter");
    await page.waitForFunction(() => window.sentChatMessages.length > 0);
    assert.deepEqual(await page.evaluate(() => window.sentChatMessages), [raw]);
    const userLinkInHistory = page.locator("[data-user-message-bubble]").getByRole("link", { name: composerTargetTitle, exact: true });
    const assistantLink = page.getByText("Assistant mention:", { exact: false }).getByRole("link", { name: composerTargetTitle, exact: true });
    await userLinkInHistory.waitFor();
    await assistantLink.waitFor();
    assert.equal(await userLinkInHistory.getAttribute("href"), composerTargetLink);
    assert.equal(await assistantLink.getAttribute("href"), composerTargetLink);
    await assistantLink.click();
    await page.getByText("Original project transcript", { exact: true }).waitFor();
  }

  if (issue === "chat-links") {
    const page = await open(true, "vrcforge://chat/surface-A");
    await page.getByRole("link", { name: chatB.title, exact: true }).waitFor();
    assert.equal(await page.getByRole("link", { name: /command=write/ }).count(), 0);
    await page.locator('[data-vrcforge-chat-id="surface-A"]').click({ button: "right" });
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.getByRole("menuitem", { name: "Copy Chat Link" }).click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), "vrcforge://chat/surface-A");
    await page.getByRole("link", { name: chatB.title, exact: true }).click();
    await page.getByRole("link", { name: "Missing chat" }).click();
    await page.getByText("Chat not found.").waitFor();
    const projectLink = page.getByRole("link", { name: chatC.title });
    assert.equal(await projectLink.getAttribute("href"), "vrcforge://chat/surface-C");
    await assertTooltip(page, projectLink, "中文标题");
    await projectLink.click();
    await page.getByText("Other project transcript", { exact: true }).waitFor();
    await page.locator('[data-open-conversation="surface-A"]').click();
    await page.getByRole("link", { name: chatB.title, exact: true }).click();
    const processGroup = page.locator("[data-agent-turn-process-group] > div > button[aria-expanded]");
    if (await processGroup.first().getAttribute("aria-expanded") === "false") await processGroup.first().click();
    const toolGroup = page.locator("[data-agent-tool-call-group] > div > button[aria-expanded]");
    if (await toolGroup.first().getAttribute("aria-expanded") === "false") await toolGroup.first().click();
    await page.getByRole("button", { name: "Called unity_send_message" }).click();
    await page.locator('[data-open-conversation="surface-C"]').click();
    await page.getByText("Other project transcript", { exact: true }).waitFor();
    await page.evaluate(() => window.subAgentEvent("deep-link://new-url", ["vrcforge://chat/surface-A"]));
    await page.getByRole("link", { name: chatB.title, exact: true }).waitFor();
    assert.equal(posts.length, 0);
    console.log("PASS real app chat links: copy, Chinese prose and Markdown, cross-project navigation, missing chat, existing source and receipt buttons, cold and warm delivery");
  }

  if (issue === "all" || issue === "race") {
    const page = await open();
    const panel = await openThread(page);
    await panel.getByText("Running", { exact: true }).waitFor();
    holdNextList = true;
    await waitFor(() => pending.length === 1, "first list request should be pending");
    tasks = [done];
    detail = done;
    await panel.getByText("Completed", { exact: true }).waitFor();
    const lateReply = page.waitForResponse(async (response) => response.url().includes("/api/app/sub-agents")
      && (await response.json()).tasks?.some((task) => task.revision === 2));
    pending.shift()();
    await lateReply;
    await page.evaluate(() => new Promise((frame) => requestAnimationFrame(() => requestAnimationFrame(frame))));
    assert.equal(await panel.getByText("Completed", { exact: true }).count(), 1, "late running reply regressed detail");
    await page.locator("[data-vrcforge-subagent-back]").click();
    assert.equal(await page.locator(`[data-vrcforge-subagent-thread-row="${taskA.id}"]`).getByText("Completed", { exact: true }).count(), 1);
    await page.close();
  }

  if (issue === "all" || issue === "reconnect") {
    tasks = [];
    const page = await open(true);
    const signal = "vrcforge-backend-event-status";
    await page.waitForFunction((name) => window.subAgentHasListener(name), signal);
    await page.evaluate((name) => window.subAgentEvent(name, { status: "connected" }), signal);
    await waitFor(() => snapshots.at(-1)?.revisions.length === 0, "list starts empty");
    await page.evaluate((name) => window.subAgentEvent(name, { status: "disconnected" }), signal);
    tasks = [done];
    detail = done;
    await page.evaluate((name) => window.subAgentEvent(name, { status: "connected" }), signal);
    const panel = await openThread(page);
    await panel.getByText("Completed", { exact: true }).waitFor();
    await page.close();
  }

  if (issue === "all" || issue === "parent") {
    catalogOnly = true;
    tasks = [...Array.from({ length: 50 }, (_, index) => ({ ...taskB, id: `B-${index}` })), taskA];
    detail = taskA;
    const page = await open();
    await (await openList(page)).locator(`[data-vrcforge-open-subagent-thread="${taskA.id}"]`).waitFor();
    holdNextParent = chatA.id;
    await waitFor(() => pending.length === 1, "A refresh must be pending");
    await page.getByText("Surface B", { exact: true }).first().click();
    await (await openList(page)).getByText("Surface worker B", { exact: true }).first().waitFor();
    const lateA = page.waitForResponse((response) => new URL(response.url()).searchParams.get("parentChatId") === chatA.id);
    pending.shift()();
    await lateA;
    await page.evaluate(() => new Promise((frame) => requestAnimationFrame(() => requestAnimationFrame(frame))));
    assert.equal(await page.getByText("Surface worker B", { exact: true }).first().isVisible(), true);
    assert.equal(await page.getByText("Surface worker A", { exact: true }).count(), 0);
    await page.getByText("Surface A", { exact: true }).first().click();
    await (await openList(page)).locator(`[data-vrcforge-open-subagent-thread="${taskA.id}"]`).waitFor();
    await page.close();
    catalogOnly = false;
  }

  if (issue === "all" || issue === "manual" || issue === "manual-materials") {
    tasks = [done];
    detail = done;
    const page = await open();
    const composer = page.locator("[data-chat-composer-dock] textarea").first();
    const files = page.locator('[data-chat-composer-dock] input[type="file"]').first();
    await files.setInputFiles({ name: "manual-note.txt", mimeType: "text/plain", buffer: Buffer.from("manual material sentinel") });
    await page.getByText("manual-note.txt", { exact: true }).waitFor();
    const message = "/delegate Read what I attached.";
    await composer.fill(message);
    await composer.press("Enter");
    await waitFor(() => posts.length === 1, "a failed manual request must reach HTTP");
    if (issue === "manual-materials") {
      assert.deepEqual(posts[0].selectedMaterials, [{ name: "manual-note.txt", content: { text: "manual material sentinel" } }]);
    } else {
      assert.equal(await composer.inputValue(), message, "draft must stay after sending");
      await page.getByText("Couldn't start the subagent. Try sending the request again.", { exact: true }).first().waitFor();
      assert.equal(await page.getByText("Fixture rejected delegation", { exact: true }).count(), 0);
      assert.equal(await composer.inputValue(), message, "draft must stay after HTTP 400");
      await page.getByText("manual-note.txt", { exact: true }).waitFor();
      assert.deepEqual(posts[0].selectedMaterials, [{ name: "manual-note.txt", content: { text: "manual material sentinel" } }]);

      holdPost = true;
      postFailure = false;
      await composer.press("Enter");
      await waitFor(() => posts.length === 2 && pending.length === 1, "second POST must be in flight");
      assert.equal(await composer.inputValue(), message, "success must wait for acceptance");
      await page.getByText("Surface B", { exact: true }).first().click();
      await composer.fill("B's separate draft");
      pending.shift()();
      await waitFor(() => pending.length === 0, "fixture accepted POST");
      await page.getByText("Surface A", { exact: true }).first().click();
      await page.waitForFunction(() => document.querySelector("[data-chat-composer-dock] textarea")?.value === "");
      await page.getByText("Surface B", { exact: true }).first().click();
      assert.equal(await composer.inputValue(), "B's separate draft");
      await page.getByText("Surface A", { exact: true }).first().click();

      await files.setInputFiles({ name: "unknown.dat", mimeType: "application/octet-stream", buffer: Buffer.from("unsupported") });
      await page.getByText("unknown.dat", { exact: true }).waitFor();
      await composer.fill("/delegate Check this binary.");
      await composer.press("Enter");
      await page.getByText(/Manual delegation accepts readable text attachments/).first().waitFor();
      assert.equal(posts.length, 2);
      assert.equal(await composer.inputValue(), "/delegate Check this binary.");

    }
    await page.close();
  }
  if (issue === "all" || issue === "manual" || issue === "manual-error") {
    tasks = [done];
    detail = done;
    const page = await open();
    const panel = await openThread(page);
    await panel.getByRole("button", { name: "Merge into chat" }).click();
    await waitFor(() => merges.length === 1, "merge must reach HTTP");
    const sidebar = page.locator("[data-vrcforge-subagent-sidebar]");
    await sidebar.getByRole("alert").getByText("This action is unavailable for the task's current status. Reopen the task to see the latest state.", { exact: true }).waitFor();
    assert.equal(await sidebar.getByText("Fixture revision conflict", { exact: true }).count(), 0);
    await panel.getByText("Completed", { exact: true }).waitFor();
    await page.close();
  }
  if (issue === "all" || issue === "task-read-errors") {
    tasks = [done];
    detail = done;
    listFailure = true;
    const page = await open();
    await openList(page);
    const sidebar = page.locator("[data-vrcforge-subagent-sidebar]");
    await sidebar.getByRole("alert").getByText("Couldn't load subagents. Reopen the list to try again.", { exact: true }).waitFor();
    assert.equal(await sidebar.getByText("Fixture failed listing workers", { exact: true }).count(), 0);
    listFailure = false;
    await sidebar.locator("[data-vrcforge-close-subagent-sidebar]").click();
    await openList(page);
    missingDetail = true;
    await openThread(page);
    await sidebar.getByRole("alert").getByText("This task is unavailable. Return to the subagent list.", { exact: true }).waitFor();
    assert.equal(await sidebar.getByText("Fixture missing worker detail", { exact: true }).count(), 0);
    await page.close();
  }
  if (issue === "app-action-errors") {
    const page = await open();
    const composer = page.locator("[data-chat-composer-dock] textarea").first();
    const request = "/goal Review the draft";
    await composer.fill(request);
    await composer.press("Enter");
    await page.getByText("Couldn't create the goal. Send it again to retry.", { exact: true }).waitFor();
    assert.equal(await page.getByText("Fixture goal storage failure", { exact: true }).count(), 0);
    assert.equal(await composer.inputValue(), request);
    const files = page.locator('[data-chat-composer-dock] input[type="file"]').first();
    await files.setInputFiles({ name: "report.zip", mimeType: "application/zip", buffer: Buffer.from("fixture") });
    await page.getByText("report.zip", { exact: true }).first().waitFor();
    const attachmentError = "Couldn't save the file because its contents don't match a supported format. Add a valid file.";
    const localizedTrigger = page.locator(`[data-app-tooltip="${attachmentError}"]`).first();
    await assertTooltip(page, localizedTrigger, attachmentError);
    assert.equal(await page.locator('[data-app-tooltip*="Fixture attachment format rejected"]').count(), 0);
    await page.close();
  }
  console.log(`PASS App browser and fixture HTTP: ${issue}`);
} catch (error) {
  console.error(error.message);
  console.error("List requests:", JSON.stringify(snapshots));
  if (currentPage && !currentPage.isClosed()) {
    console.error((await currentPage.locator("body").innerText()).slice(0, 2000));
  }
  throw error;
} finally {
  for (const send of pending) send();
  await browser?.close();
  ui.closeAllConnections();
  await new Promise((done) => ui.close(done));
  backend.closeAllConnections();
  await new Promise((done) => backend.close(done));
  await rm(scratch, { recursive: true, force: true });
}
