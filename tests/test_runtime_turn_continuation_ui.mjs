import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");

const hook = read("src/hooks/use-runtime-turn-continuation.ts");
const app = read("src/App.tsx");
const types = read("src/lib/api/types.ts");

assert.match(hook, /RUNTIME_CONTINUATION_SOURCES\.has\(event\.continuationSource \|\| ""\)/);
assert.match(hook, /sub_agent_finished/);
assert.match(hook, /chat\.sessionId === event\.sessionId/);
assert.doesNotMatch(hook, /activeChatId|activeChat/);
assert.match(hook, /deliveredRef\.current\.has\(key\)/);
assert.match(hook, /pendingRef\.current\.set\(key, event\)/);
assert.match(hook, /const continuationId = event\.clientTurnId\?\.trim\(\) \|\| event\.turnId/);
assert.match(hook, /item\.response\.clientTurnId === event\.clientTurnId\.trim\(\)/);
assert.match(hook, /item\.response\.turnId \|\| item\.response\.turn_id/);
assert.match(hook, /appendToChatRef\.current\(ownerChat\.id/);
assert.match(app, /deliverRuntimeTurnContinuation\(event\.payload\?\.payload\)/);
assert.match(app, /bootstrap\?\.runtimeContinuations \?\? \[\]/);
assert.match(app, /for \(const continuation of bootstrap\?\.runtimeContinuations/);
assert.match(app, /deliverRuntimeTurnContinuation\(continuation\)/);
assert.doesNotMatch(app, /helloPayload.*runtimeContinuations/s);
assert.match(types, /continuationSource\?: string;/);
assert.match(types, /runtimeContinuations\?: Array<Record<string, unknown>>;/);

console.log("runtime turn continuation UI contract passed");
