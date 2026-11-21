import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { build } from "esbuild";
import postcss from "postcss";
import tailwind from "tailwindcss";

const app = readFileSync("src/App.tsx", "utf8");
const start = app.indexOf("  const pendingItemsByChat =");
const projection = app.slice(start, app.indexOf("  const selectedForeground =", start));
assert.ok(projection.includes("const pendingRuntimeItems"), "use the app's actual inbox projection");
const replay = process.env.VRCFORGE_PENDING_TODO_REPLAY;
const todos = replay ? JSON.parse(readFileSync(replay, "utf8")) : [
  { progressId: "done", status: "completed", title: "Finished project task" },
  { progressId: "todo", status: "in_progress", title: "Agent project task" },
];

const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import { useState } from "react";
    import { createRoot } from "react-dom/client";
    import { useTranslation } from "react-i18next";
    import { RightRuntimeSidebar } from "./src/components/runtime/runtime-sidebar";
    import { pendingApprovalOwner, pendingQuestionReviewTargets, runtimeProgressOwner } from "./src/lib/runtime-scope";
    import { initializeI18n } from "./src/i18n";
    const noop = () => {};
    const projectRoot = "C:/Fixture";
    const activityOwners = ["a", "b"].map(id => ({ id, sessionId: id + "-session", title: "Fixture " + id, projectPath: projectRoot, items: [] }));
    const agentProgress = ${JSON.stringify(todos)}.map(todo => ({ ...todo, sessionId: "a-session", projectRoot }));
    const approval = { id: "approve", status: "pending", title: "Approve the change", projectRoot, taskContext: { sessionId: "a-session" } };
    const question = { questionId: "answer", status: "pending", question: "Which option?", projectRoot, sessionId: "b-session" };
    function Fixture() {
      const { t } = useTranslation();
      const [active, setActive] = useState("a");
      const [opened, setOpened] = useState("");
      const [handled, setHandled] = useState(false);
      const [projectWorkspace, setProjectWorkspace] = useState(false);
      const questionReview = pendingQuestionReviewTargets(activityOwners, handled ? [{ ...question, status: "answered" }] : [question], active);
      const agentApprovals = handled ? [{ ...approval, status: "applied" }] : [approval, { ...approval, id: "auto", status: "applied" }];
      const openRuntimeChat = owner => setActive(owner.id);
      const openQuestionReview = (chat, id) => { openRuntimeChat(chat); setOpened(id); };
      const openPendingApprovalOwner = entry => { openRuntimeChat(pendingApprovalOwner(entry, activityOwners)); setOpened(entry.id); };
      const presentApproval = entry => ({ title: entry.title });
      window.finishDecisions = () => setHandled(true);
      window.showProjectWorkspace = () => setProjectWorkspace(true);
      ${projection}
      return <><output id="opened">{active}:{opened}</output><main className="h-[480px] w-80">
        <RightRuntimeSidebar workspaceProjectLabel="Fixture" workspaceProjectType="unity" projectWorkspace={projectWorkspace}
          runtimeConnected authorizationStatus={{ source:"none", status:"unknown" }}
          agentProgress={agentProgress} pendingItems={pendingRuntimeItems}
          onOpenTodo={todo => openRuntimeChat(runtimeProgressOwner(todo, activityOwners))}
          userAttachmentSources={[]} workspaceSummary={null} activeDesktopActions={[]} approvalsLoaded
          pendingApprovals={0} providerTestAttempted={false} refreshUnityStatus={noop} onHideSidebar={noop}
          onOpenPendingApprovals={noop} localizeHealthMessage={noop} />
      </main></>;
    }
    void initializeI18n().then(() => createRoot(document.getElementById("root")).render(<Fixture />));
  ` }, bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
});

const css = await postcss([tailwind("tailwind.config.ts")]).process("@tailwind base; @tailwind utilities;", { from: undefined });
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
try {
  const page = await browser.newPage({ locale: "en-US" });
  await page.route("http://inbox.test/", route => route.fulfill({ contentType: "text/html", body: '<div id="root"></div>' }));
  await page.goto("http://inbox.test/");
  await page.addStyleTag({ content: css.css });
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  const inbox = page.locator("[data-runtime-pending-list]");
  const actions = inbox.locator("[data-runtime-pending-kind]");
  await actions.first().waitFor();
  assert.deepEqual(await actions.evaluateAll(rows => rows.map(row => row.dataset.runtimePendingKind)), ["question", "approval"]);
  await inbox.locator('[data-runtime-pending-kind="question"]').click();
  assert.equal(await page.locator("#opened").textContent(), "b:answer");
  await inbox.locator('[data-runtime-pending-kind="approval"]').click();
  assert.equal(await page.locator("#opened").textContent(), "a:approve");
  await page.evaluate(() => window.finishDecisions());
  await inbox.waitFor({ state: "detached" });
  const rows = page.locator("[data-vrcforge-agent-todo-item]");
  assert.equal(await rows.count(), todos.length);
  const button = rows.last().locator("button");
  assert.equal(await button.getAttribute("data-app-tooltip"), null);
  assert.equal(await rows.last().locator('[role="img"]').getAttribute("data-app-tooltip"), null);
  await button.hover();
  assert.equal(await button.evaluate(element => getComputedStyle(element).textDecorationLine), "none");
  assert.equal(await rows.first().locator("button").evaluate(element => getComputedStyle(element).textDecorationLine), "line-through");
  for (const embedded of [false, true]) {
    if (embedded) await page.evaluate(() => window.showProjectWorkspace());
    const header = page.getByRole("button", { name: "Todo", exact: true });
    await header.click();
    assert.equal(await header.getAttribute("aria-expanded"), "false");
    assert.equal(await rows.count(), 0);
    await header.click();
    assert.equal(await rows.count(), todos.length);
  }
  console.log(`PASS: ${replay ? "captured" : "synthetic"} todos remain separate from questions and approvals`);
} finally {
  await browser.close();
}
