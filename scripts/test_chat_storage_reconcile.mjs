import assert from "node:assert/strict";
import {
  preserveConflictingChatCopies,
  reconcileChatStorage,
  snapshotChatFingerprints,
  sourceRevisionsFromPayload,
} from "../src/lib/chat-storage-reconcile.ts";

function chat(id, title, revision = 0) {
  return {
    id,
    sessionId: `session-${id}`,
    title,
    projectPath: "",
    revision,
    items: [],
  };
}

const baselineChats = [chat("local", "before-local"), chat("remote", "before-remote")];
const merged = reconcileChatStorage(
  snapshotChatFingerprints(baselineChats),
  [chat("local", "after-local", 1), chat("remote", "before-remote")],
  [chat("local", "before-local"), chat("remote", "after-remote", 1)],
);
assert.equal(merged.status, "merged");
assert.deepEqual(
  merged.chats.map((item) => [item.id, item.title]),
  [["local", "after-local"], ["remote", "after-remote"]],
);

const conflict = reconcileChatStorage(
  snapshotChatFingerprints([chat("shared", "before")]),
  [chat("shared", "local-change", 1)],
  [chat("shared", "remote-change", 1)],
);
assert.equal(conflict.status, "conflict");
assert.deepEqual(conflict.conflictIds, ["shared"]);
assert.equal(conflict.chats[0].title, "local-change");

const preserved = preserveConflictingChatCopies(
  snapshotChatFingerprints([chat("shared", "before")]),
  [chat("shared", "local-change", 1)],
  [chat("shared", "remote-change", 1)],
  (value) => ({ ...value, id: "shared-local-copy", sessionId: "session-local-copy" }),
);
assert.deepEqual(preserved.conflictIds, ["shared"]);
assert.deepEqual(
  preserved.chats.map((item) => [item.id, item.title]),
  [["shared", "remote-change"], ["shared-local-copy", "local-change"]],
);

const localDelete = reconcileChatStorage(
  snapshotChatFingerprints([chat("deleted", "before")]),
  [],
  [chat("deleted", "before")],
);
assert.equal(localDelete.status, "merged");
assert.deepEqual(localDelete.chats, []);

const durableProjection = ({ transient: _transient, ...value }) => value;
const persisted = chat("projected", "before");
const remoteOnly = reconcileChatStorage(
  snapshotChatFingerprints([persisted], durableProjection),
  [{ ...persisted, transient: { inlinePayload: "local-only" } }],
  [chat("projected", "remote-title", 1)],
  durableProjection,
);
assert.equal(remoteOnly.status, "merged");
assert.equal(remoteOnly.chats[0].title, "remote-title");

const sourceRevisions = sourceRevisionsFromPayload([
  {
    storeId: "chat.project.abc",
    scope: "project",
    exists: true,
    digest: "a".repeat(64),
    status: "ok",
    projectPath: "C:\\AvatarProject",
    path: "C:\\AvatarProject\\.vrcforge\\chat-transcripts.json",
  },
]);
assert.deepEqual(sourceRevisions, [
  {
    storeId: "chat.project.abc",
    scope: "project",
    exists: true,
    digest: "a".repeat(64),
    status: "ok",
    projectPath: "C:\\AvatarProject",
  },
]);

console.log("chat storage reconcile contract: ok");
