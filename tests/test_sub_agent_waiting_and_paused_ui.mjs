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
    const plan = { planner: "llm", reply: "", summary: "", nextStep: "waiting_for_tool", reason: "sub_agent_pending" };
    const card = (nextPlan) => renderToStaticMarkup(<ConversationCard item={{
      id: "parent", type: "agent", response: { plan: nextPlan },
    }} />);
    export const childWait = card(plan);
    export const humanWait = card({ ...plan, reply: "Which avatar should I inspect?", nextStep: "needs_user_action", reason: "question_pending" });
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
const { childWait, humanWait, paused } = compiled.exports;
assert.match(childWait, /data-agent-waiting-for-subagent/);
assert.doesNotMatch(humanWait, /data-agent-waiting-for-subagent/);
assert.match(humanWait, /Which avatar should I inspect/);
assert.match(paused, /data-subagent-paused-report/);
assert.match(paused, /Verified two bindings\. Check remaining outfit\./);
assert.doesNotMatch(paused, /Short preview/);
console.log("sub-agent waits and paused report: ok");
