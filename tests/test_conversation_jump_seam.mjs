import { readFile } from "node:fs/promises";
import assert from "node:assert/strict";

// Cross-session capability visibility: agent-forwarded messages must render
// with attribution plus a jump back to the source conversation, and the
// send/followup/create tool results must offer an open-conversation jump.
const card = await readFile(new URL("../src/components/chat/conversation-card.tsx", import.meta.url), "utf8");
const timeline = await readFile(new URL("../src/components/chat/conversation-timeline.tsx", import.meta.url), "utf8");
const workspace = await readFile(new URL("../src/components/chat/chat-workspace.tsx", import.meta.url), "utf8");
const app = await readFile(new URL("../src/App.tsx", import.meta.url), "utf8");
const types = await readFile(new URL("../src/lib/chat-types.ts", import.meta.url), "utf8");

assert.match(types, /forwardedByAgent\?: boolean/, "user items carry the forwarded marker");
assert.match(types, /forwardedFrom\?: \{ sourceChatId/, "user items carry the source identity");

assert.match(card, /data-forwarded-agent-attribution/, "forwarded messages render an attribution row");
assert.match(card, /t\("chat\.forwardedByAgent"\)/, "attribution text is i18n-owned");
assert.match(card, /onOpenChatById\(sourceChatId\)/, "the source jump calls the open-chat callback");

assert.match(timeline, /vrcforge_send_message/, "send_message results are jump targets");
assert.match(timeline, /vrcforge_followup_task/, "followup_task results are jump targets");
assert.match(timeline, /vrcforge_create_conversation/, "create_conversation results are jump targets");
assert.match(timeline, /data-open-conversation/, "the timeline renders the jump button");
assert.match(timeline, /conversationResultChatId\(detail\)/, "the jump reads the result chatId, not prose");

assert.match(workspace, /onOpenChatById=\{onOpenChatById\}/, "the workspace threads the callback to cards");
assert.match(app, /onOpenChatById=\{\(chatId\) =>/, "App wires the callback");

for (const locale of ["zh-CN", "zh-TW", "en-US", "ja-JP"]) {
  const messages = JSON.parse(await readFile(new URL(`../src/locales/${locale}.json`, import.meta.url), "utf8"));
  for (const key of ["forwardedByAgent", "openSourceConversation", "openConversation"]) {
    assert.ok(typeof messages.chat?.[key] === "string" && messages.chat[key].trim(), `${locale}: chat.${key} present`);
  }
}

console.log("conversation jump seam contract passed");
