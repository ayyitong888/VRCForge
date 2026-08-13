import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const [workspace, app] = await Promise.all([
  readFile(resolve(root, "src/components/chat/chat-workspace.tsx"), "utf8"),
  readFile(resolve(root, "src/App.tsx"), "utf8"),
]);

assert.doesNotMatch(workspace, /subAgentPanel/);
const chatWorkspaceInvocation = app.slice(app.indexOf("<ChatWorkspace"), app.indexOf("\n            />", app.indexOf("<ChatWorkspace")));
assert.doesNotMatch(chatWorkspaceInvocation, /subAgentPanel=/);
assert.match(app, /projectWorkspace=\{projectChatWorkspace\}[\s\S]*subAgentPanel=\{projectChatWorkspace \? subAgentActivityPanel : undefined\}/);
console.log("subagent central absence contract: ok");
