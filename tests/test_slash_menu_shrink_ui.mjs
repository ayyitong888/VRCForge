import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const read = (path) => readFile(resolve(root, path), "utf8");

const [app, workspace, sender, en, zhCn, zhTw, ja] = await Promise.all([
  read("src/App.tsx"),
  read("src/components/chat/chat-workspace.tsx"),
  read("src/components/chat/session-handoff-send.tsx"),
  read("src/locales/en-US.json"),
  read("src/locales/zh-CN.json"),
  read("src/locales/zh-TW.json"),
  read("src/locales/ja-JP.json"),
]);

// The slash palette is a fixed set of agent-generic commands only.
assert.match(app, /const slashCommands = useMemo\(\(\) => \{/);
assert.match(app, /name: "compact", title: t\("chat\.slashCompact"\)/);
assert.match(app, /name: "goal", title: t\("chat\.slashGoal"\)/);
assert.match(app, /name: "memory", title: t\("chat\.slashMemory"\)/);
assert.match(app, /name: "delegate", title: t\("chat\.slashDelegate"\)/);
assert.doesNotMatch(app, /for \(const skill of skills\)/, "skills must not flood the user slash palette");
assert.doesNotMatch(app, /list\.push\(\{ name: skill\.name/, "skills must not be listed as user slash commands");

// Handoff replaces the removed always-on panel with a compact slash entry.
assert.match(app, /name: "handoff", title: t\("chat\.slashHandoff"\)/);
assert.match(app, /message === "\/handoff" \|\| message\.startsWith\("\/handoff "\)/);
assert.match(app, /setHandoffSendOpen\(\(current\) => !current\)/);
assert.match(app, /handoffSendOpen=\{handoffSendOpen\}/);
assert.match(app, /onHandoffSendOpenChange=\{setHandoffSendOpen\}/);

// The always-on send-handoff card above the chat is hidden by default and
// renders only when opened through the slash command.
assert.match(workspace, /handoffSendOpen \? \([\s\S]*?<SessionHandoffSend/);
assert.match(workspace, /onOpenChange=\{onHandoffSendOpenChange\}/);
assert.match(sender, /open=\{open\}/);
assert.match(sender, /onToggle=\{\(event\) => onOpenChange\?\.\(event\.currentTarget\.open\)\}/);

for (const locale of [en, zhCn, zhTw, ja]) {
  const parsed = JSON.parse(locale);
  assert.ok(typeof parsed.chat.slashHandoff === "string" && parsed.chat.slashHandoff.length > 0, "chat.slashHandoff must exist in every locale");
}

console.log("slash menu shrink contract: ok");
