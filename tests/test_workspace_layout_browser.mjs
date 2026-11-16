import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";
import loadTailwindConfig from "tailwindcss/loadConfig.js";

const root = resolve(import.meta.dirname, "..");
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
// Replay both original components with the same data and assertions, without a checkout.
const baseline = process.env.VRCFORGE_WORKSPACE_LAYOUT_BASELINE_REF;
const componentPaths = [
  "src/components/checkpoints/checkpoint-workspace.tsx",
  "src/components/doctor/doctor-workspace.tsx",
];
const baselineSources = new Map(baseline ? componentPaths.map((path) => [
  resolve(root, path),
  execFileSync("git", ["show", `${baseline}:${path}`], { cwd: root, encoding: "utf8" }),
]) : []);
const tailwindConfig = loadTailwindConfig(resolve(root, "tailwind.config.ts"));
const css = (await postcss([tailwindcss({
  ...tailwindConfig,
  content: [
    ...tailwindConfig.content.map((path) => resolve(root, path)),
    // Include old classes too, so a baseline replay uses its actual layout.
    ...[...baselineSources.values()].map((raw) => ({ raw, extension: "tsx" })),
  ],
})]).process(
  "@tailwind base; @tailwind components; @tailwind utilities;\n" + await readFile(resolve(root, "src/theme.css"), "utf8"),
  { from: undefined },
)).css;

const checkpoint = {
  id: "checkpoint_20261116_001",
  targetTool: "vrcforge_setup_outfit",
  projectRoot: "C:/Projects/Sample Avatar Workspace/Long Project Name",
  checkpointRef: "0123456789abcdef",
  status: "ready",
};
const message = "当前项目的 Unity Core 连接尚未就绪，请检查编辑器是否启动以及项目是否匹配。".repeat(3);
const bundle = await build({
  stdin: { resolveDir: root, loader: "tsx", contents: `
    import React from "react";
    import { createRoot } from "react-dom/client";
    import { initializeI18n } from "./src/i18n";
    import { CheckpointWorkspace } from "./src/components/checkpoints/checkpoint-workspace";
    import { DoctorWorkspace } from "./src/components/doctor/doctor-workspace";
    const noop = () => {};
    const checkpoint = ${JSON.stringify(checkpoint)};
    const paneStyle = { width: 654, height: 800, display: "flex" };
    void initializeI18n().then(() => createRoot(document.getElementById("root")).render(<>
      <section id="checkpoint" style={paneStyle}>
        <CheckpointWorkspace checkpoints={[checkpoint]} interruptedRecoveries={[]} adjustmentCheckpoints={[]}
          selectedProjectPath={checkpoint.projectRoot} preview={{ ok: true, checkpoint, changedFiles: [], workingTreeStatus: [] }}
          recoveryPreview={null} adjustmentPreview={null} loading={false} restoringId="" recoveryBusyId="" adjustmentBusyId=""
          message="" recoveryMessage="" adjustmentMessage="" onRefresh={noop} onPreview={noop} onRestore={noop} />
      </section>
      <section id="doctor" style={paneStyle}>
        <DoctorWorkspace report={null} loading={false} message={${JSON.stringify(message)}} messageTone="warn"
          fixingCheckId="" lastFixResult={null} chatRecoveries={[]} chatPersistenceBlocked={false}
          resolvingChatStorageConflict={false} exportingSupportBundle={false} onRefresh={noop} onFix={noop}
          onResolveChatConflict={noop} onOpenSettings={noop} onExportSupportBundle={noop} onCopy={noop} formatPayload={JSON.stringify} />
      </section>
    </>));
  ` },
  bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
  plugins: baseline ? [{
    name: "workspace-layout-baseline",
    setup(plugin) {
      plugin.onLoad({ filter: /(?:checkpoint|doctor)-workspace\.tsx$/ }, ({ path }) => ({
        loader: "tsx", contents: baselineSources.get(resolve(path)),
      }));
    },
  }] : [],
});

const browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
try {
  // The app allows a 1100px window and 440px left sidebar, leaving a 654px center.
  const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.setContent('<main id="root"></main>');
  await page.addStyleTag({ content: css });
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  await page.locator("[data-vrcforge-checkpoint-detail]").waitFor();

  const failures = [];
  for (const [name, check] of [
    ["checkpoint preview stays readable in the narrow center", async () => {
      const detail = page.locator("#checkpoint [data-vrcforge-checkpoint-detail]");
      const rows = [];
      for (const value of [checkpoint.id, checkpoint.targetTool, checkpoint.projectRoot]) {
        rows.push(await detail.getByText(value, { exact: true }).evaluate((element) => ({
          value: element.textContent,
          lines: element.getBoundingClientRect().height / parseFloat(getComputedStyle(element).lineHeight),
          overflow: element.scrollWidth - element.clientWidth,
        })));
      }
      console.log("checkpoint geometry:", JSON.stringify(rows));
      assert.ok(rows.every((row) => row.lines <= 3), "short checkpoint values must not become tall columns of wrapped characters");
      assert.ok(rows.every((row) => row.overflow <= 1), "checkpoint values must stay inside their detail card");
    }],
    ["doctor status stays inside the narrow center", async () => {
      const geometry = await page.locator("#doctor").getByText(message, { exact: true }).evaluate((element) => {
        const pane = element.closest("#doctor");
        const messageRect = element.getBoundingClientRect();
        const paneRect = pane.getBoundingClientRect();
        return {
          leftOverflow: paneRect.left - messageRect.left,
          rightOverflow: messageRect.right - paneRect.right,
          textOverflow: element.scrollWidth - element.clientWidth,
          paneOverflow: pane.scrollWidth - pane.clientWidth,
        };
      });
      console.log("doctor geometry:", JSON.stringify(geometry));
      assert.ok(Object.values(geometry).every((overflow) => overflow <= 1), "the full diagnostic message must wrap within the workspace without horizontal scrolling");
    }],
  ]) {
    try {
      await check();
      console.log(`PASS: ${name}`);
    } catch (error) {
      failures.push(`${name}: ${error.message}`);
      console.error(`FAIL: ${failures.at(-1)}`);
    }
  }
  assert.deepEqual(pageErrors, [], "workspace fixture must render without errors");
  assert.equal(failures.length, 0, failures.join("\n"));
} finally {
  await browser.close();
}
