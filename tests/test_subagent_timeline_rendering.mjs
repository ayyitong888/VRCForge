import assert from "node:assert/strict";
import Module from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

// Render the real components without a browser, backend, provider or app process.
const compiled = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React from "react";
    import { renderToStaticMarkup } from "react-dom/server";
    import i18n from "./src/i18n";
    import { initReactI18next } from "react-i18next";
    import { ConversationCard } from "./src/components/chat/conversation-card";
    import { buildDurableTimelineRows } from "./src/components/chat/conversation-timeline";
    import { mergeConversationTimelineItems } from "./src/lib/chat-timeline-presentation";
    import { SubAgentPanel } from "./src/components/subagents/sub-agent-panel";
    i18n.use(initReactI18next).init({lng:"en",resources:{en:{translation:{}}},interpolation:{escapeValue:false}});
    export function run() {
      const event={id:"subagent-event-task-1",sequence:1,timestamp:"2026-09-17T00:00:02Z",kind:"subagent",payload:{label:"Subagent sentinel",summary:"Child result sentinel",status:"completed",subagentStatus:"completed"}};
      const tool={id:"tool",sequence:0,timestamp:"2026-09-17T00:00:01Z",kind:"tool_call",payload:{tool:"vrcforge_read_text_file",status:"started"}};
      const final={id:"final",sequence:2,timestamp:"2026-09-17T00:00:03Z",kind:"assistant",payload:{summary:"User final answer",status:"done"}};
      const response={plan:{reply:"User final answer",summary:"Done",planner:"llm",shellNeeded:false,nextStep:"done"},timeline:[tool,event,final]};
      const standalone={id:"lifecycle",type:"timeline_event",createdAt:event.timestamp,event};
      const agent={id:"agent",type:"agent",response};
      const task={id:"task",displayName:"Subagent sentinel",role:"explorer",task:"Read source",summary:"Child result sentinel",status:"completed"};
      const render=(item)=><ConversationCard key={item.id} item={item}/>;
      const before=JSON.stringify({standalone,agent});
      const merged=mergeConversationTimelineItems([standalone,agent]);
      const outputs={
        durable:renderToStaticMarkup(<>{buildDurableTimelineRows([event])}</>),
        standalone:renderToStaticMarkup(render(standalone)),
        legacy:renderToStaticMarkup(render({id:"legacy",type:"subagent",task})),
        completed:renderToStaticMarkup(render(agent)),
        streaming:renderToStaticMarkup(render({id:"stream",type:"streaming",clientTurnId:"turn",text:"",timeline:[event]})),
        streamingMixed:renderToStaticMarkup(render({id:"stream-mixed",type:"streaming",clientTurnId:"turn",text:"",timeline:[tool,event]})),
        merged:renderToStaticMarkup(<>{merged.map(render)}</>),
        sidebar:renderToStaticMarkup(<SubAgentPanel tasks={[task]} loading={false} error="" onOpen={()=>{}}/>),
        unchanged:before===JSON.stringify({standalone,agent}),
        storedEvents:merged.flatMap(item=>item.response?.timeline||[]).filter(e=>e.kind==="subagent").length,
      };
      return outputs;
    }
  ` }, bundle: true, write: false, platform: "node", format: "cjs",
  external: ["react", "react-dom/server", "react/jsx-runtime", "i18next", "react-i18next"], jsx: "automatic",
});
const filename=fileURLToPath(new URL("./subagent-render-fixture.cjs",import.meta.url));
const module=new Module(filename);module.paths=Module._nodeModulePaths(process.cwd());
module._compile(compiled.outputFiles[0].text,filename);
const result=module.exports.run();
assert.equal(result.durable, "", "subagent-only durable events must not render execution rows or elapsed markers");
assert.equal(result.standalone, "", "unmerged persisted timeline_event must not render a center card");
assert.equal(result.legacy, "", "older persisted subagent cards use the sidebar too");
for(const route of ["completed","streaming","streamingMixed","merged"]){
  assert.doesNotMatch(result[route], /Subagent sentinel|Child result sentinel|data-vrcforge-timeline-event="subagent"/, route);
}
for (const route of ["completed", "streamingMixed", "merged"]) {
  assert.equal((result[route].match(/agent.callTool/g) || []).length, 1,
    route + " retains the real read tool without a second collapsed lifecycle tool row");
}
assert.equal((result.streaming.match(/agent.callTool/g) || []).length, 0);
assert.doesNotMatch(result.streaming, /data-vrcforge-live-runtime-timeline/, "no empty lifecycle wrapper");
assert.match(result.completed, /User final answer/);
assert.match(result.completed, /agent.callTool/);
assert.match(result.streamingMixed, /data-vrcforge-live-runtime-timeline/);
assert.match(result.sidebar, /data-vrcforge-open-subagent-surface/);
assert.equal(result.unchanged, true);
assert.equal(result.storedEvents, 1, "presentation must preserve stored lifecycle evidence");
console.log("subagent timeline rendering routes: ok");
