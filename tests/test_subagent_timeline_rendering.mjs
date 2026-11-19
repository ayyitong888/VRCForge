import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
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
    import { buildTimelinePresentation, mergeConversationTimelineItems } from "./src/lib/chat-timeline-presentation";
    import { projectSubAgentRegistry } from "./src/lib/subagent-ui";
    import { SubAgentPanel } from "./src/components/subagents/sub-agent-panel";
    i18n.use(initReactI18next).init({lng:"en",resources:{en:{translation:{}}},interpolation:{escapeValue:false}});
    export function projectRaw(chat) {
      return mergeConversationTimelineItems(chat.items).map(item => item.type === "agent"
        ? buildTimelinePresentation(item.timeline || item.response.timeline).entries
        : item.type === "timeline_event" ? [{ type: "standalone", event: item.event }] : []);
    }
    export function renderLifecycle(items, activeSubAgentEvents, { open = true, last = true, streaming = false } = {}) {
      const item = streaming ? { id: "stream", type: "streaming", text: "", timeline: items }
        : { id: "parent", type: "agent", response: { timeline: items, plan: {
          planner: "llm", reply: "", summary: "", nextStep: "waiting_for_tool", reason: "sub_agent_pending",
        } } };
      return renderToStaticMarkup(<ConversationCard item={item} activeSubAgentEvents={activeSubAgentEvents}
        onOpenSubAgent={()=>{}} sharedProcess={{open, last, first:false, live:streaming, failures:0, onOpenChange:()=>{}}} />);
    }
    export function renderRegistry(tasks) {
      const registry = projectSubAgentRegistry(tasks, "Child");
      const parents = tasks.map(task => ({id:task.parentTurnId, type:"agent", response:{
        sessionId:task.parentSessionId, turnId:task.parentTurnId, clientTurnId:task.parentClientTurnId, plan:{},
      }}));
      return mergeConversationTimelineItems(parents, registry.items).map((parent, index) =>
        renderLifecycle(parent.response.timeline, registry.activeEvents, { last:index === parents.length - 1 })).join("");
    }
    export function run() {
      const time=(second)=>"2026-09-17T00:00:"+String(second).padStart(2,"0")+"Z";
      const lifecycle=(id,revision,second,status,label)=>({id:"subagent-event-"+id+"-"+revision,sequence:revision,timestamp:time(second),kind:"subagent",
        sessionId:"session",turnId:"turn",clientTurnId:"request",payload:{label,summary:status==="completed"?"Child result sentinel":"",status}});
      const tool=(id,second,name,kind="tool_call",status="started",actionId=id)=>({id,sequence:second,timestamp:time(second),kind,
        payload:{tool:name,status,actionId,summary:status==="failed"?"Resume error sentinel":""}});
      const created=lifecycle("task-a",1,2,"created","Subagent sentinel");
      const started=lifecycle("task-a",2,3,"started","Subagent sentinel");
      const another=lifecycle("task-b",1,4,"started","Another worker");
      const event=lifecycle("task-a",3,6,"completed","Subagent sentinel");
      const stopped=lifecycle("task-b",2,7,"cancelled","Another worker");
      const read=tool("read",1,"vrcforge_read_text_file");
      const dispatch=tool("delegate",2,"vrcforge_delegate_subagent");
      const dispatchResult=tool("delegate-rcpt",4,"vrcforge_delegate_subagent","tool_result","ok","delegate");
      const list=tool("list",5,"vrcforge_list_directory");
      const resume=tool("resume",8,"vrcforge_resume_subagent");
      const failure=tool("resume-rcpt",9,"vrcforge_resume_subagent","tool_result","failed","resume");
      const final={id:"final",sequence:10,timestamp:time(10),kind:"assistant",payload:{summary:"User final answer",status:"done",phase:"final_answer"}};
      const response={sessionId:"session",turnId:"turn",clientTurnId:"request",
        plan:{reply:"User final answer",summary:"Done",planner:"llm",shellNeeded:false,nextStep:"done"},
        timeline:[read,dispatch,created,started,another,dispatchResult,list,event,stopped,resume,failure,final]};
      const standalone={id:"lifecycle",type:"timeline_event",createdAt:event.timestamp,event};
      const agent={id:"agent",type:"agent",response};
      const task={id:"task",displayName:"Subagent sentinel",role:"explorer",task:"Read source",summary:"Child result sentinel",status:"completed"};
      const render=(item,open=false)=><ConversationCard key={item.id} item={item} onOpenSubAgent={()=>{}} sharedProcess={open?{open:true,first:false,last:true,live:false,failures:0,onOpenChange:()=>{}}:undefined}/>;
      const before=JSON.stringify({standalone,agent});
      const waiting={...agent,response:{...response,timeline:[read,dispatch,dispatchResult]}};
      const continuation={...agent,id:"continuation",response:{...response,turnId:"resume",clientTurnId:"resumed",timeline:[resume,failure,final]}};
      const merged=mergeConversationTimelineItems([waiting,standalone,continuation]);
      const outputs={
        standalone:renderToStaticMarkup(render(standalone)),
        legacy:renderToStaticMarkup(render({id:"legacy",type:"subagent",task})),
        completed:renderToStaticMarkup(render(agent)),
        streaming:renderToStaticMarkup(render({id:"stream",type:"streaming",clientTurnId:"turn",text:"",timeline:[event]})),
        streamingMixed:renderToStaticMarkup(render({id:"stream-mixed",type:"streaming",clientTurnId:"request",text:"",timeline:response.timeline})),
        completedOpen:renderToStaticMarkup(render(agent,true)),
        entries:buildTimelinePresentation(response.timeline).entries,
        storedOwner:merged.find(item=>item.response?.timeline?.some(e=>e.id===event.id))?.id,
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
const activity = result.entries.filter(entry => entry.type === "subagent").map(entry => entry.event);
const [firstStart, secondStart, firstTerminal] = activity;
const parallelStarts = [firstStart, secondStart].map(event => ({
  ...event, payload: { ...event.payload, dispatchBatchId: "same-dispatch" },
}));
for (const streaming of [false, true]) {
  const render = events => module.exports.renderLifecycle(events, new Map(), { streaming });
  const grouped = render([...parallelStarts, firstTerminal]);
  assert.match(grouped, /data-subagent-start-group/);
  assert.deepEqual([...grouped.matchAll(/<button[^>]*data-subagent-activity="([^"]+)"/g)].map(match => match[1]), ["task-a", "task-b"]);
  assert.doesNotMatch(render([firstStart, secondStart]), /data-subagent-start-group/);
  assert.doesNotMatch(render([parallelStarts[0], { ...parallelStarts[1], payload: { ...parallelStarts[1].payload, dispatchBatchId: "later" } }]), /data-subagent-start-group/);
  assert.doesNotMatch(render([parallelStarts[0], { ...firstTerminal, timestamp: parallelStarts[0].timestamp }, parallelStarts[1]]), /data-subagent-start-group/);
}
const retryTimeline = [firstStart, { ...firstTerminal, payload: { ...firstTerminal.payload, status: "failed" } },
  { ...secondStart, timestamp: "2026-09-17T00:01:00Z", payload: { ...secondStart.payload, label: firstStart.payload.label } }];
const currentRetry = new Map([[secondStart.id, "running"]]);
for (const streaming of [false, true]) {
  const html = module.exports.renderLifecycle(retryTimeline, currentRetry, {streaming});
  assert.equal((html.match(/subagent\.statusRunningOne/g) || []).length, 1, "only the currently executing attempt says running");
  assert.equal((html.match(/subagent\.statusStarted/g) || []).length, 1, "the failed attempt retains its start in the history");
  assert.doesNotMatch(html, /data-agent-waiting-for-subagent/, "the visible active attempt already explains the wait");
}
const withHistoryOnly = module.exports.renderLifecycle(retryTimeline, new Map());
assert.match(withHistoryOnly, /data-agent-waiting-for-subagent/, "a waiting parent is visible when all child rows are historical");
assert.doesNotMatch(withHistoryOnly, /subagent\.statusRunningOne/);
assert.match(module.exports.renderLifecycle(retryTimeline, currentRetry, {open:false}), /data-agent-waiting-for-subagent/,
  "a collapsed process still shows the current parent's wait");
assert.doesNotMatch(module.exports.renderLifecycle(retryTimeline, new Map(), {last:false}), /data-agent-waiting-for-subagent/,
  "superseded parent receipts stay historical");
const pausedAttempt = module.exports.renderRegistry([{id:"paused-child", parentSessionId:"session", parentTurnId:"paused-parent",
  parentClientTurnId:"request", status:"paused", events:[{event:"started", revision:1}, {event:"paused", revision:2}]}]);
assert.match(pausedAttempt, /subagent\.statusPaused/);
assert.doesNotMatch(pausedAttempt, /subagent\.statusRunningOne/);
assert.doesNotMatch(result.completed, /data-subagent-activity/, "the historical process is collapsed");
assert.match(result.completed, /data-agent-turn-process-group/);
assert.match(result.completed, /User final answer/);
assert.equal(result.standalone, "", "standalone lifecycle stays in the sidebar until an owning process is found");
assert.equal(result.legacy, "", "legacy adoption records remain available via the sidebar");
for (const route of ["completedOpen", "streamingMixed"]) {
  assert.deepEqual([...result[route].matchAll(/data-subagent-status="([^"]+)"/g)].map((match) => match[1]),
    ["started", "started", "completed", "cancelled"], route);
  assert.equal((result[route].match(/data-agent-tool-call-group/g) || []).length, 2, route);
  assert.ok(result[route].indexOf('data-subagent-status="started"') < result[route].indexOf('agent.timeline.listDirectory'), route);
  assert.ok(result[route].indexOf('agent.timeline.listDirectory') < result[route].indexOf('data-subagent-status="completed"'), route);
}
assert.deepEqual(result.entries.map(({ type, invocation }) => invocation?.status || type),
  ["batch", "subagent", "subagent", "batch", "subagent", "subagent", "failed", "assistant"]);
assert.equal(result.entries.find(({ type }) => type === "subagent_dispatch")?.invocation.summary, "Resume error sentinel");
assert.match(result.streaming, /data-subagent-status="completed"/);
assert.match(result.sidebar, /data-vrcforge-open-subagent-surface/);
assert.equal(result.unchanged, true);
assert.equal(result.storedEvents, 1, "presentation preserves stored lifecycle evidence");
assert.equal(result.storedOwner, "agent", "post-receipt completion belongs to the waiting execution, not its continuation");
const replayPath = process.argv[2];
if (replayPath === "--retry-registry") {
  const [, , , file, original, retry] = process.argv;
  const revisions = readFileSync(file, "utf8").split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line))
    .filter(row => row.taskId === original || row.taskId === retry);
  const tasks = new Map();
  for (const { task, taskId, timestamp, revision, event } of revisions) {
    const history = tasks.get(taskId)?.events || [];
    history.push({ taskId, timestamp, revision, event });
    tasks.set(taskId, { id: taskId, parentSessionId: task.parentSessionId, parentTurnId: task.parentTurnId,
      parentClientTurnId: task.parentClientTurnId, displayName: task.displayName, status: task.status, revision, events: history });
    if (taskId !== retry || !["started", "completed"].includes(event)) continue;
    const html = module.exports.renderRegistry([...tasks.values()]);
    assert.equal((html.match(/subagent\.statusRunningOne/g) || []).length, event === "started" ? 1 : 0, event);
    assert.equal((html.match(/subagent\.statusStarted/g) || []).length, event === "started" ? 1 : 2, event);
    assert.equal((html.match(/subagent\.statusFailed/g) || []).length, 1, event);
    assert.equal((html.match(/subagent\.statusCompleted/g) || []).length, event === "started" ? 0 : 1, event);
  }
  assert.equal(tasks.get(original)?.status, "failed");
  assert.equal(tasks.get(retry)?.status, "completed");
} else if (replayPath) {
  const { chat, task } = JSON.parse(readFileSync(replayPath, "utf8"));
  const parentAndContinuation = chat.items.filter((item) => item.event?.clientTurnId === task.parentClientTurnId
    || item.response?.clientTurnId?.startsWith(task.parentClientTurnId));
  const entries = module.exports.projectRaw({ items: parentAndContinuation }).flat();
  const activity = entries.filter(({ type }) => type === "subagent");
  const completion = activity.find(({ event }) => event.payload.status === "completed");
  assert.deepEqual(activity.map(({ event }) => event.payload.status), ["started", "completed"]);
  assert.ok(completion.event.payload.summary.includes("14 UnityPackage"));
  assert.equal(entries.find(({ type }) => type === "subagent_dispatch")?.invocation.summary, "this sub-agent role does not support history resume.");
  assert.equal(entries.filter(({ type, invocations }) => type === "batch" && invocations.some(({ label }) => /vrcforge_(delegate|resume)_subagent/.test(label))).length, 0);
  assert.ok(entries.indexOf(completion) < entries.findIndex(({ type, invocation }) => type === "subagent_dispatch" && invocation.status === "failed"));
}
console.log("subagent timeline rendering routes: ok");
