import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { build } from "esbuild";

const built = await build({ entryPoints: ["src/lib/chat-storage-reconcile.ts"], bundle: true, write: false, platform: "node", format: "esm",
  plugins: process.env.VRCFORGE_CHAT_RECONCILE_BASELINE ? [{ name: "baseline", setup(b) {
    b.onLoad({ filter: /chat-storage-reconcile\.ts$/ }, () => ({ loader: "ts", contents: readFileSync(process.env.VRCFORGE_CHAT_RECONCILE_BASELINE, "utf8") }));
  } }] : [] });
const { reconcileChatStorage: reconcile, snapshotChatFingerprints: fingerprints } = await import(`data:text/javascript;base64,${Buffer.from(built.outputFiles[0].text).toString("base64")}`);
const original = process.env.VRCFORGE_CHAT_METADATA_REPLAY ? JSON.parse(readFileSync(process.env.VRCFORGE_CHAT_METADATA_REPLAY, "utf8")) : {
  id: "metadata-chat", sessionId: "metadata-session", title: "work", projectPath: "", planMode: true, revision: 1, items: [{ id: "initial", type: "user", text: "Continue" }],
};
const base = { ...original, planMode: true };
const versions = { baseline: { [base.id]: "1".repeat(64) }, remote: { [base.id]: "2".repeat(64) } };
const durable = (chat) => ({ ...chat, items: chat.items.filter(item => item.type !== "streaming") });
const local = { ...base, revision: (base.revision || 0) + 2, lastViewedAt: "last-seen", items: [...base.items, { id: "stream", type: "streaming", clientTurnId: "running", text: "progress" }] };
const remote = { ...base, revision: (base.revision || 0) + 1, planMode: false, items: [...base.items, { id: "continued", type: "user", text: "Continue editing" }] };
const run = (l = local, r = remote) => reconcile(fingerprints([base], durable), [l], [r], durable, versions);
let result = run();
assert.equal(result.status, "merged", "external mode change + append must merge with local stream/revision/viewed state");
assert.equal(result.chats[0].planMode, false);
assert.equal(result.chats[0].lastViewedAt, local.lastViewedAt);
assert.deepEqual(new Set(result.chats[0].items.map(item => item.id)), new Set([...base.items.map(item => item.id), "stream", "continued"]));
assert.deepEqual(result.baselineFingerprints[base.id], fingerprints([remote], durable)[base.id]);
assert.equal(run({ ...local, planMode: false }).status, "merged", "identical metadata changes merge");
assert.equal(run(local, { ...remote, items: base.items }).status, "conflict", "metadata display differences alone cannot explain an opaque server version");
assert.equal(run({ ...local, title: "mine" }, { ...remote, title: "theirs" }).status, "conflict", "real same-field disagreement remains protected");
assert.equal(run(local, { ...base, revision: (base.revision || 0) + 4 }).status, "conflict", "opaque digest mutation cannot be explained by revision alone");
const deleted = { ...base }; delete deleted.lastViewedAt;
const withField = { ...base, lastViewedAt: "before" };
const deletion = reconcile(fingerprints([withField]), [{ ...deleted, revision: 3 }], [{ ...withField, planMode: false, items: remote.items }], undefined, versions);
assert.equal(deletion.status, "merged");
assert.equal(Object.hasOwn(deletion.chats[0], "lastViewedAt"), false, "unilateral field deletion is retained");
console.log(`PASS three-way metadata + append/stream replay (${base.items.length} original items), derived revision, deletion, real and opaque conflicts`);
