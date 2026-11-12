import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

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
const bundled = await build({
  entryPoints: [path.join(root, "src/hooks/use-runtime-turn-continuation.ts")],
  write: false, bundle: true, platform: "node", format: "cjs", external: ["react"],
});
const module = { exports: {} };
new Function("require", "module", "exports", bundled.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports);
const { runtimeContinuationDeliveryState } = module.exports;
const event = { sessionId: "session", clientTurnId: " client ", turnId: "backend-turn", continuationSource: "foreground_completed" };
const stored = (response) => [{ type: "agent", response }];
assert.equal(runtimeContinuationDeliveryState(event, stored({ sessionId: "session", clientTurnId: "client" })), "stored");
assert.equal(runtimeContinuationDeliveryState(event, stored({ session_id: "session", turn_id: "backend-turn" })), "stored");
assert.equal(runtimeContinuationDeliveryState(event, stored({ sessionId: "previous", clientTurnId: "client", turnId: "backend-turn" })), "append");
assert.equal(runtimeContinuationDeliveryState(event, stored({ sessionId: "session", clientTurnId: "different", turnId: "backend-turn" })), "append");
assert.match(hook, /appendToChatRef\.current\(ownerChat\.id/);
assert.match(hook, /completedAt\?: string;/);
assert.match(hook, /const completedAt = event\.completedAt;/);
assert.doesNotMatch(hook, /createdAt: lastTimestamp \|\||completedAt \|\| new Date\(\)\.toISOString\(\)/, "missing server terminal time is not replaced with a local timestamp");
const replyIdPrefix = hook.match(/id: `([^`$]+)\$\{continuationId\}`/)?.[1];
assert.ok(replyIdPrefix, "continuation reply must have an app-owned identity");
assert.equal(replyIdPrefix.includes("sk-"), false, "continuation ids must survive the bridge secret-prefix filter instead of collapsing to [redacted]");
assert.match(app, /eventType === "agentRuntimeTurn"[\s\S]*?const receipt = event\.payload\?\.payload[\s\S]*?deliverRuntimeTurnContinuation\(receipt\)/);
assert.match(app, /bootstrap\?\.runtimeContinuations \?\? \[\]/);
assert.match(app, /for \(const continuation of bootstrap\?\.runtimeContinuations/);
assert.match(app, /deliverRuntimeTurnContinuation\(continuation\)/);
assert.doesNotMatch(app, /helloPayload.*runtimeContinuations/s);
assert.match(types, /continuationSource\?: string;/);
assert.match(types, /runtimeContinuations\?: Array<Record<string, unknown>>;/);

console.log("runtime turn continuation UI contract passed");
