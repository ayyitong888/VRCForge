import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appPath = path.join(root, "src", "App.tsx");
const snapshotPath = process.env.VRCFORGE_NOTIFICATION_TASK_SNAPSHOT;

test("a pending review revision is emitted once across reconnect and restart", async () => {
  let snapshot;
  try {
    snapshot = JSON.parse(await readFile(snapshotPath, "utf8"));
  } catch {
    snapshot = {
      tasks: [{
        id: "review-task",
        displayName: "Review",
        revision: 4,
        parentChatId: "parent-chat",
      }],
    };
  }
  const task = snapshot.tasks.find((item) => item.revision === 4);
  assert.ok(task);
  const key = `${task.id}:${task.revision}:${task.parentChatId}`;
  const persisted = new Set();
  let sends = 0;
  for (const cycle of ["initial", "reconnect", "restart"]) {
    void cycle;
    if (!persisted.has(key)) {
      persisted.add(key);
      sends += 1;
    }
  }
  assert.equal(sends, 1);
  assert.equal(key, `${task.id}:4:${task.parentChatId}`);
  assert.notEqual(`${task.id}:5:${task.parentChatId}`, key);

  const reconcile = (known, tasks) => {
    if (!tasks.length) return;
    const pending = new Set(
      tasks
        .filter((item) => item.status === "completed" && !item.mergeDecision && item.revision > 0 && item.parentChatId)
        .map((item) => `${item.id}:${item.revision}:${item.parentChatId}`),
    );
    const taskIds = new Set(tasks.map((item) => item.id));
    for (const id of [...known]) {
      if (taskIds.has(id.split(":", 1)[0]) && !pending.has(id)) known.delete(id);
    }
  };
  const retained = new Set([key]);
  reconcile(retained, []); // empty reconnect snapshot: preserve
  reconcile(retained, [{ ...task, status: "completed", mergeDecision: "" }]); // chat not loaded: preserve
  assert.deepEqual([...retained], [key]);
  reconcile(retained, [{ ...task, status: "completed", mergeDecision: "adopted" }]); // explicit resolution: prune
  assert.deepEqual([...retained], []);

  const delivered = new Set();
  const inFlight = new Set(["task-a:2:chat-a", "task-b:3:chat-b"]);
  delivered.add("task-a:2:chat-a");
  inFlight.delete("task-a:2:chat-a");
  assert.deepEqual([...delivered], ["task-a:2:chat-a"]);
  assert.equal(delivered.has("task-b:3:chat-b"), false); // failed/unfinished peer is not persisted
});

test("notification path keeps exact click identity and does not clear known ids", async () => {
  const app = await readFile(appPath, "utf8");
  assert.match(app, /loadSubAgentReviewNotificationIds\(\)/);
  assert.match(app, /saveSubAgentReviewNotificationIds\(deliveredSubAgentReviewNotificationIdsRef\.current\)/);
  assert.match(app, /if \(!taskSnapshot\.length\)\s*\{\s*return;/);
  assert.match(app, /const pendingReviewNotificationIds = new Set\(allReviewTasks\.map/);
  assert.match(app, /const snapshotTaskIds = new Set\(taskSnapshot\.map/);
  assert.match(app, /const deliver = \(\) => notify\(\)\.then\(\(\) => \{/);
  assert.match(app, /deliveredSubAgentReviewNotificationIdsRef/);
  assert.match(app, /inFlightSubAgentReviewNotificationIdsRef/);
  assert.doesNotMatch(app, /knownSubAgentReviewNotificationIdsRef\.current\.add\(reviewNotificationId\);\s*saveSubAgentReviewNotificationIds/);
  assert.doesNotMatch(app, /knownSubAgentReviewNotificationIdsRef\.current\.clear\(\)/);
  assert.match(app, /task\.revision !== action\.revision/);
  assert.match(app, /task\.parentChatId !== action\.parentChatId/);
  const surface = await readFile(path.join(root, "src", "components", "subagents", "sub-agent-workspace-surface.tsx"), "utf8");
  assert.match(surface, /onIgnoreNotification/);
  assert.match(surface, /subagent\.ignoreNotification/);
  assert.match(app, /setSelectedSubAgentPanelOpen\(false\)/);
});
