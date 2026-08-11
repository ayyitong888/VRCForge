import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relativePath) => readFile(path.join(root, relativePath), "utf8");
const presentationPath = path.join(root, "src/lib/approval-presentation.ts");
const presentationSource = await readFile(presentationPath, "utf8");
const presentationOutput = ts.transpileModule(presentationSource, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 },
  fileName: presentationPath,
}).outputText;
const { presentApproval } = await import(
  `data:text/javascript;base64,${Buffer.from(presentationOutput).toString("base64")}`
);

const translate = (key, options) =>
  options ? `${key}:${Object.values(options).join("|")}` : key;
const multi = presentApproval(
  { id: "approval-multi", status: "pending", targetTool: "vrcforge_capture_multi_screenshot" },
  translate,
);
const single = presentApproval(
  { id: "approval-single", status: "pending", targetTool: "vrcforge_capture_screenshot" },
  translate,
);

assert.equal(multi.title, "approval.presentation.captureMultiTitle");
assert.equal(multi.summary, "approval.presentation.captureMultiSummary");
assert.equal(multi.notificationSummary, "approval.presentation.captureMultiNotificationSummary");
assert.equal(single.title, "approval.presentation.captureSingleTitle");
assert.equal(single.summary, "approval.presentation.captureSingleSummary");
assert.equal(single.notificationSummary, "approval.presentation.captureSingleNotificationSummary");

const secretMarker = "SENSITIVE_INPUT_SENTINEL";
const sensitiveCases = [
  {
    expected: "approval.presentation.createObjectNotificationSummary",
    approval: {
      id: "approval-create",
      status: "pending",
      targetTool: "vrcforge_create_gameobject",
      arguments: { name: secretMarker, parentPath: `/Hierarchy/${secretMarker}` },
      reason: secretMarker,
    },
  },
  {
    expected: "approval.presentation.restoreNotificationSummary",
    approval: {
      id: "approval-restore",
      status: "pending",
      targetTool: "vrcforge_restore_checkpoint",
      arguments: { checkpointId: secretMarker },
    },
  },
  {
    expected: "approval.presentation.commandNotificationSummary",
    approval: {
      id: "approval-command",
      status: "pending",
      preview: { command: `run ${secretMarker}`, cwd: `C:/private/${secretMarker}` },
    },
  },
  {
    expected: "approval.presentation.genericNotificationSummary",
    approval: {
      id: "approval-generic",
      status: "pending",
      targetTool: "vrcforge_unknown_write",
      projectRoot: `C:/private/${secretMarker}`,
      taskContext: { objective: secretMarker },
    },
  },
];

for (const { approval, expected } of sensitiveCases) {
  const presentation = presentApproval(approval, translate);
  assert.equal(presentation.notificationSummary, expected);
  assert.equal(presentation.notificationSummary.includes(secretMarker), false);
}

const app = await read("src/App.tsx");
assert.match(
  app,
  /notificationBody",\s*\{\s*summary:\s*presentation\.notificationSummary\s*\}/,
  "the native approval notification must include the bounded user-facing action summary",
);
assert.doesNotMatch(
  app,
  /notificationBody",\s*\{[^}]*technicalDetails/,
  "native notifications must not expose raw approval details",
);

for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP"]) {
  const messages = JSON.parse(await read(`src/locales/${locale}.json`));
  assert.match(messages.approval.notificationBody, /\{\{summary\}\}/);
  assert.ok(messages.approval.presentation.captureMultiTitle);
  assert.ok(messages.approval.presentation.captureMultiSummary);
  assert.ok(messages.approval.presentation.captureSingleTitle);
  assert.ok(messages.approval.presentation.captureSingleSummary);
}

console.log("approval notification summary UI contract: ok");
