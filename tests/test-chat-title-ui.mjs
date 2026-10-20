import assert from "node:assert/strict";
import fs from "node:fs";

const api = fs.readFileSync("src/lib/api/notes-chats.ts", "utf8");
const types = fs.readFileSync("src/lib/chat-types.ts", "utf8");
const sessions = fs.readFileSync("src/hooks/use-chat-sessions.ts", "utf8");
const controller = fs.readFileSync("src/hooks/use-chat-run-controller.ts", "utf8");

assert.match(api, /generate_chat_title/);
assert.match(api, /\/api\/app\/chat-title/);
assert.match(types, /titleSource\?: "auto" \| "manual"/);
assert.match(sessions, /titleSource: "manual"/);
assert.match(controller, /titleGenerationAttemptedRef/);
assert.match(controller, /requestChatTitle\(endpoint, message, provider, model\)/);
assert.match(controller, /current\.titleSource === "manual"/);
assert.match(controller, /shouldGenerateChatTitle\(chat, baseItems\.length, background\)/);

console.log("chat title UI wiring checks passed");
