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
  require: () => ({ normalizeProjectPathKey: (value = "") => value.replace(/\//g, "\\").trim().toLowerCase() }),
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
assert.equal(exports.questionBelongsToRuntimeScope({ questionId: "q", sessionId: "session-owner", projectRoot: scope.projectRoot }, scope, { requireSession: true }), true);
assert.equal(exports.questionBelongsToRuntimeScope({ questionId: "q", sessionId: "other-session", projectRoot: scope.projectRoot }, scope, { requireSession: true }), false);
console.log("runtime scope behavior passed");
