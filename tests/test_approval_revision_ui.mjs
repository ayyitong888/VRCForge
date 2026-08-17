import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");

const hook = read("src/hooks/use-approval-execution.ts");
const types = read("src/lib/chat-types.ts");
const apiTypes = read("src/lib/api/types.ts");
const runtimeApi = read("src/lib/api/agent-runtime.ts");
const history = read("src/lib/conversation-utils.ts");
const scopedCard = read("src/components/approvals/scoped-pending-approval-card.tsx");
const pendingStrip = read("src/components/approvals/pending-approvals-strip.tsx");
const inlineCard = read("src/components/chat/conversation-card.tsx");
const inlineTimeline = read("src/components/chat/conversation-timeline.tsx");
const revisionEditor = read("src/components/approvals/approval-revision-editor.tsx");
const app = read("src/App.tsx");

assert.match(hook, /if \(approval\.goalDeliveryId\?\.trim\(\)\) \{\s*return;/);
assert.match(hook, /const payload = await requestApprovalRevision/);
assert.match(hook, /if \(!payload\.ok\)/);
assert.match(hook, /denyReasonCode,/);
assert.match(hook, /reason,/);
assert.doesNotMatch(hook, /approval\.arguments|approval\.paramsSummary|approval\.preview/);
assert.match(hook, /type: "approval_revision"/);
assert.match(hook, /status: "retrying"/);
assert.match(hook, /appendContinuation\(payload\.continuation\)/);
assert.doesNotMatch(hook, /setInput\(|setAttachments\(|textContextAttachment/);
assert.match(hook, /payload\.execution\?\.status === "needs_user_action"/);
assert.match(hook, /payload\.execution\.outcome\?\.summary/);
assert.match(hook, /error: payload\.execution\?\.error \|\| completionNotice/);
assert.match(hook, /const resultChatId = taskSessionId \? ownerChatId : activeChatId;/);
assert.match(hook, /appendToChat\(resultChatId/);
const approveStart = hook.indexOf("async function approveShell");
const approveEnd = hook.indexOf("async function rejectShell");
const approve = hook.slice(approveStart, approveEnd);
assert.doesNotMatch(approve, /appendToChat\(activeChatId/);
assert.ok(
  approve.indexOf("appendToChat(resultChatId") < approve.indexOf("appendContinuation(payload.continuation)"),
  "approved execution result must be appended before its assistant continuation",
);
assert.match(app, /const pendingApprovalItems = \(agentApprovals \?\? \[\]\)\.filter\(\s*\(item\) => item\.status === "pending",?\s*\)/);
assert.doesNotMatch(app, /item\.status === "pending" \|\| item\.status === "approved"/);
assert.match(scopedCard, /approval\.status === "pending"/);
assert.match(pendingStrip, /approval\.status === "pending"/);
const rejectStart = hook.indexOf("async function rejectShell");
const rejectEnd = hook.indexOf("function clearApprovalAction");
const reject = hook.slice(rejectStart, rejectEnd);
assert.match(reject, /if \(!payload\.ok\)/);
assert.ok(reject.indexOf("if (!payload.ok)") < reject.indexOf("appendContinuation"));
const continuationStart = hook.indexOf("function appendContinuation");
const continuationEnd = hook.indexOf("function pendingApprovalForResponse");
const continuation = hook.slice(continuationStart, continuationEnd);
assert.ok(continuationStart >= 0 && continuationEnd > continuationStart);
assert.match(continuation, /response\.sessionId \|\| response\.session_id/);
assert.match(continuation, /chatIdForSessionId\(ownerSessionId\)/);
assert.match(continuation, /appendToChat\(ownerChatId/);
assert.doesNotMatch(continuation, /appendToChat\(activeChatId/);
assert.match(apiTypes, /taskContext\?: \{[\s\S]*sessionId\?: string;/);
assert.match(runtimeApi, /continuation\?: AgentRuntimeResponse/);
assert.match(types, /type: "approval_revision"/);
assert.match(types, /approvalId: string;\s*targetTool: string;\s*requestedAt: string;\s*reason: string;\s*note: string;\s*denyReasonCode\?: string;\s*status: "retrying";/s);
assert.doesNotMatch(history.slice(history.indexOf("export function buildChatHistory"), history.indexOf("export function visibleAgentDialogueText")), /approval_revision/);
assert.match(scopedCard, /!approval\.goalDeliveryId\?\.trim\(\)/);
assert.match(scopedCard, /approval\.taskContext\.approvalRevisionUsed !== true/);
assert.match(scopedCard, /<ApprovalRevisionEditor/);
assert.match(scopedCard, /<ApprovalAllowSplitButton[\s\S]*onApprove=\{onApprove\}/);
assert.match(inlineTimeline, /!approval\.goalDeliveryId\?\.trim\(\)/);
assert.match(inlineTimeline, /approval\.taskContext\.approvalRevisionUsed !== true/);
assert.match(inlineTimeline, /<ApprovalRevisionEditor/);
assert.match(revisionEditor, /disabled=\{disabled \|\| !reason\.trim\(\)\}/);
assert.match(revisionEditor, /onSubmit\(reason\.trim\(\), reasonCode\)/);
assert.match(inlineCard, /data-approval-revision=/);
assert.match(inlineCard, /approval\.revisionRetrying/);
assert.doesNotMatch(inlineCard, /approval\.revisionAwaitingUserInput/);

console.log("approval revision UI contract passed");
