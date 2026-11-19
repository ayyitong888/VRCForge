import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdir, readFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { build } from "esbuild";
import { build as buildCss } from "vite";
import { chromium } from "playwright";

const root = resolve(import.meta.dirname, "..");
const baseline = process.argv.includes("--baseline");
const evidence = process.env.VRCFORGE_SUBAGENT_EVIDENCE_DIR ? resolve(process.env.VRCFORGE_SUBAGENT_EVIDENCE_DIR) : null;
if (evidence) await mkdir(evidence, { recursive: true });
const tasks = [
  { id: "first", childSessionId: "child-a", role: "llm_readonly", displayName: "Wardrobe review", task: "Read the original wardrobe layers.", status: "completed", summary: "Short first summary.", createdAt: "2026-10-28T10:00:00Z", canResume: true },
  { id: "second", resumeOf: "first", childSessionId: "child-a", role: "llm_readonly", displayName: "Wardrobe review", task: "Compare the second outfit.", status: "completed", summary: "Short second summary.", createdAt: "2026-10-28T10:01:00Z", canResume: true },
  { id: "failed", childSessionId: "child-b", role: "llm_readonly", displayName: "Failed inspection", task: "Read the unavailable file.", status: "failed",
    error: "OSError: invalid handle at fixture-internal-path", summary: "OSError: invalid handle at fixture-internal-path",
    result: { finalReport: "The source didn’t include a test scene. I checked the material list.", providerRoute: { model: "unverified-route" } },
    createdAt: "2026-10-28T10:02:00Z" },
  { id: "live", childSessionId: "child-c", role: "outfit_package_inspection", handler: "llm_readonly", model: "requested-role-model", displayName: "Live inspection", task: "Inspect the remaining sleeve.", status: "running", createdAt: "2026-10-28T10:03:00Z" },
  { id: "fixed", role: "outfit_package_inspection", handler: "outfit_package_inspection", parentModel: "original-parent-model", displayName: "Package inspection", task: "Check the package type.",
    status: "completed", summary: "Recorded package summary.", result: { packageKind: "unitypackage", proposedNextAction: "Use the package." },
    events: [{ event: "fixture-completed" }], createdAt: "2026-10-28T10:04:00Z",
    startedAt: "2026-10-28T10:04:01Z", stoppedAt: "2026-10-28T10:04:04Z" },
];
const realSidecarPath = process.env.VRCFORGE_SUBAGENT_INSPECTION_SIDECAR;
const realReplayPath = process.env.VRCFORGE_SUBAGENT_INSPECTION_REPLAY;
const realSidecar = realSidecarPath
  ? JSON.parse(await readFile(realSidecarPath, "utf8"))
  : null;
let recordedInspection;
if (realReplayPath) {
  const { task } = JSON.parse(await readFile(realReplayPath, "utf8"));
  recordedInspection = task;
} else if (realSidecar) {
  const recovery = JSON.parse(await readFile(resolve(dirname(realSidecarPath), "../sub-agent-recovery.json"), "utf8"));
  const task = recovery.tasks.find((task) => task.id === realSidecar.taskId);
  assert(task, "recorded inspection task must match the result sidecar");
  recordedInspection = { ...task, summary: realSidecar.summary, result: realSidecar.result };
}
if (recordedInspection) tasks.push(recordedInspection);
const actualModelPath = process.env.VRCFORGE_SUBAGENT_MODEL_SIDECAR;
let realModelTask;
if (actualModelPath) {
  const sidecar = JSON.parse(await readFile(actualModelPath, "utf8"));
  const response = sidecar.result.rawResult;
  assert(response.model && response.clientTurnId === sidecar.taskId);
  realModelTask = { id: sidecar.taskId, childSessionId: response.sessionId || response.session_id, role: sidecar.result.role,
    displayName: "Recorded LLM review", task: "Review the existing project", status: "completed", result: sidecar.result,
    createdAt: response.startedAt, startedAt: response.startedAt, stoppedAt: response.completedAt };
  tasks.push(realModelTask);
}
const event = (sequence, kind, summary, extra = {}) => ({ id: `event-${sequence}`, sequence,
  timestamp: "2026-10-28T10:00:00Z", kind, payload: { summary, ...extra } });
