import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const [app, panel, surface, notifications, rustNotifications, scopedApproval] = await Promise.all([
  readFile(resolve(root, "src", "App.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "subagents", "sub-agent-panel.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "subagents", "sub-agent-workspace-surface.tsx"), "utf8"),
  readFile(resolve(root, "src", "lib", "approval-notifications.ts"), "utf8"),
  readFile(resolve(root, "src-tauri", "src", "approval_notification_windows.rs"), "utf8"),
  readFile(resolve(root, "src", "components", "approvals", "scoped-pending-approval-card.tsx"), "utf8"),
]);
const mergePolicy = await readFile(resolve(root, "src", "lib", "subagent-merge.ts"), "utf8");

assert.match(panel, /data-vrcforge-open-subagent-surface/);
assert.match(panel, /statusRunning/);
assert.match(panel, /statusDone/);
assert.doesNotMatch(panel, /onMerge|onRetry|onCancel/);

assert.match(surface, /data-vrcforge-subagent-open-list/);
assert.match(surface, /overflow-y-auto/);
assert.match(surface, /sticky bottom-0/);
assert.match(surface, /onCancel\(activeTask\.id\)/);
assert.match(surface, /onRetry\(activeTask\.id\)/);
assert.match(surface, /onMerge\(activeTask, "adopted"\)/);
assert.match(surface, /onMerge\(activeTask, "dismissed"\)/);
assert.match(surface, /onAdoptNextAction\(activeTask\)/);
assert.match(surface, /canAdoptSubAgentResult\(activeTask\)/);
assert.match(surface, /canDismissSubAgentResult\(activeTask\)/);
assert.match(surface, /busyTaskIds\.has\(activeTask\.id\)/);
assert.match(surface, /resultUnavailable/);
assert.match(surface, /parentContinuationStatus/);
assert.match(mergePolicy, /task\.status === "completed"/);
assert.match(mergePolicy, /task\.status === "failed"/);
assert.match(mergePolicy, /!task\.mergeDecision/);
assert.doesNotMatch(mergePolicy, /task\.status === "cancelling"/);

assert.doesNotMatch(app, /selectedSubAgentPanelOpen \? subAgentWorkspaceSurface : activeView === "doctor"/);
assert.match(app, /selectedSubAgentPanelOpen \? \([\s\S]*absolute inset-x-0 bottom-0 top-14/);
assert.match(app, /fetchSubAgents\(endpoint, true\)/);
assert.match(app, /subagent-event-\$\{task\.id\}-\$\{revision\}/);
assert.match(app, /type: "timeline_event"/);
assert.match(app, /\["created", "started", "completed", "failed", "cancel_requested", "cancelled", "interrupted"\]/);
assert.match(app, /showSubAgentReviewNotification/);
assert.match(app, /handledSubAgentReviewNotificationIdsRef/);
assert.match(app, /fetchSubAgent\(endpoint, action\.taskId\)/);
assert.match(app, /task\.revision !== action\.revision/);
assert.match(app, /task\.parentChatId !== action\.parentChatId/);
assert.match(app, /!isAwaitingMergeReview\(task\)/);
assert.match(app, /openChat\(chat\)/);
assert.match(app, /setSelectedSubAgentPanelOpen\(true\)/);
assert.match(app, /subAgentInspectRequestRef/);
assert.match(app, /subAgentActionBusyRef/);

assert.match(notifications, /action !== "open"/);
assert.match(notifications, /ApprovalNotificationAction = "open"/);
assert.match(app, /const latest = await fetchAgentApprovals\(endpoint\)/);
assert.match(app, /item\.id === action\.approvalId && String\(item\.status \|\| ""\)[\s\S]*=== "pending"/, "toast activation must re-read and revalidate current backend pending state");
assert.match(scopedApproval, /data-approval-id=\{approval\.id\}/);
assert.doesNotMatch(app, /action\.action === "approve"/);
assert.match(notifications, /taskId/);
assert.match(notifications, /parentChatId/);
assert.match(notifications, /revisionValue <= 0/);

assert.match(rustNotifications, /show_sub_agent_review_notification/);
assert.match(rustNotifications, /focus_main_window\(&callback_app\)/);
assert.match(rustNotifications, /parse_sub_agent_review_notification_action/);
assert.doesNotMatch(rustNotifications, /merge_sub_agent|approve_sub_agent|dismiss_sub_agent/);
const mainApprovalToast = rustNotifications.slice(
  rustNotifications.indexOf("pub(crate) fn show_approval_notification"),
  rustNotifications.indexOf("pub(crate) fn show_sub_agent_review_notification"),
);
assert.doesNotMatch(mainApprovalToast, /\.add_button\(/);
assert.match(mainApprovalToast, /action: "open"/);

console.log("sub-agent workbench and notification deep-link contract: ok");
