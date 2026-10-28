import assert from "node:assert/strict";
import Module from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const compiled = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
      import React from "react";
      import { renderToStaticMarkup } from "react-dom/server";
      import i18n from "./src/i18n";
      import { initReactI18next } from "react-i18next";
      import { groupSubAgentThreads } from "./src/lib/subagent-threads";
      import { SubAgentPanel } from "./src/components/subagents/sub-agent-panel";

      const tasks = [
        { id:"follow-2", childSessionId:"child-a", resumeOf:"follow-1", displayName:"Research", role:"explorer", task:"Continue review", status:"running" },
        { id:"follow-1", childSessionId:"child-a", resumeOf:"root", displayName:"Research", role:"explorer", task:"Follow up", status:"interrupted" },
        { id:"root", childSessionId:"child-a", displayName:"Research", role:"explorer", task:"Review source", status:"failed", error:"Provider stopped unexpectedly" },
        { id:"failed-only", childSessionId:"child-failed", displayName:"Failed task", role:"reviewer", task:"Inspect failed task", status:"failed", error:"No result was produced" },
        { id:"cancelled-only", childSessionId:"child-cancelled", displayName:"Cancelled task", role:"reviewer", task:"Inspect cancelled task", status:"cancelled" },
        { id:"interrupted-only", childSessionId:"child-interrupted", displayName:"Interrupted task", role:"reviewer", task:"Inspect interrupted task", status:"interrupted" },
        { id:"retry", childSessionId:"child-retry", retryOf:"root", displayName:"Retry", role:"explorer", task:"Start a fresh retry", status:"completed" },
        { id:"late-shallow", childSessionId:"child-branch", resumeOf:"branch-root", createdAt:"2026-06-04T00:00:00Z", displayName:"Branched resume", role:"explorer", task:"Latest branch", status:"completed" },
        { id:"branch-follow-2", childSessionId:"child-branch", resumeOf:"branch-follow-1", createdAt:"2026-06-03T00:00:00Z", updatedAt:"2026-12-01T00:00:00Z", displayName:"Branched resume", role:"explorer", task:"Earlier deep turn", status:"completed" },
        { id:"branch-follow-1", childSessionId:"child-branch", resumeOf:"branch-root", startedAt:"2026-06-02T00:00:00Z", displayName:"Branched resume", role:"explorer", task:"Middle turn", status:"completed" },
        { id:"branch-root", childSessionId:"child-branch", createdAt:"2026-06-01T00:00:00Z", displayName:"Branched resume", role:"explorer", task:"First turn", status:"completed" },
      ];

      export async function run() {
        await i18n.use(initReactI18next).init({
          lng:"en-US",
          resources:{"en-US":{translation:{
            workspace:{subAgents:"Subagents"},
            subagent:{active:"Active",done:"Done",statusRunning:"{{count}} active",statusDone:"{{count}} done",inspect:"Inspect",noTasks:"No tasks",statusRunningOne:"Running",statusInterrupted:"Interrupted",statusFailed:"Failed",statusCancelled:"Cancelled",statusCompleted:"Completed"}
          }}},
          interpolation:{escapeValue:false}
        });
        const groups = groupSubAgentThreads(tasks);
        const markup = renderToStaticMarkup(<SubAgentPanel tasks={tasks} loading={false} error="Could not refresh tasks" onOpen={()=>{}}/>);
        return {
          groups: groups.map(({id,task,turns})=>({id,taskId:task.id,turnIds:turns.map(turn=>turn.id)})),
          markup,
        };
      }
    `,
  },
  bundle: true,
  write: false,
  platform: "node",
  format: "cjs",
  external: ["react", "react-dom/server", "react/jsx-runtime", "i18next", "react-i18next"],
  jsx: "automatic",
});

const filename = fileURLToPath(new URL("./subagent-threads-fixture.cjs", import.meta.url));
const fixture = new Module(filename);
fixture.paths = Module._nodeModulePaths(process.cwd());
fixture._compile(compiled.outputFiles[0].text, filename);
const { groups, markup } = await fixture.exports.run();

assert.deepEqual(groups[0], {
  id: "child-a",
  taskId: "follow-2",
  turnIds: ["root", "follow-1", "follow-2"],
}, "resumeOf links determine conversation order even when input is newest first and has no timestamps");
assert.deepEqual(groups.find((group) => group.id === "child-branch"), {
  id: "child-branch",
  taskId: "late-shallow",
  turnIds: ["branch-root", "branch-follow-1", "branch-follow-2", "late-shallow"],
}, "complete creation times order resume branches globally and an older tip's later update does not make it newest");
assert.ok(groups.some((group) => group.id === "child-retry" && group.taskId === "retry"), "retry uses its distinct child session");
assert.ok(groups.some((group) => group.id === "child-failed"), "failed-only child remains a thread");
assert.match(markup, /data-vrcforge-open-subagent-surface/);
for (const id of ["child-a", "child-failed", "child-cancelled", "child-interrupted", "child-retry"]) {
  assert.match(markup, new RegExp(`data-vrcforge-open-subagent-thread="${id}"`), `${id} remains openable from the sidebar`);
}
assert.doesNotMatch(markup, /Provider stopped unexpectedly/, "a resumed thread does not advertise an earlier failure as its current state");
assert.match(markup, /No result was produced/);
assert.match(markup, /Could not refresh tasks/);
console.log("subagent thread grouping and sidebar states: ok");