const first = [
  event(0, "assistant", "Inspecting the original wardrobe.", { phase: "commentary", status: "done" }),
  event(1, "tool_call", "Inspect FX layers", { tool: "vrc_get_animator_layers", actionId: "inspect-1", status: "running" }),
  event(2, "tool_result", "Found three wardrobe layers.", { tool: "vrc_get_animator_layers", actionId: "inspect-1", status: "success" }),
  event(3, "assistant", "Checking the command output.", { phase: "commentary", status: "done" }),
  event(4, "command", "Get-Content layers.txt", { label: "Get-Content layers.txt", actionId: "shell-1", status: "running" }),
  event(5, "tool_result", "Shell command exited.", { actionId: "shell-1", status: "ok" }),
  ...Array.from({ length: 201 }, (_, i) => event(i + 6, "phase", "Working", { phase: "executing" })),
  event(207, "assistant", "Full original report beyond page 200.", { phase: "final_answer", status: "completed" }),
];
const firstResult = {
  ok: true, sessionId: "child-a", session_id: "child-a", clientTurnId: "first", turnId: "runtime-first", turn_id: "runtime-first",
  startedAt: "2026-10-28T10:00:00Z", completedAt: "2026-10-28T10:00:12Z", status: "completed", model: "fixture-reported-model",
  plan: { reply: "Full original report beyond page 200.", summary: "", planner: "llm", shellNeeded: false, nextStep: "done" },
  observe: {},
  steps: [
    { actionId: "inspect-1", result: { layers: 3 } },
    { actionId: "shell-1", result: { command: "Get-Content layers.txt", stdout: "SHELL-OUTPUT-FIXTURE", exitCode: 0 } },
  ],
};
const firstDetails = { ...tasks[0], result: { rawResult: firstResult } };
const inspectionTool = "vrcforge_inspect_outfit_package";
const inspectionOutput = recordedInspection?.result.inspection ?? {
  ok: true, source: { name: "Wardrobe package" }, summary: { unityPackageCount: 1 },
  unityPackages: [{ path: "wardrobe.unitypackage", pathnames: [{ path: "Assets/Wardrobe.prefab" }] }],
};
const second = [
  event(0, "tool_call", "Read package", { tool: inspectionTool, actionId: "inspect-package", status: "running" }),
  event(1, "tool_result", "Package inspected", { tool: inspectionTool, actionId: "inspect-package", status: "success" }),
  event(2, "assistant", "Full second-turn report.", { phase: "final_answer", status: "completed" }),
];
tasks[1].result = { rawResult: { ...firstResult, clientTurnId: "second", turnId: "runtime-second", turn_id: "runtime-second",
  startedAt: "2026-10-28T10:01:00Z", completedAt: "2026-10-28T10:01:01Z",
  plan: { ...firstResult.plan, reply: "Full second-turn report." },
  steps: [{ actionId: "inspect-package", result: inspectionOutput }] }, providerRoute: { model: "fixture-requested-model" } };
