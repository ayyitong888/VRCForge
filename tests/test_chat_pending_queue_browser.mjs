import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { build } from "esbuild";

const root = process.cwd();
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundled = await build({
  stdin: { resolveDir: root, loader: "tsx", contents: `
    import React, {useState} from 'react';
    import {createRoot} from 'react-dom/client';
    import {ChatWorkspace} from './src/components/chat/chat-workspace';
    window.queueCalls=[];
    function Fixture(){
      const [queued,setQueued]=useState([{id:'queued-turn-1',text:'待发送提示',attachments:[{id:'queued-image',name:'model.png',size:9,type:'image/png'}],queueStatus:'queued',composerQueued:true}]);
      window.queueState=queued;
      const edit=(id,patch)=>{window.queueCalls.push({kind:'edit',id,patch});setQueued(current=>current.map(turn=>turn.id===id?{...turn,...patch}:turn));};
      const remove=id=>{window.queueCalls.push({kind:'remove',id});setQueued(current=>current.filter(turn=>turn.id!==id));};
      const steer=id=>window.queueCalls.push({kind:'steer',id});
      return <ChatWorkspace input='' setInput={()=>{}} sending={true} queueAllowed={true} onSubmit={()=>{}} onQueue={()=>{}} onSteerNow={()=>{}} onSwitchMode={()=>{}}
        conversation={[{id:'old',type:'user',text:'已发送历史'},{id:'queued-user',type:'user',text:'待发送提示',composerQueued:true}]}
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
      const mocks = {
        "./composer": 'export const Composer=(props)=>React.createElement("textarea",{"data-composer":true,"aria-label":"composer",value:props.input,onChange:()=>{}});',
        "./background-goal-catch-up-card": "export const BackgroundGoalCatchUpCard=()=>null;",
        "./conversation-card": 'export const ConversationCard=({item})=>React.createElement("p",{"data-history-item":item?.id},item?.text||"history item");',
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
      plugin.onLoad({filter:/.*/,namespace:"mock"},args=>({contents:args.path==="icons"
        ? 'export const AlertTriangle=()=>null,ArrowDown=()=>null,ArrowUp=()=>null,ChevronLeft=()=>null,ChevronRight=()=>null,Loader2=()=>null,MoreHorizontal=()=>null,Pause=()=>null,Play=()=>null,X=()=>null;'
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
  await page.addScriptTag({ content: bundled.outputFiles[0].text });
  const row = page.locator('[data-chat-pending-turn="queued-turn-1"]');
  await row.waitFor();
  assert.equal(await page.locator("[data-chat-history-scroll] [data-history-item='queued-user']").count(), 0, "pending queued user input is hidden from sent history");
  assert.equal(await page.locator("[data-chat-history-scroll] [data-history-item='old']").innerText(), "已发送历史", "ordinary sent history remains visible");
  assert.equal(await row.locator("[data-chat-pending-turn-preview]").innerText(), "待发送提示", "queued message appears as a compact preview above composer");
  await row.locator("[data-chat-pending-turn-more]").click();
  await row.locator("[data-chat-pending-turn-edit]").click();
  assert.equal(await row.locator("textarea").inputValue(), "待发送提示", "edit message is available from the more menu");
  await row.locator("textarea").fill("修订后的提示");
  await row.locator("[data-chat-pending-turn-save]").click();
  await page.waitForFunction(() => window.queueCalls.some(call => call.kind === "edit"));
  assert.deepEqual(await page.evaluate(() => window.queueCalls[0]), {kind:"edit",id:"queued-turn-1",patch:{text:"修订后的提示"}});
  await row.getByRole("button", {name:"attachments.remove"}).click();
  await page.waitForFunction(() => window.queueCalls.filter(call => call.kind === "edit").length === 2);
  assert.deepEqual(await page.evaluate(() => window.queueCalls[1]), {kind:"edit",id:"queued-turn-1",patch:{text:"修订后的提示",attachments:[]}});
  await row.locator("[data-chat-pending-turn-steer]").click();
  await page.waitForFunction(() => window.queueCalls.some(call => call.kind === "steer"));
  await row.locator("[data-chat-pending-turn-remove]").click();
  await page.waitForFunction(() => window.queueCalls.some(call => call.kind === "remove"));
  assert.equal(await page.locator("[data-chat-pending-turn]").count(), 0, "removing a queued message clears the pending queue row");
  assert.deepEqual(errors, []);
  console.log("chat pending queue: hidden from history, editable, steerable, and removable");
} finally {
  await browser.close();
}
