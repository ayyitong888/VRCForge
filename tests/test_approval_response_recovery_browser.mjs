import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { build } from "esbuild";
import { chromium } from "playwright";

const replayPath = process.env.VRCFORGE_APPROVAL_RECEIPT_REPLAY;
const savedReceipt = replayPath ? JSON.parse(readFileSync(replayPath, "utf8")) : {
  id: "rollback-approval",
  projectRoot: "D:/TestProject",
  status: "applied",
  targetTool: "vrcforge_restore_checkpoint",
};
const receipt = Array.isArray(savedReceipt) ? savedReceipt[0] : savedReceipt;
assert.ok(receipt.id && receipt.projectRoot && receipt.status === "applied");
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React, { useState } from "react";
    import { createRoot } from "react-dom/client";
    import { initializeI18n, setLocale } from "./src/i18n";
    import { useApprovalExecution } from "./src/hooks/use-approval-execution";
    function Fixture() {
      const [error, setError] = useState("");
      const [notice, setRuntimeNotice] = useState("");
      const approval = window.approval;
      const noop = () => {};
      const execution = useApprovalExecution({
        endpoint: "http://127.0.0.1:8757",
        activeRuntimeProjectPath: approval.projectRoot,
        activeChatId: "chat",
        activeView: "chat",
        pendingApprovalItems: [{ ...approval, status: "pending" }],
        externalApprovalItems: window.mockExternal ? [approval] : [],
        setRuntimeNotice, setError,
        appendToChat: noop, chatIdForSessionId: () => "chat",
        refresh: async () => { window.refreshes.push("workspace"); },
        refreshRuntimeRuns: async () => { window.refreshes.push("runtime"); },
        loadCheckpoints: async () => { window.refreshes.push("checkpoints"); },
        reloadChatStorageState: async () => false,
      });
      return <>
        <button disabled={Boolean(execution.approvalActions[approval.id])}
          onClick={() => execution.approveShell(approval.id)}>批准</button>
        <output id="error">{error}</output>
        <output id="notice">{notice}</output>
      </>;
    }
    async function mount() {
      await initializeI18n();
      await setLocale("zh-CN");
      createRoot(document.getElementById("root")).render(<Fixture />);
    }
    void mount();
  ` },
  bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
});

const browser = await chromium.launch({
  headless: true,
  channel: process.env.VRCFORGE_BROWSER_CHANNEL || (process.platform === "win32" ? "msedge" : undefined),
});
try {
  for (const mode of ["applied", "pending", "other-approval", "lookup-unavailable", "global", "external"]) {
    const submitted = mode === "global" || mode === "external"
      ? { ...receipt, projectRoot: undefined } : receipt;
    const page = await browser.newPage();
    await page.goto("about:blank");
    await page.setContent('<div id="root"></div>');
    await page.evaluate(({ mode, receipt }) => {
      window.approval = receipt;
      window.mockExternal = mode === "external";
      window.refreshes = [];
      window.calls = [];
      Object.defineProperty(window, "__TAURI_INTERNALS__", { value: {
        invoke(command, args) {
          window.calls.push({ command, request: args.request });
          if (command === "approve_agent_approval") {
            return Promise.reject(`Network Error: http://127.0.0.1:8757/api/app/agent/approvals/${receipt.id}/approve: os error 10060`);
          }
          if (mode === "lookup-unavailable") return Promise.reject("os error 10060");
          const actualReceipt = { ...receipt };
          if (mode === "pending") actualReceipt.status = "pending";
          if (mode === "other-approval") actualReceipt.id = "different-approval";
          return Promise.resolve({ ok: true, approvals: [actualReceipt] });
        },
      } });
    }, { mode, receipt: submitted });
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.getByRole("button", { name: "批准" }).click();
    await page.waitForFunction(() => document.querySelector("#error").textContent || document.querySelector("#notice").textContent);
    const globalOnly = !submitted.projectRoot;
    const expectedCalls = [
      { command: "approve_agent_approval", request: { approvalId: submitted.id, expectedProjectRoot: submitted.projectRoot, globalOnly, timeoutMs: 180000 } },
    ];
    if (mode !== "external") {
      expectedCalls.push({ command: "fetch_agent_approvals", request: { projectRoot: submitted.projectRoot, globalOnly, timeoutMs: 30000 } });
    }
    assert.deepEqual(await page.evaluate(() => window.calls), expectedCalls,
      "reconciliation uses a scoped read where available and only sends one approval operation");
    const refreshes = await page.evaluate(() => window.refreshes);
    if (mode === "applied" || mode === "global") {
      assert.equal(await page.locator("#notice").textContent(), "已核对审批记录，这项操作已执行。");
      assert.equal(await page.locator("#error").textContent(), "");
      assert.deepEqual(refreshes, ["workspace", "runtime", "checkpoints"]);
    } else {
      assert.equal(await page.locator("#notice").textContent(), "");
      assert.equal(await page.locator("#error").textContent(), "暂时无法确认这项操作是否执行，请查看审批记录确认状态。");
      assert.deepEqual(refreshes, []);
    }
    await page.close();
  }
} finally {
  await browser.close();
}
console.log("Lost approval replies reconcile the exact receipt without repeating a write or exposing transport details.");
