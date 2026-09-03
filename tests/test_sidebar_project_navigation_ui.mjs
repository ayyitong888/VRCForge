import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appSource = await readFile(path.join(root, "src/App.tsx"), "utf8");
const sidebarSource = await readFile(path.join(root, "src/components/sidebar/app-sidebar.tsx"), "utf8");
const sessionsSource = await readFile(path.join(root, "src/hooks/use-chat-sessions.ts"), "utf8");

function functionBody(source, name, nextName) {
  const start = source.indexOf(`function ${name}(`);
  const end = source.indexOf(`function ${nextName}(`, start + 1);
  assert.notEqual(start, -1, `${name} must exist`);
  assert.notEqual(end, -1, `${nextName} must follow ${name}`);
  return source.slice(start, end);
}

test("project rows start a project conversation while chat rows restore history", () => {
  assert.match(appSource, /onSelectProject=\{newConversationForProject\}/);
  assert.doesNotMatch(appSource, /onSelectProject=\{selectProjectByPath\}/);

  assert.equal(
    sidebarSource.match(/onClick=\{\(\) => onSelectProject\(key\)\}/g)?.length,
    2,
    "General and Unity project rows must share project-new-conversation semantics",
  );
  assert.equal(
    sidebarSource.match(/onClick=\{\(\) => onOpenChat\(chat\)\}/g)?.length,
    3,
    "General, Unity, and temporary chat rows must restore the selected chat",
  );

  const newConversationBody = functionBody(sessionsSource, "newConversation", "togglePinChat");
  assert.match(newConversationBody, /setActiveChatId\(""\)/);

  const openChatBody = functionBody(sessionsSource, "openChat", "selectProject");
  assert.match(openChatBody, /setActiveChatId\(chat\.id\)/);
});
