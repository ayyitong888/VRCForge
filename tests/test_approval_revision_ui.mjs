import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");

const hook = read("src/hooks/use-approval-execution.ts");
const types = read("src/lib/chat-types.ts");
const history = read("src/lib/conversation-utils.ts");
const scopedCard = read("src/components/approvals/scoped-pending-approval-card.tsx");
const inlineCard = read("src/components/chat/conversation-card.tsx");

assert.match(hook, /if \(approval\.goalDeliveryId\?\.trim\(\)\) \{\s*return;/);
assert.match(hook, /const payload = await requestApprovalRevision/);
assert.match(hook, /if \(!payload\.ok\)/);
assert.ok(hook.indexOf("const payload = await requestApprovalRevision") < hook.indexOf("appendToChat(activeChatId"));
assert.doesNotMatch(hook, /approval\.arguments|approval\.paramsSummary|approval\.preview/);
assert.match(hook, /type: "approval_revision"/);
assert.match(hook, /status: "awaiting_user_input"/);
assert.match(types, /type: "approval_revision"/);
assert.match(types, /approvalId: string;\s*targetTool: string;\s*requestedAt: string;\s*reason: string;\s*note: string;\s*status: "awaiting_user_input";/s);
assert.doesNotMatch(history.slice(history.indexOf("export function buildChatHistory"), history.indexOf("export function visibleAgentDialogueText")), /approval_revision/);
assert.match(scopedCard, /!approval\.goalDeliveryId\?\.trim\(\)/);
assert.match(inlineCard, /!approval\.goalDeliveryId\?\.trim\(\)/);
assert.match(inlineCard, /data-approval-revision=/);

console.log("approval revision UI contract passed");
