import assert from "node:assert/strict";
import { build } from "esbuild";

const bundle = await build({
  entryPoints: ["src/lib/conversation-utils.ts"],
  bundle: true,
  write: false,
  format: "esm",
  platform: "node",
});
const { conversationItemRenderKey } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`,
);

const duplicateItems = [
  { id: "[redacted]", type: "agent", response: { clientTurnId: "turn:question", turnId: "runtime-1" } },
  { id: "[redacted]", type: "agent", response: { clientTurnId: "turn:approval-a", turnId: "runtime-2" } },
  { id: "[redacted]", type: "agent", response: { clientTurnId: "turn:approval-b", turnId: "runtime-3" } },
];
const keys = duplicateItems.map((item, index, items) => conversationItemRenderKey(item, "chat-a", new Map()));
assert.equal(new Set(keys).size, keys.length, "runtime identities must be unique");
assert.equal(keys[0], "chat-a:agent:turn:question");

const occurrences = new Map();
const first = conversationItemRenderKey({ id: "legacy", type: "error", text: "a" }, "chat-a", occurrences);
const second = conversationItemRenderKey({ id: "legacy", type: "error", text: "b" }, "chat-a", occurrences);
assert.equal(first, "chat-a:error:legacy");
assert.equal(second, "chat-a:error:legacy#2");

const streaming = new Map();
const durableKey = conversationItemRenderKey(duplicateItems[0], "chat-a", streaming);
const streamKey = conversationItemRenderKey({ id: "stream", type: "streaming", clientTurnId: "turn:new" }, "chat-a", streaming);
assert.equal(durableKey, "chat-a:agent:turn:question", "existing identity key remains stable when a stream is added");
assert.notEqual(streamKey, durableKey);
assert.notEqual(conversationItemRenderKey(duplicateItems[0], "chat-b", new Map()), durableKey, "chat scopes stay isolated");
console.log("chat render keys regression passed");
