import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import ts from "typescript";

const source = fs.readFileSync(path.resolve("src/components/chat/conversation-timeline.tsx"), "utf8");
const start = source.indexOf("function normalizeAgentSteps");
const end = source.indexOf("\nfunction normalizeAgentStepKind", start);
const functionSource = `export ${source.slice(start, end)}`;
const output = ts.transpileModule(functionSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const exports = {};
vm.runInNewContext(output, { exports, module: { exports } });

const steps = [
  { index: 0, kind: "tool", tool: "current", historical: false },
  { index: 1, kind: "tool", tool: "replayed", historical: true },
  { index: 2, kind: "assistant", text: "current reply" },
];
const normalized = exports.normalizeAgentSteps(steps);
assert.deepEqual(normalized.map(({ step }) => step.tool || step.kind), ["current", "assistant"]);
assert.equal(normalized.some(({ step }) => step.historical === true), false);
console.log("historical step projection behavior passed");
