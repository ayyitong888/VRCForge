import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const [app, workspace, card, timeline, projection, noticeHook] = await Promise.all([
  readFile(resolve(root, "src", "App.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "chat", "chat-workspace.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "chat", "conversation-card.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "chat", "conversation-timeline.tsx"), "utf8"),
  readFile(resolve(root, "src", "lib", "conversation-utils.ts"), "utf8"),
  readFile(resolve(root, "src", "hooks", "use-transient-failure-notice.ts"), "utf8"),
]);

assert.match(card, /editingText/);
assert.match(card, /onEditItemSave/);
assert.match(card, /onEditItemCancel/);
assert.match(card, /<textarea[\s\S]*value=\{displayedText\}/);
assert.doesNotMatch(workspace, /editing=\{Boolean\(editingMessage/);

assert.match(timeline, /normalizeAgentSteps/);
assert.match(timeline, /for \(const \{ step, sourceIndex \} of steps\)/);
assert.match(timeline, /return left\.sourceIndex - right\.sourceIndex/);
assert.match(timeline, /step\.tool \|\| skill\?\.tool/);
assert.match(timeline, /step\.status \|\| skill\?\.status/);
assert.doesNotMatch(timeline, /assigned\.has\(/);

assert.doesNotMatch(app, /ThumbsUp|ThumbsDown|messageFeedback|onFeedbackItem/);
assert.doesNotMatch(card, /ThumbsUp|ThumbsDown|onFeedback/);

assert.match(projection, /item\.response\.plan\?\.reply \|\| item\.response\.plan\?\.summary \|\| ""/);
assert.doesNotMatch(projection, /`Tool:|`Command:|`Write:/);
assert.match(noticeHook, /"copy"/);
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
