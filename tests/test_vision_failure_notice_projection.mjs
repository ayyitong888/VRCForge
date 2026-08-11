import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import ts from "typescript";

const root = resolve(import.meta.dirname, "..");
const sourcePath = resolve(root, "src", "lib", "vision-failure-notice.ts");
const source = await readFile(sourcePath, "utf8");
const javascript = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
  fileName: sourcePath,
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(javascript).toString("base64")}`;
const { projectVisionFailureNotice } = await import(moduleUrl);

const translate = (key) => ({
  "notifications.visionFailed": "Vision failed.",
  "notifications.visionRetryable": "The captured images were retained for retry.",
  "notifications.visionReattach": "Attach or capture the images again before retrying.",
})[key] || key;

const permanent = projectVisionFailureNotice({
  vision: {
    status: "error",
    provider: "deepseek",
    providerLabel: "DeepSeek",
    model: "deepseek-v4-flash",
    error: "Selected provider rejected image input.",
    retryable: false,
    retainImages: false,
  },
}, translate);
assert.deepEqual(permanent, {
  kind: "vision",
  message: "DeepSeek · deepseek-v4-flash: Selected provider rejected image input. Attach or capture the images again before retrying.",
});

const transient = projectVisionFailureNotice({
  skill: {
    tool: "vrcforge_vision_audit_multi",
    result: {
      results: [{
        providerError: {
          provider: "openai",
          providerLabel: "OpenAI",
          model: "gpt-5.1",
          error: "Rate limit.",
          retryable: true,
          retainImages: true,
        },
      }],
    },
  },
}, translate);
assert.deepEqual(transient, {
  kind: "vision",
  message: "OpenAI · gpt-5.1: Rate limit. The captured images were retained for retry.",
});

assert.equal(projectVisionFailureNotice({ vision: { status: "ok" } }, translate), null);
assert.equal(projectVisionFailureNotice({
  skill: { tool: "another_tool", result: { results: [{ providerError: { error: "ignored" } }] } },
}, translate), null);

console.log("vision failure notice projection: ok");
