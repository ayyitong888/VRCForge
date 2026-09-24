import assert from "node:assert/strict";
import { build } from "esbuild";

const fakeTauriModule = "tauri-core-fake";
const bundle = await build({
  entryPoints: ["src/lib/api/agent-runtime.ts"],
  bundle: true,
  write: false,
  format: "esm",
  platform: "node",
  plugins: [{
    name: "fake-tauri-core",
    setup(plugin) {
      plugin.onResolve({ filter: /^@tauri-apps\/api\/core$/ }, () => ({ path: fakeTauriModule, namespace: "fake" }));
      plugin.onLoad({ filter: /^tauri-core-fake$/, namespace: "fake" }, () => ({
        contents: "export const invoke = (command, args) => globalThis.__fakeTauriInvoke(command, args);",
        loader: "js",
      }));
    },
  }],
});
const { sendAgentMessage } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`,
);

globalThis.window = { __TAURI_INTERNALS__: {} };
const calls = [];
const sessionId = "session-entry";
const clientTurnId = "turn-entry";
const response = {
  ok: true,
  sessionId,
  session_id: sessionId,
  clientTurnId,
  turnId: "runtime-entry",
  turn_id: "runtime-entry",
  observe: { result: "kept" },
  plan: { summary: "done", planner: "runtime", shellNeeded: false, nextStep: "done" },
  contextCompaction: { summary: "compacted history" },
  consumedSteerInputIds: ["steer-1"],
  deferredSteerFollowups: [{ inputId: "followup-1", status: "deferred" }],
  steps: [{ kind: "tool", result: { value: "kept" } }],
};
const states = [
  { ok: true, sessionId, clientTurnId, status: "running" },
  { ok: true, sessionId, clientTurnId, status: "completed", response },
];
globalThis.__fakeTauriInvoke = async (command, args) => {
  calls.push({ command, args });
  if (command === "send_agent_message") {
    throw "VRCForge runtime is not reachable at http://127.0.0.1:8757: timeout";
  }
  assert.equal(command, "fetch_agent_turn_response");
  return states[calls.filter((call) => call.command === "fetch_agent_turn_response").length - 1];
};
const recovered = await sendAgentMessage("http://127.0.0.1:8757", "continue", sessionId, [], "desktop-agent", { clientTurnId });
assert.equal(calls.filter((call) => call.command === "send_agent_message").length, 1);
assert.equal(calls.filter((call) => call.command === "fetch_agent_turn_response").length, 2);
assert.deepEqual(recovered, response, "entry recovery returns the complete response unchanged");
assert.deepEqual(calls[1].args, { request: { sessionId, clientTurnId, timeoutMs: 10000 } });

for (const error of [
  { status: 400, message: "HTTP 400" },
  { status: 0, message: "provider returned timeout in its response" },
]) {
  calls.length = 0;
  globalThis.__fakeTauriInvoke = async (command) => {
    calls.push(command);
    const cause = new Error(error.message);
    cause.status = error.status;
    throw cause;
  };
  await assert.rejects(sendAgentMessage("http://127.0.0.1:8757", "continue", sessionId, [], "desktop-agent", { clientTurnId }));
  assert.deepEqual(calls, ["send_agent_message"], "non-transport/provider failures must not GET recover");
}

console.log("agent runtime recovery entry regression passed");
