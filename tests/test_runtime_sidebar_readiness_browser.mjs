import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React from "react";
    import { createRoot } from "react-dom/client";
    import { RightRuntimeSidebar } from "./src/components/runtime/runtime-sidebar";
    import { initializeI18n } from "./src/i18n";
    const root = createRoot(document.getElementById("root"));
    const ready = initializeI18n();
    window.showSidebar = async (state, pendingApprovals = 0) => {
      await ready;
      const bridge = state === "offline" ? "warning" : state === "checking" ? "unknown" : "ok";
      const tools = state === "ready" ? "ok" : state === "checking" ? "unknown" : "warning";
      root.render(<RightRuntimeSidebar
        runtimeConnected loadingUnityStatus={state === "checking"} workspaceProjectLabel="VRCForge"
        workspaceProjectType="unity" projectWorkspace
        selectedProjectComponent={{status:"ok"}} backendComponent={{status:"ok"}} mcpPackageComponent={{status:"ok"}}
        unityBridgeComponent={{status:bridge}} unityInstanceComponent={{status:bridge}}
        unityToolsComponent={{status:tools, detail:state === "checking" ? undefined : {readiness:{
          ready:state === "ready", blockerCode:state === "offline" ? "unity_core_connection_failed" : "unknown",
        }}}}
        authorizationStatus={{source:"none",status:"unknown"}} agentProgress={[]}
        subAgentTaskCount={0} subAgentRunningTaskCount={0} subAgentCompletedTaskCount={0}
        userAttachmentSources={[]} approvalsLoaded pendingApprovals={pendingApprovals} workspaceSummary={null}
        activeDesktopActions={[]} refreshUnityStatus={() => {}} providerTestAttempted={false}
        onHideSidebar={() => {}} onOpenPendingApprovals={() => window.approvalsOpened = true}
        localizeHealthMessage={() => ""} />);
    };
  ` },
  bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
});
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const server = createServer((_, response) => {
  response.setHeader("Content-Type", "text/html; charset=utf-8");
  response.end('<main id="root"></main>');
});
let browser;
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage({ viewport: { width: 300, height: 600 } });
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  const show = (state, approvals) => page.evaluate(([state, approvals]) => window.showSidebar(state, approvals), [state, approvals]);
  await show("blocked", 1);
  const blocked = page.locator('[data-unity-readiness="blocked"]');
  await blocked.waitFor({ timeout: 4000 });
  assert.match(await blocked.innerText(), /Unity is connected.*Avatar tools are unavailable/);
  assert.match(await blocked.innerText(), /Check the selected project and Unity Console/);
  assert.equal(await blocked.locator("details").evaluate((element) => element.open), false);
  const details = page.getByRole("button", { name: "Environment details" });
  assert.equal(await details.getAttribute("aria-expanded"), "false");
  assert.equal(await page.locator("[data-vrcforge-project-environment]").count(), 0);
  const approval = page.locator('[data-vrcforge-status="approval"]');
  await approval.click();
  assert.equal(await page.evaluate(() => window.approvalsOpened), true);
  assert.equal(await details.getAttribute("aria-expanded"), "false");
  await details.click();
  await page.locator("[data-vrcforge-project-environment]").waitFor();
  await show("ready", 0);
  await page.locator('[data-unity-readiness="ready"]').waitFor();
  assert.equal(await approval.count(), 0);
  await show("offline", 0);
  const unavailable = page.locator('[data-unity-readiness="unavailable"]');
  await unavailable.waitFor();
  assert.match(await unavailable.innerText(), /Not connected to Unity yet/);
  await show("checking", 0);
  await page.locator('[data-unity-readiness="checking"]').waitFor();
  assert.equal(await page.getByRole("alert").count(), 0);
  console.log("Unity readiness, environment disclosure and pending approval priority passed");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
