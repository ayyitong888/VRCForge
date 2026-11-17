import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
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
const date = "2026-10-28T10:00:00Z";
const chatA = { id: "surface-A", sessionId: "surface-session-A", title: "Surface A", projectPath: "", projectType: "general", createdAt: date, updatedAt: date, revision: 0, items: [] };
const chatB = { ...chatA, id: "surface-B", sessionId: "surface-session-B", title: "Surface B" };
const chatC = { ...chatB, id: "surface-C", sessionId: "surface-session-C", title: "Surface C", projectPath: "E:/fixtures/other-project", projectType: "unity" };
const userLink = "vrcforge://chat/surface-B";
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
const fixtureChats = issue === "chat-links" ? [chatA, chatB, chatC] : [chatA, chatB];
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
  apiConfig: { provider: "custom", configured: true, base_url: "", model: "fixture", api_type: "auto" },
  permission: { pending: [] }, approvals: [],
};
let tasks = [taskA];
let detail = taskA;
let holdNextList = false;
let holdNextParent = "";
let holdPost = false;
let postFailure = true;
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
  if (url.pathname === "/api/app/sub-agents" && req.method === "GET") {
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
  if (url.pathname.startsWith("/api/app/sub-agents/")) return respond({ ok: true, task: detail });
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
  ...(issue === "chat-links" ? ["src/components/chat/chat-markdown.tsx", "src/components/sidebar/sidebar-menus.tsx", "src/hooks/use-chat-deep-links.ts"] : []),
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

  const open = async (desktop = false, startupUrl = "") => {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    currentPage = page;
    page.setDefaultTimeout(8000);
    page.on("pageerror", (error) => console.error("App fixture:", error.message));
    page.on("console", (message) => {
      if (message.type() === "error" && !message.text().startsWith("Failed to load resource")) console.error(message.text());
    });
    await page.addInitScript(({ desktop, base, startupUrl, linkProjectPath }) => {
      localStorage.setItem("vrcforge_onboarded", "true");
      localStorage.setItem("vrcforge-locale", "en-US");
      localStorage.setItem("vrcforge_startup_animation", "false");
      if (!desktop) return;
      const callbacks = new Map();
      const listeners = new Map();
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
          if (command === "fetch_project_prefs" && linkProjectPath) return { ok: true, customPaths: [linkProjectPath] };
          const request = args?.request || {};
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
          else return { ok: true, items: [], questions: [], runs: [], actions: [], settings: {} };
          return (await fetch(base + path)).json();
        },
      };
    }, { desktop, base, startupUrl, linkProjectPath: issue === "chat-links" ? chatC.projectPath : "" });
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

  const openThread = async (page) => {
    await page.locator(`[data-vrcforge-open-subagent-thread="${taskA.id}"]`).first().click();
    return page.locator("[data-vrcforge-subagent-workspace-surface]");
  };

  if (issue === "chat-links") {
    const page = await open(true, "vrcforge://chat/surface-A");
    await page.getByRole("link", { name: userLink, exact: true }).waitFor();
    assert.equal(await page.getByRole("link", { name: /command=write/ }).count(), 0);
    await page.locator('[data-vrcforge-chat-id="surface-A"]').click({ button: "right" });
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.getByRole("menuitem", { name: "Copy Chat Link" }).click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), "vrcforge://chat/surface-A");
    await page.getByRole("link", { name: userLink, exact: true }).click();
    await page.getByRole("link", { name: "Missing chat" }).click();
    await page.getByText("Chat not found.").waitFor();
    const projectLink = page.getByRole("link", { name: "另一个项目" });
    assert.equal(await projectLink.getAttribute("href"), "vrcforge://chat/surface-C");
    assert.equal(await projectLink.getAttribute("title"), "中文标题");
    await projectLink.click();
    await page.getByText("Other project transcript", { exact: true }).waitFor();
    await page.locator('[data-open-conversation="surface-A"]').click();
    await page.getByRole("link", { name: userLink, exact: true }).click();
    const processGroup = page.locator("[data-agent-turn-process-group] > div > button[aria-expanded]");
    if (await processGroup.first().getAttribute("aria-expanded") === "false") await processGroup.first().click();
    const toolGroup = page.locator("[data-agent-tool-call-group] > div > button[aria-expanded]");
    if (await toolGroup.first().getAttribute("aria-expanded") === "false") await toolGroup.first().click();
    await page.getByRole("button", { name: "Called unity_send_message" }).click();
    await page.locator('[data-open-conversation="surface-C"]').click();
    await page.getByText("Other project transcript", { exact: true }).waitFor();
    await page.evaluate(() => window.subAgentEvent("deep-link://new-url", ["vrcforge://chat/surface-A"]));
    await page.getByRole("link", { name: userLink, exact: true }).waitFor();
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
    await page.locator(`[data-vrcforge-open-subagent-thread="${taskA.id}"]`).first().waitFor();
    holdNextParent = chatA.id;
    await waitFor(() => pending.length === 1, "A refresh must be pending");
    await page.getByText("Surface B", { exact: true }).first().click();
    await page.getByText("Surface worker B", { exact: true }).first().waitFor();
    const lateA = page.waitForResponse((response) => new URL(response.url()).searchParams.get("parentChatId") === chatA.id);
    pending.shift()();
    await lateA;
    await page.evaluate(() => new Promise((frame) => requestAnimationFrame(() => requestAnimationFrame(frame))));
    assert.equal(await page.getByText("Surface worker B", { exact: true }).first().isVisible(), true);
    assert.equal(await page.getByText("Surface worker A", { exact: true }).count(), 0);
    await page.getByText("Surface A", { exact: true }).first().click();
    await page.locator(`[data-vrcforge-open-subagent-thread="${taskA.id}"]`).first().waitFor();
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
      await page.getByText("Fixture rejected delegation", { exact: true }).first().waitFor();
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
    await sidebar.getByRole("alert").getByText("Fixture revision conflict", { exact: true }).waitFor();
    await panel.getByText("Completed", { exact: true }).waitFor();
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
