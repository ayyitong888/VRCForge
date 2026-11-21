import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { build } from "esbuild";
import postcss from "postcss";
import tailwind from "tailwindcss";

const app = readFileSync("src/App.tsx", "utf8");
const todoLoopStart = app.indexOf("  for (const todo of agentProgress) {");
const todoLoop = app.slice(todoLoopStart, app.indexOf("  const pendingRuntimeItems =", todoLoopStart));
assert.ok(todoLoop.includes("addPendingItem"), "replay the actual inbox callback from App");

const replay = process.env.VRCFORGE_PENDING_TODO_REPLAY;
const firstTodos = replay ? JSON.parse(readFileSync(replay, "utf8")) : Array.from({ length: 8 }, (_, index) => ({
  progressId: `todo-${index + 1}`, title: `Review project step ${index + 1}`, status: index ? "pending" : "completed",
}));
const target = firstTodos.at(-1);
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import { useState } from "react";
    import { createRoot } from "react-dom/client";
    import { RightRuntimeSidebar } from "./src/components/runtime/runtime-sidebar";
    import { runtimeProgressOwner } from "./src/lib/runtime-scope";
    import { initializeI18n } from "./src/i18n";

    const noop = () => {};
    const activityOwners = ["a", "b"].map(id => ({ id, sessionId: id + "-session", title: "Fixture " + id, projectPath: "C:/Fixture" }));
    const firstTodos = ${JSON.stringify(firstTodos)}.map(todo => ({ ...todo, sessionId: "a-session" }));
    const secondTodo = { ...firstTodos.at(-1), sessionId: "b-session", title: "Todo in the other conversation" };
    const agentProgress = [...firstTodos, secondTodo];
    function Fixture() {
      const [active, setActive] = useState("a");
      const [visibleTodos, setVisibleTodos] = useState(firstTodos);
      const [projectWorkspace, setProjectWorkspace] = useState(false);
      const [todoReveal, setTodoReveal] = useState(null);
      const openRuntimeChat = owner => setActive(owner.id);
      const pendingItems = [];
      const addPendingItem = (chat, item) => pendingItems.push({ ...item, chatTitle: chat.title });
      window.showTargetTodos = () => setVisibleTodos([secondTodo]);
      window.showProjectWorkspace = () => setProjectWorkspace(true);
      ${todoLoop}
      return <><output id="conversation">{active}</output><div className="h-[360px] w-80">
        <RightRuntimeSidebar runtimeConnected loadingUnityStatus={false} workspaceProjectLabel="Fixture"
          workspaceProjectType="unity" projectWorkspace={projectWorkspace}
          authorizationStatus={{ source:"none", status:"unknown" }}
          agentProgress={visibleTodos} pendingItems={pendingItems} todoReveal={todoReveal?.chatId === active ? todoReveal : undefined}
          onOpenTodo={todo => openRuntimeChat(runtimeProgressOwner(todo, activityOwners))}
          userAttachmentSources={[]} workspaceSummary={null} activeDesktopActions={[]} approvalsLoaded pendingApprovals={0}
          providerTestAttempted={false} refreshUnityStatus={noop} onHideSidebar={noop}
          onOpenPendingApprovals={noop} localizeHealthMessage={noop} />
      </div></>;
    }
    void initializeI18n().then(() => createRoot(document.getElementById("root")).render(<Fixture />));
  ` }, bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
});

const css = (await postcss([tailwind("tailwind.config.ts")]).process(
  "@tailwind base; @tailwind components; @tailwind utilities;", { from: "src/styles.css" },
)).css;
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const browser = await chromium.launch({
  headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || (process.platform === "win32" ? "msedge" : undefined),
});
try {
  const page = await browser.newPage();
  await page.route("http://todo.test/", route => route.fulfill({ contentType: "text/html", body: '<main id="root"></main>' }));
  await page.goto("http://todo.test/");
  await page.addStyleTag({ content: css });
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  const pending = page.locator("[data-runtime-pending-list]");
  const entries = pending.locator("button");
  const rows = page.locator("[data-vrcforge-agent-todo-item]");
  const targetRow = rows.filter({ hasText: target.title });
  const targetButton = targetRow.getByRole("button");
  const pendingTarget = pending.getByRole("button", { name: `${target.title} Todo · Fixture a` });
  await pendingTarget.click({ timeout: 4000 });
  assert.equal(await page.locator("#conversation").textContent(), "a");
  assert.equal(await targetButton.evaluate(button => button === document.activeElement), true,
    "clicking a pending todo in an already open chat focuses its exact row");
  assert.equal(await targetButton.evaluate(button => {
    const outer = button.closest("aside").getBoundingClientRect();
    const inner = button.closest(".app-scrollbar").getBoundingClientRect();
    const rect = button.getBoundingClientRect();
    return rect.top >= Math.max(outer.top, inner.top) && rect.bottom <= Math.min(outer.bottom, inner.bottom) + 1;
  }), true, "focus reveals the item inside the nested scroll panes");
  assert.equal(await entries.count(), firstTodos.filter(todo => todo.status !== "completed").length + 1,
    "opening a pending todo leaves its task status unchanged");
  assert.equal(await targetButton.getAttribute("data-app-tooltip"), null);
  assert.equal(await targetButton.locator("..").locator('[role="img"]').getAttribute("data-app-tooltip"), null);
  await targetButton.hover();
  assert.equal(await targetButton.evaluate(button => getComputedStyle(button).textDecorationLine), "none");
  assert.equal(await rows.first().getByRole("button").evaluate(button => getComputedStyle(button).textDecorationLine), "line-through");

  for (const embedded of [false, true]) {
    if (embedded) await page.evaluate(() => window.showProjectWorkspace());
    const header = page.getByRole("button", { name: "Todo", exact: true });
    await header.click();
    assert.equal(await header.getAttribute("aria-expanded"), "false");
    assert.equal(await targetRow.count(), 0);
    await pendingTarget.click();
    assert.equal(await header.getAttribute("aria-expanded"), "true");
    assert.equal(await targetButton.evaluate(button => button === document.activeElement), true);
  }

  await pending.getByRole("button", { name: /Todo in the other conversation/ }).click();
  assert.equal(await page.locator("#conversation").textContent(), "b");
  assert.equal(await targetButton.evaluate(button => button === document.activeElement), false,
    "an old todo from another session with the same ID must not receive focus");
  await page.evaluate(() => window.showTargetTodos());
  const otherTodoButton = rows.getByRole("button", { name: "Todo in the other conversation" });
  await page.waitForFunction(() => document.activeElement?.textContent?.trim() === "Todo in the other conversation");
  assert.equal(await otherTodoButton.count(), 1);
  console.log(`PASS: ${replay ? "captured" : "synthetic"} pending todo opens its exact row in both panels`);
} finally {
  await browser.close();
}
