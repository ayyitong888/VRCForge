import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const root = process.cwd();
const temp = await fs.mkdtemp(path.join(root, "tests", ".approval-auto-hide-"));
const bundle = path.join(temp, "bundle.mjs");
const entry = `
import React from "react";
import { createRoot } from "react-dom/client";
import { ScopedPendingApprovalCard } from ${JSON.stringify(path.join(root, "src/components/approvals/scoped-pending-approval-card.tsx"))};
import { PendingApprovalsStrip } from ${JSON.stringify(path.join(root, "src/components/approvals/pending-approvals-strip.tsx"))};
const approval = { id: "approval-1", status: "pending", taskContext: { approvalRevisionUsed: false } };
function Fixture() {
  return React.createElement("main", null,
    React.createElement("section", { "data-scoped": true }, React.createElement(ScopedPendingApprovalCard, {
      approvals: [approval], actions: {}, disabled: false, onApprove: () => {}, onReject: () => {}, onModifyApproval: () => {},
    })),
    React.createElement("section", { "data-strip": true }, React.createElement(PendingApprovalsStrip, {
      approvals: [approval], actions: {}, loading: false, onApprove: () => {}, onReject: () => {},
    })),
  );
}
createRoot(document.getElementById("root")).render(React.createElement(Fixture));
`;
await build({
  stdin: { contents: entry, resolveDir: root, sourcefile: "approval-auto-hide-entry.mjs" },
  outfile: bundle, bundle: true, format: "iife", platform: "browser",
  plugins: [{ name: "approval-auto-hide-mocks", setup(plugin) {
    plugin.onResolve({ filter: /^react-i18next$/ }, () => ({ path: "translation", namespace: "approval" }));
    plugin.onResolve({ filter: /^lucide-react$/ }, () => ({ path: "icons", namespace: "approval" }));
    plugin.onResolve({ filter: /approval-presentation$/ }, () => ({ path: "presentation", namespace: "approval" }));
    plugin.onResolve({ filter: /ui\/(button|badge|data-line)$/ }, (args) => ({ path: args.path.split("/").at(-1), namespace: "approval" }));
    plugin.onResolve({ filter: /approval-(allow-split-button|revision-editor)$/ }, (args) => ({ path: args.path.split("/").at(-1), namespace: "approval" }));
    plugin.onLoad({ filter: /.*/, namespace: "approval" }, (args) => ({
      contents: {
        translation: 'export const useTranslation=()=>({t:(key,fallback)=>({"approval.blockingTitle":"Approval","approval.blockingDescription":"Review this request","approval.contextDetails":"Details","approval.presentation.project":"Project","approval.presentation.rollback":"Rollback","approval.reject":"Reject","approval.modify":"Modify","approval.modifyCancel":"Cancel edit","approval.modifyConfirm":"Submit","approval.revisionCategoryLabel":"Category","approval.revisionReasonPrompt":"Reason","approval.revisionReasonPlaceholder":"Explain"}[key]||fallback||key)});',
        icons: 'export const ChevronDown=()=>null; export const Pencil=()=>null; export const X=()=>null; export const AlertTriangle=()=>null;',
        presentation: 'export const presentApproval=()=>({title:"Pending approval",summary:"Review",project:"Project",rollback:"Rollback",technicalDetails:{}});',
        button: 'import React from "react"; export const Button=({children,...props})=>React.createElement("button",props,children);',
        badge: 'import React from "react"; export const Badge=({children,...props})=>React.createElement("span",props,children);',
        "data-line": 'import React from "react"; export const DataLine=({label,value})=>React.createElement("div",null,label,value);',
        "approval-allow-split-button": 'import React from "react"; export const ApprovalAllowSplitButton=({disabled,onApprove,approvalId})=>React.createElement("button",{disabled,onClick:()=>onApprove(approvalId)},"Approve");',
        "approval-revision-editor": 'import React,{useState} from "react"; export function ApprovalRevisionEditor({onCancel,onSubmit}){const [reason,setReason]=useState(""); return React.createElement("div",null,React.createElement("textarea",{value:reason,onChange:e=>setReason(e.target.value),"data-draft":true}),React.createElement("button",{onClick:onCancel},"Cancel"),React.createElement("button",{onClick:()=>onSubmit(reason,"other")},"Submit"));}',
      }[args.path], loader: "js", resolveDir: root,
    }));
  } }],
});
const script = await fs.readFile(bundle, "utf8");
const server = createServer((_request, response) => {
  response.writeHead(200, { "content-type": "text/html" });
  response.end(`<div id="root"></div><script>${script}</script>`);
});
server.listen(0, "127.0.0.1");
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  await page.clock.install();
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const scoped = page.locator("[data-scoped]");
  const strip = page.locator("[data-strip]");
  await scoped.getByRole("button", { name: "Modify" }).click();
  const draft = scoped.locator("[data-draft]");
  await draft.fill("keep this approval draft");
  await page.clock.runFor(30_000);
  assert.equal(await scoped.locator("[data-approval-reopen]").count(), 0, "editing pauses auto-hide for the scoped approval");
  assert.equal(await draft.inputValue(), "keep this approval draft", "editing preserves the draft while the timer runs");
  assert.equal(await strip.locator("[data-approval-reopen]").count(), 1, "idle approval strip collapses after 30 seconds");
  await scoped.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.clock.runFor(30_000);
  assert.equal(await scoped.locator("[data-approval-reopen]").count(), 1, "scoped approval collapses after editing ends and the idle interval elapses");
  await strip.locator("[data-approval-reopen]").click();
  assert.equal(await strip.getByRole("button", { name: "Approve" }).count(), 1, "collapsed approval strip can be reopened");
  console.log("pending approval auto-hide browser regression: 30-second collapse, edit pause/draft retention, and reopen passed");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
  await fs.rm(temp, { recursive: true, force: true });
}
