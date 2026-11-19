import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { build } from "esbuild";
import postcss from "postcss";
import tailwind from "tailwindcss";

const fixturePath = process.env.VRCFORGE_TODO_ACTIVITY_REPLAY;
const replay = fixturePath ? JSON.parse(readFileSync(fixturePath, "utf8")) : undefined;
const run = replay?.timeline.run ?? {
  sessionId: "session", clientTurnId: "turn", turnId: "backend-turn", projectRoot: "C:/Project",
  status: "failed", startedAt: "2026-01-01T00:00:00Z", completedAt: "2026-01-01T00:01:00Z",
};
const items = replay
  ? replay.chat.items.at(-1).response.steps.findLast((step) => step.tool === "vrcforge_progress_replace").result.items
  : [
      { progressId: "active", title: "Prepare the project", status: "in_progress" },
      { progressId: "pending", title: "Review the project", status: "pending" },
    ].map((item) => ({
      ...item, sessionId: run.sessionId, projectRoot: run.projectRoot, updatedAt: "2026-01-01T00:00:10Z",
    }));
const chat = replay?.chat ?? {
  id: "chat", title: "Test project", projectPath: run.projectRoot, sessionId: run.sessionId,
  items: [
    { id: "user", type: "user", clientTurnId: run.clientTurnId, text: "Prepare and review the project." },
    { id: "response", type: "agent", response: { ...run, turnStatus: { status: run.status } } },
  ],
};
assert.equal(run.status, "failed");
assert.ok(run.completedAt);
assert.equal(items[0].status, "in_progress");

const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import { createRoot } from "react-dom/client";
    import { RightRuntimeSidebar } from "./src/components/runtime/runtime-sidebar";
    import { initializeI18n } from "./src/i18n";
    import { activeRuntimeTodoIds } from "./src/lib/runtime-todo-activity";

    const root = createRoot(document.getElementById("root"));
    const ready = initializeI18n();
    const noop = () => {};
    const items = ${JSON.stringify(items)};
    const chat = ${JSON.stringify(chat)};
    const terminalRun = ${JSON.stringify(run)};
    const activeRun = { ...terminalRun, status: "running", completedAt: undefined };
    const newerRun = {
      ...activeRun, clientTurnId: "new-turn", turnId: "new-backend-turn",
      startedAt: terminalRun.completedAt,
    };
    const liveChat = (run, state) => ({
      ...chat,
      items: [
        ...(run === newerRun ? chat.items : chat.items.slice(0, -1)),
        ...(run === newerRun ? [{
          id: "new-user", type: "user", text: "A later request.", clientTurnId: run.clientTurnId,
          createdAt: run.startedAt,
        }] : []),
        {
          id: "live", type: "streaming", text: "", clientTurnId: run.clientTurnId, sessionId: run.sessionId,
          turnId: run.turnId, createdAt: run.startedAt, stopRequested: state === "stopping",
          phase: state === "waiting-for-approval" ? "waiting_for_approval" : undefined,
        },
      ],
    });
    window.renderSidebar = async (projectWorkspace, state) => {
      await ready;
      const progress = state === "next-item" ? items.map((item, index) => ({
        ...item, status: index === 0 ? "completed" : index === 1 ? "running" : item.status,
      })) : items;
      const run = state === "failed" ? terminalRun : state === "unrelated-round" ? newerRun : activeRun;
      const currentChat = state === "failed" ? chat : liveChat(run, state);
      root.render(<RightRuntimeSidebar
        runtimeConnected loadingUnityStatus={false} workspaceProjectLabel="Test project"
        workspaceProjectType="unity" projectWorkspace={projectWorkspace}
        authorizationStatus={{ source: "none", status: "unknown" }}
        agentProgress={progress} activeTodoIds={activeRuntimeTodoIds(progress, currentChat, [run])}
        userAttachmentSources={[]} activeDesktopActions={[]} workspaceSummary={null}
        approvalsLoaded pendingApprovals={0} providerTestAttempted={false}
        refreshUnityStatus={noop} onHideSidebar={noop} onOpenPendingApprovals={noop}
        localizeHealthMessage={noop}
      />);
    };
  ` },
  bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
});
const css = (await postcss([tailwind("tailwind.config.ts")]).process(
  "@tailwind base; @tailwind components; @tailwind utilities;", { from: "src/styles.css" },
)).css;
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const browser = await chromium.launch({
  headless: true,
  channel: process.env.VRCFORGE_BROWSER_CHANNEL || (process.platform === "win32" ? "msedge" : undefined),
});
try {
  const page = await browser.newPage({ reducedMotion: "no-preference" });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("http://todo.test/", (route) => route.fulfill({
    contentType: "text/html",
    body: '<div id="root"></div>',
  }));
  await page.goto("http://todo.test/");
  await page.addStyleTag({ content: css });
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  const render = (projectWorkspace, state) =>
    page.evaluate(([project, state]) => window.renderSidebar(project, state), [projectWorkspace, state]);
  const rows = page.locator("[data-vrcforge-agent-todo-item]");
  const marker = rows.first().getByRole("img");
  const animationName = (index = 0) =>
    rows.nth(index).getByRole("img").evaluate((element) => getComputedStyle(element).animationName);
  const waitForAnimation = (expected, index = 0) => page.waitForFunction(([expected, index]) => {
    const marker = document.querySelectorAll("[data-vrcforge-agent-todo-item] [role=img]")[index];
    return marker && getComputedStyle(marker).animationName === expected;
  }, [expected, index], { timeout: 4000 });

  for (const projectWorkspace of [true, false]) {
    await render(projectWorkspace, "failed");
    await page.waitForFunction((project) =>
      document.querySelector("aside")?.hasAttribute("data-vrcforge-environment-status") === !project,
    projectWorkspace);
    await marker.waitFor({ timeout: 4000 });
    assert.equal(await rows.count(), items.length);
    assert.equal(await rows.first().getAttribute("data-vrcforge-agent-todo-status"), "in_progress");
    assert.equal(await rows.first().getByText(items[0].title, { exact: true }).count(), 1);
    assert.equal(await animationName(), "none", "an idle sidebar must not pulse a retained in-progress item");

    await render(projectWorkspace, "active");
    await waitForAnimation("pulse");
    assert.equal(await animationName(1), "none", "a pending item must stay still during an active turn");
    await page.emulateMedia({ reducedMotion: "reduce" });
    assert.equal(await animationName(), "none", "respect the reduced-motion preference");
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await waitForAnimation("pulse");

    await render(projectWorkspace, "unrelated-round");
    await waitForAnimation("none");
    assert.equal(await rows.first().getAttribute("data-vrcforge-agent-todo-status"), "in_progress");

    for (const paused of ["waiting-for-approval", "stopping"]) {
      await render(projectWorkspace, "active");
      await waitForAnimation("pulse");
      await render(projectWorkspace, paused);
      await waitForAnimation("none");
    }

    await render(projectWorkspace, "next-item");
    await waitForAnimation("pulse", 1);
    assert.equal(await animationName(), "none", "a completed item must stay still when work moves to the next item");

    await render(projectWorkspace, "failed");
    await waitForAnimation("none", 1);
    assert.equal(await animationName(), "none");
    assert.equal(await rows.first().getAttribute("data-vrcforge-agent-todo-status"), "in_progress");
  }
  assert.deepEqual(errors, []);
  console.log(`PASS: ${replay ? "original" : "synthetic"} TODOs pulse only while working, honoring reduced motion`);
} finally {
  await browser.close();
}
