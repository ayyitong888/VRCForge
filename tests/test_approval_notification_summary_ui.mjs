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

const checkpointQuote = "本轮上述修改不用检查点，继续并保存";
const skippedCheckpoint = presentApproval(
  {
    id: "approval-checkpoint-skipped",
    status: "pending",
    targetTool: "vrcforge_write_file",
    taskContext: {
      checkpointUserPolicy: {
        schema: "vrcforge.user_checkpoint_policy.v1",
        status: "skipped_by_user",
        source: "user_message",
        sourceQuote: checkpointQuote,
      },
    },
  },
  translate,
);
assert.equal(
  skippedCheckpoint.rollback,
  `approval.presentation.rollbackSkippedByUser:${checkpointQuote}`,
  "the approval rollback row must show the explicit no-checkpoint warning and source quote",
);
const externalSkippedCheckpoint = presentApproval(
  {
    id: "approval-external-checkpoint-skipped",
    status: "pending",
    targetTool: "external_mcp_write",
    taskContext: {
      checkpointUserPolicy: {
        schema: "vrcforge.user_checkpoint_policy.v1",
        status: "skipped_by_user",
        source: "external_user_instruction",
        sourceQuote: "Skip this MCP checkpoint for this approved action",
      },
    },
  },
  translate,
);
assert.equal(
  externalSkippedCheckpoint.rollback,
  "approval.presentation.rollbackSkippedByUser:Skip this MCP checkpoint for this approved action",
);
const ordinaryApproval = presentApproval(
  { id: "approval-normal", status: "pending", targetTool: "vrcforge_write_file" },
  translate,
);
assert.equal(ordinaryApproval.rollback, "approval.presentation.rollbackDepends");
const invalidCheckpointPolicy = presentApproval(
  {
    id: "approval-invalid-checkpoint-policy",
    status: "pending",
    targetTool: "vrcforge_write_file",
    taskContext: { checkpointUserPolicy: { schema: "other", sourceQuote: checkpointQuote } },
  },
  translate,
);
assert.equal(invalidCheckpointPolicy.rollback, "approval.presentation.rollbackDepends");

const secretMarker = "SENSITIVE_INPUT_SENTINEL";
const general = presentApproval(
  {
    id: "approval-general-delete",
    status: "pending",
    targetTool: "vrcforge_delete_path",
    arguments: { path: "C:/General Project/old-note.txt" },
  },
  translate,
);
assert.equal(general.title, "approval.presentation.generalTitle");
assert.equal(general.summary, "approval.presentation.generalSummary:approval.presentation.generalAction.delete|old-note.txt");
assert.equal(
  general.notificationSummary,
  "approval.presentation.generalNotificationSummary:approval.presentation.generalAction.delete",
);
assert.equal(general.notificationSummary.includes("C:/General Project"), false);

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
  assert.ok(messages.approval.presentation.generalTitle);
  assert.ok(messages.approval.presentation.generalNotificationSummary);
  assert.ok(messages.approval.presentation.rollbackSkippedByUser.includes("{{sourceQuote}}"));
}
assert.equal(
  JSON.parse(await read("src/locales/zh-CN.json")).approval.presentation.rollbackSkippedByUser,
  "本次无还原点（用户声明引用）：{{sourceQuote}}",
);

console.log("approval notification summary UI contract: ok");
