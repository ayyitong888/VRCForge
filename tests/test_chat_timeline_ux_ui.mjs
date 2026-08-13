import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const [app, workspace, card, timeline, timelinePresentation, projection, noticeHook] = await Promise.all([
  readFile(resolve(root, "src", "App.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "chat", "chat-workspace.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "chat", "conversation-card.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "chat", "conversation-timeline.tsx"), "utf8"),
  readFile(resolve(root, "src", "lib", "chat-timeline-presentation.ts"), "utf8"),
  readFile(resolve(root, "src", "lib", "conversation-utils.ts"), "utf8"),
  readFile(resolve(root, "src", "hooks", "use-transient-failure-notice.ts"), "utf8"),
]);

assert.match(card, /editingText/);
assert.match(card, /onEditItemSave/);
assert.match(card, /onEditItemCancel/);
assert.match(card, /<textarea[\s\S]*value=\{displayedText\}/);
assert.doesNotMatch(workspace, /editing=\{Boolean\(editingMessage/);

assert.match(timeline, /normalizeAgentSteps/);
assert.match(timeline, /buildDurableTimelineRows/);
assert.match(timelinePresentation, /finalAssistant/);
assert.match(timeline, /<ChatMarkdown text=\{entry\.text\}/);
assert.match(timelinePresentation, /"tool_call"|"tool_result"|"file_edit"|"command"|"subagent"/);
assert.match(timeline, /for \(const \{ step, sourceIndex \} of steps\)/);
assert.match(timeline, /return left\.sourceIndex - right\.sourceIndex/);
assert.match(timeline, /step\.tool \|\| skill\?\.tool/);
assert.match(timeline, /step\.status \|\| skill\?\.status/);
assert.match(timeline, /step\.result !== undefined/);
assert.match(timeline, /stepResult/);
assert.doesNotMatch(timeline, /assigned\.has\(/);

assert.doesNotMatch(app, /ThumbsUp|ThumbsDown|messageFeedback|onFeedbackItem/);
assert.doesNotMatch(card, /ThumbsUp|ThumbsDown|onFeedback/);
assert.doesNotMatch(card, /localIdle|keywordPlannerHint|deterministic-local/);
assert.doesNotMatch(timeline, /deterministic-local/);
assert.doesNotMatch(timeline, /planner\.local/);

const copyBindings = card.match(/onCopy=\{copyableReply \? \(\) => onCopyItem\?\.\(\{ \.\.\.item, response \}\) : undefined\}/g) || [];
assert.equal(copyBindings.length, 1, "the natural-language agent card must bind copy once");
assert.match(card, /copyableAgentDialogueText\(response\)/);
assert.match(card, /onCopy=\{copyableReply \? \(\) => onCopyItem\?\.\(\{ \.\.\.item, response \}\) : undefined\}/);
assert.match(card.slice(card.indexOf('if (item.type === "user")'), card.indexOf('if (item.type === "error")')), /onCopy=\{\(\) => onCopyItem\?\.\(item\)\}/);
assert.doesNotMatch(card.slice(card.indexOf('if \(item.type === "result"\)'), card.indexOf('if \(item.type === "approval_revision"\)')), /onCopy=/);
assert.doesNotMatch(card.slice(card.indexOf('if \(item.type === "approval_revision"\)'), card.indexOf('if \(item.type === "compact"\)')), /onCopy=/);
assert.doesNotMatch(card.slice(card.indexOf('if \(item.type === "subagent"\)'), card.indexOf("const response =")), /onCopy=/);
assert.match(card, /if \(item\.type === "timeline_event"\)/);
assert.match(card, /buildDurableTimelineRows\(\[item\.event\]\)/);
const timelineEventBranch = card.slice(card.indexOf('if (item.type === "timeline_event")'), card.indexOf("const response ="));
assert.doesNotMatch(timelineEventBranch, /MessageActions|createdAt/, "an event row must not receive its own chat-message timestamp");
const agentBranch = card.slice(card.indexOf("const response ="));
assert.doesNotMatch(agentBranch.slice(agentBranch.indexOf("<MessageActions"), agentBranch.indexOf("</div>", agentBranch.indexOf("<MessageActions"))), /createdAt=/, "the Agent card uses its one elapsed marker instead of a second wall-clock label");

assert.match(projection, /event\.kind === "planner" \|\| event\.kind === "assistant"/);
assert.match(projection, /visibleTimelineProse\.join\("\\n\\n"\)/);
assert.doesNotMatch(projection, /`Tool:|`Command:|`Write:/);
assert.match(projection, /export function copyableAgentDialogueText/);
assert.match(projection, /if \(item\.type === "user"\)[\s\S]*return item\.text/);
assert.doesNotMatch(
  projection.slice(projection.indexOf("export function conversationItemText"), projection.indexOf("export function latestConversationItemId")),
  /item\.type === "result"|item\.type === "approval_revision"|item\.type === "subagent"/,
);
assert.match(noticeHook, /"copy"/);
assert.match(noticeHook, /TRANSIENT_FAILURE_NOTICE_MS = 3_000/);
assert.match(app, /showTransientNotice\("success", "copy"/);
assert.match(app, /showTransientNotice\("error", "copy"/);

assert.match(workspace, /data-chat-history-scroll/);
assert.match(workspace, /min-h-0 flex-1 overflow-auto/);
assert.match(workspace, /data-chat-composer-dock/);
assert.ok(
  workspace.indexOf("data-chat-history-scroll") < workspace.lastIndexOf("data-chat-composer-dock"),
  "the independently scrollable history must remain above the fixed decision/composer dock",
);
assert.match(app, /setPinnedToConversationBottom\(nearBottom\)/);

console.log("chat timeline UX contract: ok");
