import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdir } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
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
  { id: "failed", childSessionId: "child-b", role: "llm_readonly", displayName: "Failed inspection", task: "Read the unavailable file.", status: "failed", error: "Fixture file is unavailable.", createdAt: "2026-10-28T10:02:00Z" },
  { id: "live", childSessionId: "child-c", role: "llm_readonly", displayName: "Live inspection", task: "Inspect the remaining sleeve.", status: "running", createdAt: "2026-10-28T10:03:00Z" },
];
const event = (sequence, kind, summary, extra = {}) => ({ id: `event-${sequence}`, sequence,
  timestamp: "2026-10-28T10:00:00Z", kind, payload: { summary, ...extra } });
const first = [
  event(0, "assistant", "Inspecting the original wardrobe."),
  event(1, "tool_call", "Inspect FX layers", { tool: "vrc_get_animator_layers", actionId: "inspect-1", status: "running" }),
  event(2, "tool_result", "Found three wardrobe layers.", { tool: "vrc_get_animator_layers", actionId: "inspect-1", status: "success" }),
  ...Array.from({ length: 201 }, (_, i) => event(i + 3, "phase", "Working", { phase: "executing" })),
  event(204, "assistant", "Full original report beyond page 200.", { phase: "final_answer", status: "completed" }),
];
let liveReads = 0;
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
  import {SubAgentSidebar} from './src/components/subagents/sub-agent-sidebar';
  import {resumeSubAgent} from './src/lib/api/sub-agents';
  const initial=${JSON.stringify(tasks)},Sidebar=${baseline ? 'SubAgentPanel' : 'SubAgentSidebar'};
  function Fixture(){ const [tasks,setTasks]=useState(initial),[selected,setSelected]=useState(null),[open,setOpen]=useState(false),[visible,setVisible]=useState(true),[draft,setDraft]=useState('');
    window.onlyFailed=()=>setTasks([initial[2]]);window.resetTasks=()=>setTasks(initial);window.setLanguage=lng=>i18n.changeLanguage(lng);window.manyDone=()=>setTasks(Array.from({length:24},(_,n)=>({...initial[1],id:"history-"+n,childSessionId:"history-child-"+n,displayName:"Completed review "+n,resumeOf:undefined})));window.cancelled=[];
    const noop=()=>{};const choose=id=>{setSelected(tasks.find(t=>t.id===(id||tasks[0]?.id)));setOpen(true);setVisible(true)};
    async function resume(id,question){const p=await resumeSubAgent(location.origin,id,{task:question});setTasks(v=>[...v,p.task]);setSelected(p.task);return true}
    return <main className="grid h-screen bg-background text-foreground" style={{gridTemplateColumns:"minmax(340px,0.6fr) minmax(0,1.4fr)"}}>
      <section className="flex min-h-0 flex-col border-r border-border bg-workspace" data-parent-conversation>
        <header className="h-12 shrink-0 border-b border-border px-4 py-3 text-sm font-medium">Main conversation</header>
        <div className="flex-1 space-y-5 overflow-auto p-5 text-sm"><div className="rounded-2xl bg-muted p-3">Review the three outfits with subagents.</div>
          <p>I'll inspect the wardrobe layers and compare the outfits.</p><p className="text-xs text-muted-foreground">UI preview · sample conversation</p></div>
        <div className="p-4" data-main-composer><Composer input={draft} setInput={setDraft} sending={false} onSubmit={event=>event?.preventDefault()} onSwitchMode={noop} compact /></div>
      </section>
      <div className="min-h-0 overflow-hidden bg-workspace">{visible?<Sidebar endpoint={location.origin} tasks={tasks} selected={selected} roles={[{id:"llm_readonly",title:"Reviewer"}]}
        openThread={open} loading={false} error="" onOpen={choose} onSelect={choose} onCancel={id=>window.cancelled.push(id)} onRetry={noop} onResume={resume}
        onMerge={noop} onIgnoreNotification={noop} onAdoptNextAction={noop} busyTaskIds={new Set()}
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
  if (url.pathname === "/api/app/agent/runs/timeline") {
    const turn = url.searchParams.get("clientTurnId");
    let events = turn === "first" ? first : turn === "second" ? [event(0,"assistant","Full second-turn report.",{phase:"final_answer",status:"completed"})] : [];
    if (turn === "live") { liveReads++;events = [event(0,"assistant","Inspecting the sleeve."),...(liveReads>1?[event(1,"assistant","Fresh running update arrived.",{phase:"final_answer",status:"completed"})]:[])]; }
    if (turn === "third") events = [event(0,"assistant","Third turn retains the same child context.",{phase:"final_answer",status:"completed"})];
    const after=Number(url.searchParams.get("afterSequence")||0),remaining=events.filter(e=>e.sequence>=after),page=remaining.slice(0,200);
    res.writeHead(200,{"Content-Type":"application/json"});res.end(JSON.stringify({ok:true,run:{sessionId:url.searchParams.get("sessionId"),clientTurnId:turn},events:page,nextSequence:page.length?page.at(-1).sequence+1:after,hasMore:remaining.length>page.length}));return;
  }
  if (req.method === "POST" && url.pathname === "/api/app/sub-agents/second/resume") {
    let body="";for await(const chunk of req)body+=chunk;
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
  assert.equal(await page.locator('[data-vrcforge-open-subagent-thread="child-b"]').count(),1,"failed-only child must remain reachable in sidebar");
  await page.locator('[data-vrcforge-open-subagent-thread="child-b"]').click();
  const surface=page.locator('[data-vrcforge-subagent-workspace-surface]');
  await surface.getByText("Fixture file is unavailable.",{exact:true}).waitFor();
  await surface.locator('[data-vrcforge-subagent-back]').click();
  await page.locator('[data-vrcforge-subagent-sidebar-list]').waitFor();
  assert.equal(await page.locator("[data-main-composer] textarea").inputValue(),"Keep this parent draft","back must preserve parent draft");
  await page.evaluate(()=>window.resetTasks());
  assert.equal(await page.locator('[data-vrcforge-open-subagent-thread="child-a"]').count(),1,"resume turns must share one sidebar row");
  await page.locator('[data-vrcforge-open-subagent-thread="child-a"]').click();
  await surface.getByText("Full original report beyond page 200.",{exact:true}).waitFor();
  await surface.getByText("Full second-turn report.",{exact:true}).waitFor();
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
  await surface.locator('[data-vrcforge-subagent-back]').click();
  await page.locator('[data-vrcforge-subagent-sidebar-list]').waitFor();
  assert.equal(await page.locator('[data-vrcforge-open-subagent-thread="child-a"]').count(),1);
  await page.locator('[data-vrcforge-open-subagent-thread="child-c"]').click();
  await surface.getByText("Fresh running update arrived.",{exact:true}).waitFor();
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
  await page.locator('[data-vrcforge-open-subagent-thread="child-a"]').click();
  await surface.getByText("Full second-turn report.",{exact:true}).waitFor();
  if (evidence) await page.screenshot({path:resolve(evidence,"subagent-conversation-light.png"),fullPage:true});
  console.log("PASS: right-pane list/conversation/back/close preserve parent chat and draft; failed-only entry, grouped resume, durable paging, tool conversation, readonly opening, live refresh, follow-up and return to parent; light/dark/wallpaper composer parity.");
} finally {await browser?.close();server.closeAllConnections();await new Promise(done=>server.close(done));}
