import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { build } from "esbuild";
import postcss from "postcss";
import tailwind from "tailwindcss";
import {execFileSync} from "node:child_process";
import {readFileSync} from "node:fs";
const css=(await postcss([tailwind("tailwind.config.ts")]).process("@tailwind base; @tailwind utilities;",{from:undefined})).css;

const root = process.cwd();
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundled = await build({
  stdin: { resolveDir: root, loader: "tsx", contents: `
    import React, {useState} from 'react';
    import {createRoot} from 'react-dom/client';
    import {ChatWorkspace} from './src/components/chat/chat-workspace';
    window.queueCalls=[];
    function Fixture(){
      const pending={id:'queued-turn-1',text:'待发送提示',attachments:[{id:'queued-image',name:'model.png',size:9,type:'image/png',dataUrl:'data:image/png;base64,AA=='}],queueStatus:'queued',composerQueued:true};
      const [queued,setQueued]=useState([pending]);
      const [sent,setSent]=useState(false);
      window.queueState=queued;
      window.setQueueText=text=>setQueued(current=>current.map(turn=>({...turn,text})));
      window.setQueuePhase=queueTransition=>setQueued(current=>current.map(turn=>({...turn,queueTransition})));
      window.markSent=()=>{setQueued([]);setSent(true);};
      window.restoreQueue=()=>setQueued([pending]);
      window.stackQueue=()=>setQueued(Array.from({length:7},(_,index)=>({...pending,id:'stack-'+index,text:'Message '+(index+1)})));
      const [input,setInput]=useState('');
      const edit=id=>{const turn=queued.find(turn=>turn.id===id);window.queueCalls.push({kind:'edit',id,attachments:turn.attachments});setInput(turn.text);setQueued(current=>current.filter(turn=>turn.id!==id));};
      const remove=id=>{window.queueCalls.push({kind:'remove',id});setQueued(current=>current.filter(turn=>turn.id!==id));};
      const steer=id=>window.queueCalls.push({kind:'steer',id});
      return <ChatWorkspace input={input} setInput={setInput} sending={true} queueAllowed={true} onSubmit={()=>{}} onQueue={()=>{}} onSteerNow={()=>{}} onSwitchMode={()=>{}}
        conversation={[{id:'old',type:'user',text:'已发送历史'},{id:'queued-user',type:'user',text:'待发送提示',queuedFrom:!sent,queueStatus:sent?undefined:'queued'}]}
        queued={queued} onEditQueuedTurn={edit} onRemoveQueuedTurn={remove} onSteerQueuedTurn={steer}
        agentQuestions={[]} runtimeRuns={[]} scopedPendingApprovals={[]} approvalActions={{}} activeGoal={null}
        attachments={[]} commands={[]} actions={[]} projects={[]} backgroundGoalDeliveries={[]} backgroundGoalProviderWarnings={[]}
        pendingApprovalForResponse={()=>null} onAnswerQuestion={()=>{}} onStopQuestionContinuation={()=>{}}
        onConversationMouseUp={()=>{}} onConversationScroll={()=>{}} onCopyItem={()=>{}} onRetryItem={()=>{}} />;
    }
    createRoot(document.getElementById('root')).render(<Fixture/>);
  ` },
  bundle: true,
  write: false,
  format: "iife",
  platform: "browser",
  jsx: "automatic",
  plugins: [{
    name: "pending-queue-stubs",
    setup(plugin) {
      if(process.env.VRCFORGE_QUEUE_BASELINE) plugin.onLoad({filter:/chat-workspace\.tsx$/},()=>({loader:"tsx",contents:execFileSync("git",["show",`${process.env.VRCFORGE_QUEUE_BASELINE}:src/components/chat/chat-workspace.tsx`],{encoding:"utf8"})}));
      const mocks = {
        "./composer": 'export const Composer=(props)=>React.createElement("textarea",{"data-composer":true,"aria-label":"composer",value:props.input,onChange:()=>{}});',
        "./background-goal-catch-up-card": "export const BackgroundGoalCatchUpCard=()=>null;",
        "./conversation-card": 'export const MessageDateSeparator=()=>null,StreamingPhaseStatus=()=>null;export const ConversationCard=({item})=>React.createElement("p",{"data-history-item":item?.id},item?.text||"history item");',
        "./session-handoff-card": "export const SessionHandoffCard=()=>null;",
        "./session-handoff-send": "export const SessionHandoffSend=()=>null;",
        "../approvals/scoped-pending-approval-card": "export const ScopedPendingApprovalCard=()=>null;",
        "../ui/button": 'export const Button=({children,...props})=>React.createElement("button",props,children);',
        "../../lib/api": "export const updateAgentGoal=async()=>({});",
        "../../lib/path-to-skill-context": "export const matchPathToSkillRuntimeOperation=()=>null;",
        "../../lib/chat-thread": "export const mergeConversationTimelineItems=x=>x;",
      };
      plugin.onResolve({filter:/^react-i18next$/},()=>({path:"translation",namespace:"mock"}));
      plugin.onResolve({filter:/^lucide-react$/},()=>({path:"icons",namespace:"mock"}));
      plugin.onLoad({filter:/.*/,namespace:"mock"},args=>({resolveDir:root,contents:args.path==="icons"
        ? 'import React from "react";export const HelpCircle=()=>null,Pencil=()=>null;export const AlertTriangle=()=>null,ArrowDown=()=>null,ArrowUp=()=>null,ChevronLeft=()=>null,ChevronRight=()=>null,ListEnd=()=>null,Loader2=(props)=>React.createElement("span",props),Minus=()=>null,MoreHorizontal=()=>null,Paperclip=()=>null,Pause=()=>null,Play=()=>null,Plus=()=>null,X=()=>null;'
        : 'export const useTranslation=()=>({t:key=>key});'}));
      plugin.onResolve({filter:/.*/},args=>args.importer.endsWith("chat-workspace.tsx")&&mocks[args.path]
        ? {path:args.path,namespace:"pending-queue"}:undefined);
      plugin.onLoad({filter:/.*/,namespace:"pending-queue"},args=>({contents:`import React from 'react';${mocks[args.path]}`,resolveDir:root}));
    },
  }],
});

