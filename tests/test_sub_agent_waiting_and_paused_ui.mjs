import assert from "node:assert/strict";
import Module from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React from "react";
    import { renderToStaticMarkup } from "react-dom/server";
    import { ConversationCard } from "./src/components/chat/conversation-card";
    import { SubAgentConversation } from "./src/components/subagents/sub-agent-conversation";
    import { mergeConversationTimelineItems, projectRuntimeResponseForDisplay } from "./src/lib/chat-timeline-presentation";
    const plan = { planner: "llm", reply: "", summary: "", nextStep: "waiting_for_tool", reason: "sub_agent_pending" };
    const card = (nextPlan) => renderToStaticMarkup(<ConversationCard item={{
      id: "parent", type: "agent", response: { plan: nextPlan },
    }} />);
    export const childWait = card(plan);
    const event = (id, phase, status, summary) => ({ id: String(id), sequence: id, timestamp: "2026-01-01T00:00:00Z",
      kind: "assistant", payload: { phase, status, summary } });
    const commentary = event(1, "commentary", "running", "Waiting for Unity to reload.");
    const wait = event(2, "final_answer", "waiting_for_tool", "internal process polling instruction");
    export const shellWait = ["item", "response"].map(source => {
      const item = { id: "parent", type: "agent",
        timeline: source === "item" ? [commentary, wait] : [],
        response: { plan: { nextStep: "waiting_for_tool", reply: wait.payload.summary, summary: "internal shell summary",
          completionGate: { status: "running", reason: "action_not_terminal" } },
          timeline: source === "response" ? [commentary, wait] : [] } };
      const merged = mergeConversationTimelineItems([item])[0];
      return { card: renderToStaticMarkup(<ConversationCard item={merged} />),
        events: projectRuntimeResponseForDisplay(merged.response, key => key).timeline };
    });
    export const humanWait = card({ ...plan, reply: "Which avatar should I inspect?", nextStep: "needs_user_action", reason: "question_pending" });
    export const restoredQuestion = renderToStaticMarkup(<ConversationCard item={{
      id: "restored", type: "agent", response: { status: "blocked", continuationSource: "foreground_interrupted",
        plan: { ...plan, nextStep: "needs_user_action", reply: "Choose an avatar", taskCompletion: {status:"waiting_user"} } },
    }} />);
    export const paused = renderToStaticMarkup(<SubAgentConversation endpoint="" turns={[{
      id: "child", childSessionId: "session", status: "paused", reason: "model_turn_budget_exhausted",
      task: "Inspect project", summary: "Short preview", result: { finalReport: "Verified two bindings. Check remaining outfit." },
    }]} />);
  ` },
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic",
  external: ["react", "react-dom/server", "react/jsx-runtime", "i18next", "react-i18next"],
});
const filename = fileURLToPath(new URL("./sub-agent-waiting-fixture.cjs", import.meta.url));
const compiled = new Module(filename);
compiled.paths = Module._nodeModulePaths(process.cwd());
compiled._compile(bundle.outputFiles[0].text, filename);
const { childWait, shellWait, humanWait, paused, restoredQuestion } = compiled.exports;
for (const {card, events} of shellWait) {
  assert.doesNotMatch(card, /internal process polling instruction|internal shell summary/);
  assert.deepEqual(events.map(e => e.payload.summary), ["Waiting for Unity to reload."]);
  assert.match(card, /toolInBackground/);
}
assert.match(childWait, /data-agent-waiting-for-subagent/);
assert.doesNotMatch(humanWait, /data-agent-waiting-for-subagent/);
assert.match(humanWait, /Which avatar should I inspect/);
assert.match(restoredQuestion, /Choose an avatar/);
assert.doesNotMatch(restoredQuestion, /data-agent-transport-interrupted/);
assert.match(paused, /data-subagent-paused-report/);
assert.match(paused, /Verified two bindings\. Check remaining outfit\./);
assert.doesNotMatch(paused, /Short preview/);
console.log("sub-agent waits and paused report: ok");
