import assert from "node:assert/strict";
import { build } from "esbuild";

const bundle = await build({ entryPoints: ["src/lib/sidebar-view.ts"], bundle: true, write: false, format: "esm", platform: "node" });
const { buildChatSidebarView, chatSidebarActivity } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`);
const threadBundle = await build({ entryPoints: ["src/lib/chat-thread.ts"], bundle: true, write: false, format: "esm", platform: "node" });
const { filterPersistableChats } = await import(`data:text/javascript;base64,${Buffer.from(threadBundle.outputFiles[0].text).toString("base64")}`);

const baseResponse = {
  ok: true,
  plan: { nextStep: "done" },
};
const chat = (response, extra = {}) => ({
  id: "chat-1",
  sessionId: "session-1",
  title: "Fixture",
  projectPath: "D:/Fixture",
  items: [
    { id: "user-1", type: "user", text: "inspect" },
    { id: "agent-1", type: "agent", response, createdAt: "2026-09-24T00:00:10.000Z" },
  ],
  ...extra,
});

assert.equal(chatSidebarActivity(chat(baseResponse)), "completed", "done nextStep marks a normal response complete even without response.status");
assert.equal(chatSidebarActivity(chat({ ...baseResponse, plan: { nextStep: "completed" }, status: undefined })), "completed");
assert.equal(chatSidebarActivity(chat(baseResponse), "chat-1"), undefined, "the currently viewed chat has no completion dot");
assert.equal(chatSidebarActivity(chat(baseResponse, { lastViewedAt: "2026-09-24T00:00:10.000Z" })), undefined, "a viewed completion stays clear");
assert.equal(chatSidebarActivity(chat(baseResponse, { lastViewedAt: "2026-09-24T00:00:09.000Z" })), "completed", "a newer background completion lights the dot again");
const idFallbackChat = chat(baseResponse, { id: "id-only", lastViewedAt: "agent-id" });
idFallbackChat.items[1] = { ...idFallbackChat.items[1], id: "agent-id", createdAt: undefined };
assert.equal(chatSidebarActivity(idFallbackChat), undefined, "an id fallback marker equal to lastViewedAt stays read without a parseable timestamp");
const sidebarChats = [chat(baseResponse)];
assert.equal(buildChatSidebarView(sidebarChats, "en", (value) => value, 0, "chat-1").activityByChat.has("chat-1"), false, "the visible chat is read while chat view is active");
assert.equal(buildChatSidebarView(sidebarChats, "en", (value) => value, 0, "").activityByChat.get("chat-1"), "completed", "leaving chat view does not silently clear a background completion");
const persisted = filterPersistableChats([chat(baseResponse, { lastViewedAt: "2026-09-24T00:00:10.000Z" })])[0];
assert.equal(persisted.lastViewedAt, "2026-09-24T00:00:10.000Z", "lastViewedAt survives the existing persistence projection");
for (const nextStep of ["needs_user_action", "approval_pending", "cancelled", "failed", "completion_unverified"]) {
  assert.equal(chatSidebarActivity(chat({ ...baseResponse, plan: { nextStep } })), undefined, `${nextStep} must not create a completed sidebar marker`);
}
assert.equal(chatSidebarActivity(chat({ ...baseResponse, ok: false })), undefined, "an explicitly failed response cannot be marked complete");
const running = chat(baseResponse);
running.items.push({ id: "stream", type: "streaming", clientTurnId: "next-turn", text: "" });
assert.equal(chatSidebarActivity(running), "running");
const nextTurn = chat(baseResponse);
nextTurn.items.push({ id: "user-2", type: "user", text: "next question" });
assert.equal(chatSidebarActivity(nextTurn), undefined, "a new unanswered user turn clears the old completion marker");
console.log("sidebar view activity contract: ok");