const browser = await chromium.launch({ channel: "msedge", headless: true });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setContent('<div id="root"></div>');
  await page.addStyleTag({content:css+readFileSync("src/theme.css","utf8")});
  await page.addScriptTag({ content: bundled.outputFiles[0].text });
  const row = page.locator('[data-chat-pending-turn="queued-turn-1"]');
  await row.waitFor();
  assert.doesNotMatch(await page.locator("[data-chat-pending-queue]").innerText(), /chat\.(queued|pendingQueue)/);
  assert.equal(await page.locator("[data-chat-history-scroll] [data-history-item='queued-user']").count(), 0, "pending queued user input is hidden from sent history");
  assert.equal(await page.locator("[data-chat-history-scroll] [data-history-item='old']").innerText(), "已发送历史", "ordinary sent history remains visible");
  assert.equal(await row.locator("[data-chat-pending-turn-preview]").innerText(), "待发送提示", "queued message appears as a compact preview above composer");
  assert.equal(await row.locator("[data-chat-pending-turn-attachments] img").getAttribute("alt"), "model.png");
  for (const phase of ["enqueue", "dispatch", undefined]) {
    await page.evaluate((value) => window.setQueuePhase(value), phase);
    await page.waitForFunction((value)=>window.queueState[0].queueTransition===value, phase);
    assert.equal(await row.locator(".animate-spin").count(),0,"queued work never shows an activity spinner");
  }
  for (const width of [1100, 700]) {
    await page.setViewportSize({width,height:900});
    await page.evaluate(()=>window.setQueueText(('很长的引导消息\n').repeat(300)));
    await page.waitForFunction(()=>document.querySelector('[data-chat-pending-turn-preview]').textContent.length>1000);
    const rect=await row.boundingBox();
    assert.ok(rect.height<=48,'long multiline messages stay in one compact row');
    const preview=await row.locator('[data-chat-pending-turn-preview]').evaluate(el=>({whiteSpace:getComputedStyle(el).whiteSpace,overflow:el.scrollWidth>el.clientWidth}));
    assert.equal(preview.whiteSpace,'nowrap'); assert.equal(preview.overflow,true);
  }
  await page.evaluate(()=>window.setQueueText('待发送提示'));
  assert.equal(await row.locator("[data-chat-pending-turn-steer]").getAttribute("aria-label"), "chat.sendQueuedNow");
  await row.locator("[data-chat-pending-turn-steer]").click();

  await row.locator("[data-chat-pending-turn-edit]").click();
  assert.equal(await page.locator('[data-composer]').inputValue(), '待发送提示', 'edit returns the complete message to the main composer');
  assert.equal(await row.count(),0,'editing withdraws the queued row instead of sending again');
  await page.evaluate(()=>window.restoreQueue());
  await row.locator("[data-chat-pending-turn-remove]").click();
  assert.equal(await row.count(), 0, "cancel removes the pending row");
  assert.deepEqual(await page.evaluate(()=>window.queueCalls), [
    {kind:'steer',id:'queued-turn-1'},
    {kind:'edit',id:'queued-turn-1',attachments:[{id:'queued-image',name:'model.png',size:9,type:'image/png',dataUrl:'data:image/png;base64,AA=='}]},
    {kind:'remove',id:'queued-turn-1'},
  ]);
  await page.evaluate(()=>window.stackQueue());
  const scroll = page.locator("[data-chat-pending-queue-scroll]");
  await page.waitForFunction(()=>document.querySelector("[data-chat-pending-queue-scroll]")?.scrollTop>0);
  assert.equal(await scroll.locator("[data-chat-pending-turn]").count(), 7);
  const stack = await scroll.evaluate((el) => ({
    height: el.clientHeight,
    contentHeight: el.scrollHeight,
    rowHeight: el.firstElementChild.getBoundingClientRect().height,
    scrollTop: el.scrollTop,
  }));
  assert.equal(stack.height, stack.rowHeight * 5, "only five queued rows fit in the scroll region");
  assert.equal(stack.scrollTop, stack.contentHeight - stack.height, "new rows reveal the end of the queue");
  const composerBox = await page.locator("[data-composer]").boundingBox();
  await scroll.hover();
  await page.mouse.wheel(0, -300);
  await page.waitForFunction(()=>document.querySelector("[data-chat-pending-queue-scroll]")?.scrollTop===0);
  assert.equal((await page.locator("[data-composer]").boundingBox()).y, composerBox.y, "wheel only scrolls queued messages");
  await page.evaluate(()=>window.markSent());
  await page.locator("[data-chat-history-scroll] [data-history-item='queued-user']").waitFor();
  assert.equal(await page.locator("[data-chat-pending-queue]").count(),0,"the queue row yields to one sent user message");
  assert.deepEqual(errors, []);
  console.log("chat pending queue: hidden from history, editable, steerable, and removable");
} finally {
  await browser.close();
}
