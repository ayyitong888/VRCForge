import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import ts from "typescript";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = fs.readFileSync(path.join(root, "src/lib/runtime-scope.ts"), "utf8");
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const exports = {};
vm.runInNewContext(output, {
  exports,
  module: { exports },
  require: () => ({
    normalizeProjectPathKey: (value = "") => value.replace(/\//g, "\\").trim().toLowerCase(),
    approvalIdFromResponse: (response) => response.approvalId || "",
  }),
});

const scope = { sessionId: "session-owner", projectRoot: "C:\\UnityProject" };
const approval = (sessionId, projectRoot = scope.projectRoot) => ({
  id: `${sessionId || "none"}-${projectRoot}`,
  status: "pending",
  projectRoot,
  taskContext: sessionId ? { sessionId } : {},
});
assert.equal(exports.approvalBelongsToRuntimeScope(approval("session-owner"), scope, { requireSession: true }), true);
assert.equal(exports.approvalBelongsToRuntimeScope(approval("other-session"), scope, { requireSession: true }), false);
assert.equal(exports.approvalBelongsToRuntimeScope(approval("session-owner", "D:\\Other"), scope, { requireSession: true }), false);
assert.equal(exports.approvalBelongsToRuntimeScope(approval("other-session"), scope), true);
assert.equal(exports.approvalBelongsToRuntimeScope(approval("session-owner"), { sessionId: "", projectRoot: scope.projectRoot }, { requireSession: true }), false);
const owner = { id: "owner", sessionId: "session-owner", projectPath: scope.projectRoot, items: [] };
const other = { ...owner, id: "other", sessionId: "other-session" };
const owned = approval("session-owner");
assert.equal(exports.pendingApprovalOwner(owned, [other, owner]), owner, "a unique catalog owner needs no loaded response body");
assert.equal(exports.pendingApprovalOwner(owned, [other]), undefined, "an orphan never becomes an approval in the active chat");
assert.equal(exports.pendingApprovalOwner(owned, [{ ...owner, projectPath: "D:\\Other" }]), undefined, "a cross-project session match is insufficient");
const receiptOwner = { ...owner, id: "receipt-owner", items: [{ type: "agent", response: { approvalId: owned.id } }] };
assert.equal(exports.pendingApprovalOwner(owned, [owner, { ...owner, id: "duplicate" }]), undefined, "duplicate sessions need exact receipt evidence");
assert.equal(exports.pendingApprovalOwner(owned, [owner, receiptOwner]), receiptOwner);
const receiptOnly = { ...owned, taskContext: undefined };
assert.equal(exports.pendingApprovalOwner(receiptOnly, [owner, receiptOwner]), receiptOwner, "a legacy request still requires an exact same-project receipt");
assert.equal(exports.pendingApprovalOwner(receiptOnly, [{ ...receiptOwner, projectPath: "D:\\Other" }]), undefined);
assert.equal(exports.pendingApprovalOwner({ ...owned, status: "applied" }, [owner]), undefined, "settled approvals cannot reopen");
assert.equal(exports.questionBelongsToRuntimeScope({ questionId: "q", sessionId: "session-owner", projectRoot: scope.projectRoot }, scope, { requireSession: true }), true);
assert.equal(exports.questionBelongsToRuntimeScope({ questionId: "q", sessionId: "other-session", projectRoot: scope.projectRoot }, scope, { requireSession: true }), false);
assert.deepEqual(
  exports.filterQuestionsForRuntimeScope([
    { questionId: "current", sessionId: "session-owner", projectRoot: scope.projectRoot },
    { questionId: "other", sessionId: "other-session", projectRoot: scope.projectRoot },
  ], scope).map((question) => question.questionId),
  ["current"],
);
assert.deepEqual(
  Array.from(exports.filterQuestionsForRuntimeScope([
    { questionId: "other", sessionId: "other-session", projectRoot: scope.projectRoot },
  ], { sessionId: "", projectRoot: scope.projectRoot })),
  [],
);
console.log("runtime scope behavior passed");
