import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relativePath) => readFile(path.join(root, relativePath), "utf8");
const helperPath = path.join(root, "src/lib/provider-key-state.ts");
const helperSource = await readFile(helperPath, "utf8");
const helperOutput = ts.transpileModule(helperSource, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 },
  fileName: helperPath,
}).outputText;
const keyState = await import(`data:text/javascript;base64,${Buffer.from(helperOutput).toString("base64")}`);

const legacyCurrent = { provider: "openai", apiKeyPresent: true };
const providerHistory = {
  provider: "openai",
  apiKeyPresent: true,
  savedKeyProviders: ["anthropic", "gemini", "openai"],
};

assert.equal(keyState.hasSavedProviderKey("openai", legacyCurrent), true);
assert.equal(keyState.hasSavedProviderKey("anthropic", legacyCurrent), false);
assert.equal(keyState.hasSavedProviderKey("anthropic", providerHistory), true);
assert.equal(keyState.hasSavedProviderKey(" GEMINI ", providerHistory), true);
assert.equal(keyState.hasSavedProviderKey("deepseek", providerHistory), false);
assert.equal(keyState.hasSavedProviderKey("", providerHistory), false);

const [typesSource, hookSource, workspaceSource] = await Promise.all([
  read("src/lib/api/types.ts"),
  read("src/hooks/use-provider-settings.ts"),
  read("src/components/settings/settings-workspace.tsx"),
]);

assert.match(typesSource, /savedKeyProviders\?: string\[\]/);
assert.ok(hookSource.includes("hasSavedProviderKey(apiProvider, apiConfig, visionConfig)"));
assert.ok(hookSource.includes("hasSavedProviderKey(visionProvider, visionConfig, apiConfig)"));
assert.ok(workspaceSource.includes("hasSavedProviderKey(visionProvider, visionConfig)"));
assert.ok(!hookSource.includes("setApiKey(apiConfig.api_key"));
assert.ok(!hookSource.includes("setVisionApiKey(visionConfig.api_key"));

console.log("provider key persistence UI contract: ok");