let liveReads = 0;
let failFirstTimeline = false;
let failFirstDetail = false;
const requests = [];
const plugins = baseline ? [{ name: "baseline-panel", setup(b) {
  b.onLoad({ filter: /sub-agent-panel\.tsx$/ }, () => ({
    contents: execFileSync("git", ["show", "HEAD:src/components/subagents/sub-agent-panel.tsx"], { cwd: root, encoding: "utf8" }), loader: "tsx",
  }));
} }] : [];
const bundled = await build({ stdin: { resolveDir: root, loader: "tsx", contents: `
  import React,{useState} from 'react'; import {createRoot} from 'react-dom/client';
  import i18n from 'i18next'; import {initReactI18next} from 'react-i18next';
  import messages from './src/locales/en-US.json'; import chinese from './src/locales/zh-CN.json';
  import {SubAgentPanel} from './src/components/subagents/sub-agent-panel';
  import {Composer} from './src/components/chat/composer';
  import {ConversationCard} from './src/components/chat/conversation-card';
  import {conversationItemText} from './src/lib/conversation-utils';
  import {SubAgentSidebar} from './src/components/subagents/sub-agent-sidebar';
  import {resumeSubAgent} from './src/lib/api/sub-agents';
  const initial=${JSON.stringify(tasks)},Sidebar=${baseline ? 'SubAgentPanel' : 'SubAgentSidebar'};
  const parentUser={id:"parent-initial",type:"user",text:initial[0].task,createdAt:initial[0].createdAt};
  const parentTurn={id:"parent-turn",type:"agent",response:{...${JSON.stringify(firstResult)},timeline:${JSON.stringify(first)}},elapsedSeconds:12};
  function Fixture(){ const [tasks,setTasks]=useState(initial),[selected,setSelected]=useState(null),[open,setOpen]=useState(false),[visible,setVisible]=useState(true),[draft,setDraft]=useState(''),[developer,setDeveloper]=useState(false);
    window.setDeveloper=setDeveloper;
    window.onlyFailed=()=>setTasks([initial[2]]);window.resetTasks=()=>setTasks(initial);window.setLanguage=lng=>i18n.changeLanguage(lng);window.manyDone=()=>setTasks(Array.from({length:24},(_,n)=>({...initial[1],id:"history-"+n,childSessionId:"history-child-"+n,displayName:"Completed review "+n,resumeOf:undefined})));window.cancelled=[];
    const noop=()=>{};const choose=id=>{setSelected(tasks.find(t=>t.id===(id||tasks[0]?.id)));setOpen(true);setVisible(true)};
    async function resume(id,question){const p=await resumeSubAgent(location.origin,id,{task:question});setTasks(v=>[...v,p.task]);setSelected(p.task);return true}
    return <main className="grid h-screen bg-background text-foreground" style={{gridTemplateColumns:"minmax(340px,0.6fr) minmax(0,1.4fr)"}}>
      <section className="flex min-h-0 flex-col border-r border-border bg-workspace" data-parent-conversation>
        <header className="h-12 shrink-0 border-b border-border px-4 py-3 text-sm font-medium">Main conversation</header>
        <div className="flex-1 space-y-6 overflow-auto p-5" data-parent-parity>
          <ConversationCard item={parentTurn} initialUserContent={<ConversationCard item={parentUser} />} />
        </div>
        <div className="p-4" data-main-composer><Composer input={draft} setInput={setDraft} sending={false} onSubmit={event=>event?.preventDefault()} onSwitchMode={noop} compact /></div>
      </section>
      <div className="min-h-0 overflow-hidden bg-workspace">{visible?<Sidebar endpoint={location.origin} tasks={tasks} selected={selected} roles={[{id:"llm_readonly",title:"Reviewer"}]}
        openThread={open} developerOptionsEnabled={developer} loading={false} error="" onOpen={choose} onSelect={choose} onCancel={id=>window.cancelled.push(id)} onResume={resume}
        onMerge={noop} onIgnoreNotification={noop} onCopyItem={item=>window.copiedReply=conversationItemText(item,i18n.t)} busyTaskIds={new Set()}
        onBack={()=>setOpen(false)} onClose={()=>setVisible(false)}/>:<button data-open-subagents onClick={()=>setVisible(true)}>Open subagents</button>}</div>
    </main>}
  document.documentElement.classList.remove('dark');
  i18n.use(initReactI18next).init({lng:'en-US',resources:{'en-US':{translation:messages},'zh-CN':{translation:chinese}},interpolation:{escapeValue:false}}).then(()=>createRoot(document.getElementById('root')).render(<Fixture/>));
` }, bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic", plugins });
const cssBuild = await buildCss({ root, logLevel: "silent", build: { write: false, emptyOutDir: false, copyPublicDir: false,
  rollupOptions: { input: resolve(root, "src/styles.css") } } });
const cssOutput = Array.isArray(cssBuild) ? cssBuild[0].output : cssBuild.output;
const css = Buffer.from(cssOutput.find(item => item.type === "asset" && item.fileName.endsWith(".css")).source).toString();
const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  if (url.pathname === "/") { res.writeHead(200,{"Content-Type":"text/html; charset=utf-8"});res.end(`<style>${css}</style><div id="root"></div><script>${bundled.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script>`);return; }
  requests.push({ method: req.method, path: url.pathname, session: url.searchParams.get("sessionId"), turn: url.searchParams.get("clientTurnId"), after: Number(url.searchParams.get("afterSequence") || 0) });
  if (req.method === "GET" && url.pathname === "/api/app/sub-agents/first") {
    if (failFirstDetail) { failFirstDetail = false; res.writeHead(503); res.end(); return; }
    res.writeHead(200,{"Content-Type":"application/json"});res.end(JSON.stringify({ok:true,task:firstDetails}));return;
  }
  if (url.pathname === "/api/app/agent/runs/timeline") {
    const turn = url.searchParams.get("clientTurnId");
    if (turn === "failed") {
      res.writeHead(503, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ detail: "Fixture timeline backend failure" }));
      return;
    }
    if (turn === "first" && failFirstTimeline) { failFirstTimeline = false; res.writeHead(503); res.end(); return; }
    let events = turn === "first" ? first : turn === "second" ? second : [];
    if (turn === "live") { liveReads++;events = [event(0,"assistant","Inspecting the sleeve."),...(liveReads>1?[event(1,"assistant","Fresh running update arrived.",{phase:"final_answer",status:"completed"})]:[])]; }
    if (turn === "third") events = [event(0,"assistant","Third turn retains the same child context.",{phase:"final_answer",status:"completed"})];
    const after=Number(url.searchParams.get("afterSequence")||0),remaining=events.filter(e=>e.sequence>=after),page=remaining.slice(0,200);
    const mismatchedRun = turn === "live" && liveReads === 1;
    const run = {sessionId:mismatchedRun ? "unrelated-session" : url.searchParams.get("sessionId"),clientTurnId:turn,
      model:turn === "live" ? (mismatchedRun ? "unrelated-model" : "effective-child-model") : undefined};
    res.writeHead(200,{"Content-Type":"application/json"});res.end(JSON.stringify({ok:true,run,events:page,nextSequence:page.length?page.at(-1).sequence+1:after,hasMore:remaining.length>page.length}));return;
  }
  if (req.method === "POST" && url.pathname === "/api/app/sub-agents/second/resume") {
    let body="";for await(const chunk of req)body+=chunk;
    failFirstTimeline = failFirstDetail = true;
    const data=JSON.parse(body);res.writeHead(200,{"Content-Type":"application/json"});res.end(JSON.stringify({ok:true,task:{...tasks[1],id:"third",resumeOf:"second",task:data.task,status:"running",canResume:false,createdAt:"2026-10-28T10:04:00Z"}}));return;
  }
  res.writeHead(404);res.end();
});
let browser;
try {
  await new Promise(done=>server.listen(0,"127.0.0.1",done));
  browser=await chromium.launch({channel:"msedge",headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000},colorScheme:"light"});
  page.setDefaultTimeout(7000);const errors=[];page.on("pageerror",e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.waitForFunction(()=>typeof window.onlyFailed==="function");
  await page.locator("[data-main-composer] textarea").fill("Keep this parent draft");
  await page.locator('[data-vrcforge-stop-subagent="child-c"]').click();
  assert.deepEqual(await page.evaluate(()=>window.cancelled),['live']);
  assert.equal(await page.locator('[data-vrcforge-subagent-workspace-surface]').count(),0,"Stop is separate from opening a conversation");
  await page.evaluate(()=>window.manyDone());
  await page.locator('[data-vrcforge-subagent-show-more="done"]').waitFor();
  assert.equal(await page.locator('[data-vrcforge-open-subagent-thread]').count(),10);
  await page.locator('[data-vrcforge-subagent-show-more="done"]').click();
  assert.equal(await page.locator('[data-vrcforge-open-subagent-thread]').count(),24);
  await page.locator('[data-vrcforge-open-subagent-thread="history-child-20"]').click();
  await page.locator('[data-vrcforge-subagent-back]').click();
  await page.locator('[data-vrcforge-subagent-sidebar-list]').waitFor();
  assert.equal(await page.locator('[data-vrcforge-open-subagent-thread]').count(),24,"Back preserves expanded history rather than resetting to ten rows");
  await page.evaluate(()=>window.onlyFailed());
  const failedThread = page.locator('[data-vrcforge-open-subagent-thread="child-b"]');
  assert.equal(await failedThread.count(),1,"failed-only child must remain reachable in sidebar");
  await failedThread.getByText("The sub-agent couldn’t finish this task. Try running it again.").waitFor();
  assert.doesNotMatch(await failedThread.innerText(), /OSError|fixture-internal-path/);
  await failedThread.click();
  const surface=page.locator('[data-vrcforge-subagent-workspace-surface]');
  assert.equal(await surface.locator("[data-subagent-model]").innerText(), "Model: Not recorded");
  assert.doesNotMatch(await surface.innerText(), /unverified-route/);
  await surface.getByText("The sub-agent couldn’t finish this task. Try running it again.",{exact:true}).waitFor();
  await surface.getByText("The source didn’t include a test scene. I checked the material list.", {exact:true}).waitFor();
  await surface.getByText("Couldn’t load the conversation history. Refresh to try again.", {exact:true}).waitFor();
  assert.doesNotMatch(await surface.innerText(), /OSError|fixture-internal-path|Fixture timeline backend failure/);
  await page.evaluate(() => window.setDeveloper(true));
  await surface.locator("[data-subagent-technical-details]").click();
  await surface.getByText("OSError: invalid handle at fixture-internal-path", {exact:false}).waitFor();
  await surface.getByText("Fixture timeline backend failure", {exact:false}).waitFor();
  await page.evaluate(() => window.setDeveloper(false));
  await surface.locator('[data-vrcforge-subagent-back]').click();
  await page.locator('[data-vrcforge-subagent-sidebar-list]').waitFor();
  assert.equal(await page.locator("[data-main-composer] textarea").inputValue(),"Keep this parent draft","back must preserve parent draft");
  await page.evaluate(()=>window.resetTasks());
  await page.locator('[data-vrcforge-open-subagent-thread="fixed"]').click();
  await surface.getByText("Recorded package summary.", { exact: true }).waitFor();
  assert.equal(await surface.getByText("Recorded package summary.", { exact: true }).count(), 1);
  assert.equal(await surface.locator("details").count(), 0, "default child view has no native raw result disclosure");
  const technicalDetails = surface.locator("[data-subagent-technical-details]");
  assert.equal(await technicalDetails.count(), 0);
  await page.evaluate(() => window.setDeveloper(true));
  await technicalDetails.waitFor();
  assert.equal(await technicalDetails.count(), 1, "result and event history share one entry");
  assert.equal(await technicalDetails.getAttribute("aria-expanded"), "false");
  assert.equal(await surface.getByText('"packageKind": "unitypackage"', { exact: false }).count(), 0);
  await technicalDetails.click();
  await surface.getByText('"packageKind": "unitypackage"', { exact: false }).waitFor();
  await surface.getByText("fixture-completed", { exact: false }).waitFor();
  await page.evaluate(() => window.setDeveloper(false));
  await technicalDetails.waitFor({ state: "detached" });
  assert.equal(await surface.getByText("fixture-completed", { exact: false }).count(), 0);
  assert.equal(await surface.getByRole("button", { name: "Ask a follow-up", exact: true }).count(), 0);
  assert.equal(await surface.locator("[data-subagent-model]").innerText(), "Model: No model used");
  assert.equal(await surface.locator("[data-subagent-parent-model]").innerText(), "Main conversation model: original-parent-model");
  assert.match(await surface.locator("[data-subagent-duration]").innerText(), /3s/);
  assert.equal(await surface.locator("[data-agent-turn-metadata]").count(), 0);
  await surface.locator("header [data-subagent-copy-reply]").click();
  assert.equal(await page.evaluate(() => window.copiedReply), "Recorded package summary.");
  assert.equal(await surface.getByRole("button", {name: "Retry as new task", exact: true}).count(), 0);
  assert.equal(await surface.getByRole("button", {name: "Use as input", exact: true}).count(), 0);
  assert.equal(requests.some(request => request.turn === "fixed"), false);
  await surface.locator('[data-vrcforge-subagent-back]').click();
  if (recordedInspection) {
    const { id, task, startedAt, stoppedAt, result: { inspection } } = recordedInspection;
    await page.locator(`[data-vrcforge-open-subagent-thread="${recordedInspection.childSessionId || id}"]`).click();
    if (recordedInspection.parentModel) {
      assert.equal(await surface.locator("[data-subagent-model]").innerText(), "Model: No model used");
      assert.equal(await surface.locator("[data-subagent-parent-model]").innerText(),
        `Main conversation model: ${recordedInspection.parentModel}`);
    }
    const card = surface.locator(`[data-vrcforge-agent-client-turn="${id}"]`);
    const summary = inspection.summary;
    const answer = `Found ${summary.unityPackageCount} Unity packages, ${summary.prefabCandidateCount} prefab candidates, and ${summary.textureCount} textures.`;
    const process = card.locator("[data-agent-turn-process-group]");
    const toggle = process.getByRole("button").first();
    await card.getByText(answer, { exact: true }).waitFor();
    const collapsedText = await card.innerText();
    const elapsedTitle = await process.locator("[data-agent-turn-process-title]").innerText();
    assert.match(elapsedTitle, new RegExp(`${Math.floor((Date.parse(stoppedAt) - Date.parse(startedAt)) / 1000)}s`));
    assert(collapsedText.indexOf(task) < collapsedText.indexOf(elapsedTitle) && collapsedText.indexOf(elapsedTitle) < collapsedText.indexOf(answer));
    assert.equal(await toggle.getAttribute("aria-expanded"), "false");
    assert.equal(await card.locator("[data-subagent-inspection-activity]").count(), 0);
    assert.equal(await card.getByText(answer, { exact: true }).count(), 1);
    if (summary.truncated || summary.nestedPathnamesTruncated) {
      await card.getByText("The scan covered only part of the source. The counts reflect the files it found.", { exact: true }).waitFor();
    }
    assert.equal(await card.locator("[data-agent-turn-metadata]").count(), 0);
    assert.equal(await card.locator("[data-chat-message-actions]").count(), 1, "only the user's timestamp remains in the thread");
    assert.equal(await surface.locator("header [data-subagent-duration]").count(), 0, "elapsed appears once in the shared process disclosure");
    assert.equal(await surface.locator("header [data-subagent-copy-reply]").count(), 1);
    await surface.locator("header [data-subagent-copy-reply]").click();
    assert.match(await page.evaluate(() => window.copiedReply), new RegExp(`${summary.unityPackageCount} Unity packages`));
    await toggle.click();
    const activity = process.locator("[data-subagent-inspection-activity]");
    assert.equal(await activity.locator("[data-subagent-inspection-evidence]").count(), 0);
    await activity.getByRole("button", { name: "Scan asset files" }).click();
    await activity.locator("[data-subagent-inspection-source]").getByText(inspection.source.name, { exact: true }).waitFor();
    const packages = activity.locator("summary").filter({ hasText: /^Unity packages/ });
    await packages.click();
    await activity.locator("summary").getByText(inspection.unityPackages[0].path, { exact: true }).waitFor();
    assert.equal(await card.locator("pre").count(), 0, "the real inspection receipt is not rendered as JSON");
    assert.equal(await surface.locator("[data-subagent-technical-details]").count(), 0);
    if (evidence) {
      await page.evaluate(() => window.setLanguage("zh-CN"));
      await card.getByText(`共找到 ${summary.unityPackageCount} 个 Unity 包、${summary.prefabCandidateCount} 个预制体候选和 ${summary.textureCount} 张贴图。`, { exact: true }).waitFor();
      await surface.screenshot({ path: resolve(evidence, "inspection-evidence.png") });
      await toggle.click();
      await surface.screenshot({ path: resolve(evidence, "inspection-collapsed.png") });
      await page.evaluate(() => window.setLanguage("en-US"));
    }
    await surface.locator('[data-vrcforge-subagent-back]').click();
  }
  if (realModelTask) {
    await page.locator(`[data-vrcforge-open-subagent-thread="${realModelTask.childSessionId}"]`).click();
    assert.equal(await surface.locator("header [data-subagent-model]").innerText(), `Model: ${realModelTask.result.rawResult.model}`);
    await surface.locator('[data-vrcforge-subagent-back]').click();
  }
  assert.equal(await page.locator('[data-vrcforge-open-subagent-thread="child-a"]').count(),1,"resume turns must share one sidebar row");
  await page.locator('[data-vrcforge-open-subagent-thread="child-a"]').click();
  await surface.getByText("Full original report beyond page 200.",{exact:true}).waitFor();
  await surface.getByText("Full second-turn report.",{exact:true}).waitFor();
  const parentCard = page.locator("[data-parent-parity] [data-vrcforge-agent-client-turn]");
  const childCard = surface.locator('[data-vrcforge-agent-client-turn="first"]');
  const openButton = root => root.locator(":scope > div > button[aria-expanded]").first();
  const getProcessButton = card => openButton(card.locator("[data-agent-turn-process-group]"));
  assert.equal(await childCard.locator("[data-user-message-bubble]").innerText(), await parentCard.locator("[data-user-message-bubble]").innerText());
  assert.equal(await surface.locator("[data-subagent-model]").innerText(), "Model: fixture-reported-model");
  for (const card of [parentCard, childCard]) {
    const group = getProcessButton(card);
    assert.equal(await group.getAttribute("aria-expanded"), "false");
    await group.click();
    assert.equal(await group.getAttribute("aria-expanded"), "true");
    const toolGroups = card.locator("[data-agent-tool-call-group]");
    assert.equal(await toolGroups.count(), 2);
    for (const toolGroup of await toolGroups.all()) {
      await openButton(toolGroup).click();
      await toolGroup.locator('button[aria-expanded="false"]').last().click();
      await toolGroup.locator("[data-agent-invocation-output-card]").waitFor();
    }
  }
  function contentOrder(text) {
    const expected = ["Inspecting the original wardrobe.", '"layers": 3', "Checking the command output.",
      "Get-Content layers.txt", "SHELL-OUTPUT-FIXTURE", "Full original report beyond page 200."];
    const order = expected.map(token => text.indexOf(token));
    assert(order.every((index, i) => index >= 0 && (i === 0 || index > order[i - 1])), JSON.stringify({ expected, order, text }));
  }
  const parentText = await parentCard.innerText();
  const childText = await childCard.innerText();
  contentOrder(parentText);
  contentOrder(childText);
  const nativeInspection = surface.locator('[data-vrcforge-agent-client-turn="second"]');
  await getProcessButton(nativeInspection).click();
  const inspectionGroup = nativeInspection.locator("[data-agent-tool-call-group]");
  await openButton(inspectionGroup).click();
  await inspectionGroup.getByRole("button", { name: "Scan asset files", exact: true }).last().click();
  const nativeOutput = inspectionGroup.locator("[data-agent-invocation-output-card]");
  await nativeOutput.getByText(inspectionOutput.source.name, { exact: true }).waitFor();
  assert.equal(await nativeOutput.locator("pre").count(), 0, "the native tool shows inventory evidence without raw diagnostics");
  assert.equal(await nativeInspection.getByText("Full second-turn report.", { exact: true }).count(), 1);
  assert(requests.some(request => request.path === "/api/app/sub-agents/first"), "prior turn details are read when the list omits its result");
  for(const mode of ['light','dark','wallpaper']) {
    await page.evaluate(mode=>{
      document.documentElement.dataset.theme=mode==='dark'?'dark':'light';
      if(mode==='wallpaper')document.documentElement.dataset.vrcforgeWallpaper='active';else delete document.documentElement.dataset.vrcforgeWallpaper;
    },mode);
    await page.waitForFunction(()=>getComputedStyle(document.querySelector('[data-main-composer] .chat-composer-frame')).backgroundColor===getComputedStyle(document.querySelector('[data-vrcforge-subagent-resume-question]').closest('.chat-composer-frame')).backgroundColor);
    const colors=await page.evaluate(()=>{
      const read=node=>{const c=getComputedStyle(node);return {background:c.backgroundColor,font:c.fontSize}};
      return {main:read(document.querySelector('[data-main-composer] .chat-composer-shell')),child:read(document.querySelector('[data-vrcforge-subagent-resume-question]').closest('form')),
        mainFrame:read(document.querySelector('[data-main-composer] .chat-composer-frame')),childFrame:read(document.querySelector('[data-vrcforge-subagent-resume-question]').closest('.chat-composer-frame')),
        mainInput:read(document.querySelector('[data-main-composer] textarea')),childInput:read(document.querySelector('[data-vrcforge-subagent-resume-question]'))};
    });
    assert.equal(colors.main.background,colors.child.background,mode+' composer shell colors must match');
    assert.equal(colors.mainFrame.background,colors.childFrame.background,mode+' editor frame colors must match');
    assert.equal(colors.mainInput.background,colors.childInput.background,mode+' textarea colors must match');
    assert.equal(colors.mainInput.font,colors.childInput.font,mode+' textarea font sizes must match');
  }
  await page.evaluate(()=>{document.documentElement.dataset.theme='light';delete document.documentElement.dataset.vrcforgeWallpaper});
  assert.equal(await surface.locator('[data-subagent-user-message]').count(),2);
  assert(requests.some(r=>r.turn==="first"&&r.after===200),"history must read the second page");
  assert.equal(requests.filter(r=>r.method==="POST").length,0,"opening and reading must never execute a turn");
  assert.equal(await page.locator("[data-main-composer] textarea").inputValue(),"Keep this parent draft","opening child must preserve parent draft");
  const question="Continue with the third outfit.";
  await surface.getByLabel("New question for this child").fill(question);
  await surface.getByRole("button",{name:"Ask a follow-up",exact:true}).click();
  await surface.getByText("Third turn retains the same child context.",{exact:true}).waitFor();
  assert.equal(await surface.locator('[data-subagent-user-message]').count(),3);
  await surface.getByText("Full original report beyond page 200.", { exact: true }).waitFor();
  assert(requests.slice(requests.findIndex(request => request.method === "POST")).filter(request => request.path === "/api/app/sub-agents/first").length >= 2,
    "the existing live poll retries failed older timeline and result reads without dropping the successful new turn");
  await surface.locator('[data-vrcforge-subagent-back]').click();
  await page.locator('[data-vrcforge-subagent-sidebar-list]').waitFor();
  assert.equal(await page.locator('[data-vrcforge-open-subagent-thread="child-a"]').count(),1);
  await page.locator('[data-vrcforge-open-subagent-thread="child-c"]').click();
  await surface.getByText("Inspecting the sleeve.", { exact: true }).waitFor();
  assert.equal(await surface.locator("[data-subagent-model]").innerText(), "Model: Pending");
  await surface.getByText("Fresh running update arrived.",{exact:true}).waitFor();
  await surface.getByText("Model: effective-child-model", { exact: true }).waitFor();
  assert(liveReads>=2,"running conversation must refresh without reopening");
  await surface.locator('[data-vrcforge-subagent-back]').click();
  await page.locator('[data-vrcforge-subagent-sidebar-list]').waitFor();
  await page.locator('[data-vrcforge-close-subagent-sidebar]').click();
  await page.locator('[data-open-subagents]').waitFor();
  assert.equal(await page.locator("[data-main-composer] textarea").inputValue(),"Keep this parent draft","closing right pane must preserve parent draft");
  assert.equal(requests.filter(r=>r.method==="POST").length,1);
  assert.deepEqual(errors,[]);
  await page.evaluate(()=>{window.resetTasks();return window.setLanguage('zh-CN')});
  await page.locator('[data-open-subagents]').click();
  await page.locator('[data-vrcforge-subagent-sidebar-list]').waitFor();
  if (evidence) await page.screenshot({path:resolve(evidence,"subagent-list-light.png"),fullPage:true});
  if (recordedInspection?.parentModel) {
    await page.locator(`[data-vrcforge-open-subagent-thread="${recordedInspection.childSessionId || recordedInspection.id}"]`).click();
    await surface.getByText("模型：未使用模型", { exact: true }).waitFor();
    await surface.getByText(`主对话模型：${recordedInspection.parentModel}`, { exact: true }).waitFor();
    if (evidence) await page.screenshot({path:resolve(evidence,"rc6-original-model-identity.png"),fullPage:true});
    await surface.locator('[data-vrcforge-subagent-back]').click();
  }
  await page.locator('[data-vrcforge-open-subagent-thread="child-a"]').click();
  await surface.getByText("Full second-turn report.",{exact:true}).waitFor();
  if (evidence) await page.screenshot({path:resolve(evidence,"subagent-conversation-light.png"),fullPage:true});
  console.log("PASS: main and child share ordered conversation cards, expandable tool and shell output, elapsed and final reply; parent draft, fixed role, older sidecar, durable paging and follow-up remain available.");
} finally {await browser?.close();server.closeAllConnections();await new Promise(done=>server.close(done));}
