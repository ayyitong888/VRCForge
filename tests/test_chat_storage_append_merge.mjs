import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { build } from "esbuild";

const bundle = await build({
  entryPoints: ["src/lib/chat-storage-reconcile.ts"], bundle: true, write: false,
  format: "esm", platform: "node",
  plugins: process.env.VRCFORGE_CHAT_RECONCILE_BASELINE ? [{
    name: "original-reconcile",
    setup(builder) {
      builder.onLoad({ filter: /chat-storage-reconcile\.ts$/ }, () => ({
        contents: readFileSync(process.env.VRCFORGE_CHAT_RECONCILE_BASELINE, "utf8"), loader: "ts",
      }));
    },
  }] : [],
});
const { reconcileChatStorage: reconcile, snapshotChatFingerprints: fingerprints } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`,
);
const item = (id, text = id) => ({ id, type: "user", text });
const base = { id: "chat", sessionId: "s", projectPath: "", title: "work", revision: 2,
  updatedAt: "2026-01-01T00:00:00Z", items: [item("original")] };
const left = { ...base, revision: 5, updatedAt: "2026-01-01T00:00:01Z", items: [...base.items, item("local")] };
const right = { ...base, revision: 3, updatedAt: "2026-01-01T00:00:02Z", items: [...base.items, item("remote")] };
const versions = { baseline: { chat: "1".repeat(64) }, remote: { chat: "2".repeat(64) } };
const run = (local = left, remote = right, baseline = base) => reconcile(fingerprints([baseline]), [local], [remote], undefined, versions);
let result = run();
assert.equal(result.status, "merged", "independent append-only messages must not freeze the entire chat");
assert.deepEqual(result.chats[0].items.map((value) => value.id), ["original", "remote", "local"]);
assert.equal(result.chats[0].revision, 6, "invalidate stale in-flight mutations after merging");
assert.equal(result.chats[0].updatedAt, right.updatedAt);
assert.deepEqual(result.baselineFingerprints.chat, fingerprints([right]).chat, "delta baseline remains the acknowledged remote version");
assert.equal(run({ ...base, revision: 9 }).status, "merged", "local runtime activity must not hide a remote append");
const streamed = { ...base, revision: 4, items: [...base.items, { id: "live", type: "streaming", clientTurnId: "turn", text: "working" }] };
const durable = (chat) => ({ ...chat, items: chat.items.filter((value) => value.type !== "streaming") });
result = reconcile(fingerprints([base], durable), [streamed], [right], durable, versions);
assert.equal(result.status, "merged");
assert.ok(result.chats[0].items.some((value) => value.id === "live"), "keep the active in-memory stream");
assert.equal(run({ ...left, title: "user edit" }).chats[0].title, "user edit", "a unilateral metadata edit merges with independent appends");
assert.equal(run({ ...left, title: "local edit" }, { ...right, title: "remote edit" }).status, "conflict");
assert.equal(run({ ...left, items: [item("original", "edited"), item("local")] }).status, "conflict");
const finalized = item("original", "finalized by the earlier save");
const afterSave = { ...base, revision: 3, items: [finalized] };
assert.equal(run(afterSave, afterSave).status, "merged", "the same visible save must not become a conflict");
result = run({ ...left, items: [finalized, item("local")] }, afterSave);
assert.equal(result.status, "merged", "a previously saved update shared by both sides can precede a new local append");
assert.deepEqual(result.chats[0].items, [finalized, item("local")]);
result = run(left, { ...right, items: [finalized, item("remote")] });
assert.equal(result.status, "merged", "a local append must preserve a server update to an untouched earlier item");
assert.deepEqual(result.chats[0].items, [finalized, item("remote"), item("local")]);
assert.equal(run({ ...left, items: [item("original", "local edit"), item("local")] },
  { ...right, items: [finalized, item("remote")] }).status, "conflict", "divergent edits still require review");
assert.equal(run({ ...left, items: [] }).status, "conflict");
assert.equal(run({ ...left, items: [...base.items, item("remote", "different")] }).status, "conflict");
assert.equal(run({ ...left, items: [...base.items, item("same"), item("same")] }).status, "conflict");
assert.equal(run({ ...left, items: [...base.items, item("x"), item("y")] },
  { ...right, items: [...base.items, item("y"), item("x")] }).status, "conflict", "contradictory message order cannot be guessed");
assert.equal(run(left, { ...base }).status, "conflict", "opaque remote mutation without a visible append stays protected");
assert.equal(run(left, { ...right, updatedAt: "invalid" }).status, "conflict");
assert.equal(run(left, { ...right, revision: Number.MAX_SAFE_INTEGER }).status, "conflict");
console.log("PASS independent chat appends, stream preservation, revision ownership, existing-content and opaque conflicts");
