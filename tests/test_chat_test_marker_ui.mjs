import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const menu = fs.readFileSync(path.join(root, "src/components/sidebar/sidebar-menus.tsx"), "utf8");
const sessions = fs.readFileSync(path.join(root, "src/hooks/use-chat-sessions.ts"), "utf8");
const sidebar = fs.readFileSync(path.join(root, "src/components/sidebar/sidebar.tsx"), "utf8");
const appSidebar = fs.readFileSync(path.join(root, "src/components/sidebar/app-sidebar.tsx"), "utf8");
const runController = fs.readFileSync(path.join(root, "src/hooks/use-chat-run-controller.ts"), "utf8");
const localeFiles = ["en-US.json", "zh-CN.json", "zh-TW.json", "ja-JP.json"];

assert.match(menu, /onToggleTestChat: \(chatId: string\) => void/);
assert.match(menu, /chat\.isTestChat\s*\?/);
assert.match(menu, /markTestChat/);
assert.match(menu, /unmarkTestChat/);
assert.match(sessions, /function toggleTestChat\(chatId: string\)/);
assert.match(sessions, /isTestChat: !chat\.isTestChat/);
assert.match(sessions, /isTestChat: chat\.isTestChat === true/);
assert.match(sidebar, /data-vrcforge-chat-id/);
assert.match(sidebar, /isTestChat \?/);
assert.match(appSidebar, /chatSidebar\.testChats/);
assert.match(appSidebar, /sidebar\.testChats/);
for (const localeFile of localeFiles) {
  const locale = JSON.parse(fs.readFileSync(path.join(root, "src/locales", localeFile), "utf8"));
  assert.equal(typeof locale.sidebar?.testChats, "string");
  assert.equal(typeof locale.contextMenu?.markTestChat, "string");
  assert.equal(typeof locale.contextMenu?.unmarkTestChat, "string");
  assert.equal(typeof locale.contextMenu?.testChat, "string");
}
// Test classification is display/storage metadata only; it must not enter the
// provider request construction path.
assert.doesNotMatch(runController, /isTestChat/);

// The request helper uses the browser global even when running the isolated
// payload contract check under Node. Keep this shim local to the test; it does
// not alter the production request path.
globalThis.window = {
  setTimeout: globalThis.setTimeout,
  clearTimeout: globalThis.clearTimeout,
};

const runtimeBundle = await build({ entryPoints: ["src/lib/api/agent-runtime.ts"], bundle: true, write: false, format: "esm", platform: "node" });
const { sendAgentMessage } = await import(`data:text/javascript;base64,${Buffer.from(runtimeBundle.outputFiles[0].text).toString("base64")}`);
let sentPayload;
globalThis.fetch = async (_url, options) => {
  sentPayload = JSON.parse(options.body);
  return new Response(JSON.stringify({ ok: true, status: "completed" }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
};
await sendAgentMessage("http://127.0.0.1:1", "Please inspect this avatar.", undefined, [], undefined, {
  chatId: "acceptance-chat",
  projectPath: "D:/AvatarProject",
  projectType: "unity",
});
assert.equal(sentPayload.projectPath, "D:/AvatarProject");
assert.equal(sentPayload.message, "Please inspect this avatar.");
assert.equal(Object.hasOwn(sentPayload, "isTestChat"), false);

console.log("explicit test-chat marker UI/storage wiring passed");
