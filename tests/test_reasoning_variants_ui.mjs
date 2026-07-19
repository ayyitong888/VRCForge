import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");

const hook = read("src/hooks/use-provider-settings.ts");
const settings = read("src/components/settings/provider-settings.tsx");
const api = read("src/lib/api/app.ts");
const rustCommands = read("src-tauri/src/commands.rs");
const rustMain = read("src-tauri/src/main.rs");

assert.match(hook, /fetchReasoningVariants\(endpoint, \{ provider: apiProvider, model: apiModel\.trim\(\) \}\)/);
assert.doesNotMatch(hook, /setApiThinkingLevel\(\(current\)/);
assert.match(hook, /thinking_level: apiThinkingLevel === "default" \? "" : apiThinkingLevel/g);

assert.match(settings, /reasoningVariants\?\.variants \?\? \[\]/);
assert.match(settings, /<option value="default">/);
assert.match(settings, /hasUnsupportedReasoningVariant/);
assert.match(settings, /provider\.reasoningUnsupported/);
assert.match(settings, /supportedReasoningVariants\.map\(\(variant\)/);
assert.doesNotMatch(settings, /const REASONING_LEVELS/);

assert.match(api, /"fetch_reasoning_variants"/);
assert.match(api, /\/api\/app\/provider\/reasoning-variants/);
assert.match(rustCommands, /pub async fn fetch_reasoning_variants/);
assert.match(rustCommands, /"thinking_level"\.to_string\(\)[\s\S]*request\.thinking_level\.unwrap_or_default\(\)/);
assert.match(rustMain, /fetch_reasoning_variants/);

console.log("reasoning variants UI/backend contract: ok");
